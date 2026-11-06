"""Checkout, financial reconciliation and audited order fulfilment."""

from typing import Annotated, Literal
from uuid import UUID
import hmac
import logging
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import (
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from accounts import current, throttle
from infrastructure import PUBLISHED, database
from services import mail
from services.crm import DeliveryUpdate
from services.logistics import event
from services.payments import verify_signature, matches


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estado: Literal[
        "pendiente", "pagada", "en_preparacion", "enviada", "entregada", "cancelada"
    ]


def reserve(app, user: dict, product_id: int, key: UUID) -> dict:
    with database(app) as (_, cur):
        cur.execute(
            """SELECT c.entrega_segura FROM clientes c JOIN customer_sessions s ON s.cliente_id=c.id
                       WHERE c.id=%s AND s.token_hash=%s AND c.activo AND s.expires>extract(epoch FROM now())
                       FOR SHARE OF c,s""",
            (user["id"], user["token_hash"]),
        )
        active = cur.fetchone()
        if not active:
            raise HTTPException(401, "La sesión ya no está activa.")
        shipping = app.state.vault.open(
            f"clientes:{user['id']}", active["entrega_segura"]
        )
        if not all(shipping.get(k) for k in ("direccion", "ciudad", "cp")):
            raise HTTPException(400, "Completa tu dirección de envío antes de comprar.")
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(key),))
        cur.execute("SELECT * FROM ordenes WHERE checkout_key=%s FOR UPDATE", (key,))
        order = cur.fetchone()
        if order:
            if order["cliente_id"] != user["id"] or order["producto_id"] != product_id:
                raise HTTPException(409, "La referencia pertenece a otra compra.")
            if order["estado"] != "pendiente" or order["pago_verificado"]:
                raise HTTPException(409, "Esta compra ya se procesó. Revisa tu cuenta.")
            if order["checkout_url"]:
                return order
            if (
                order["intento_en"]
                and (datetime.now(timezone.utc) - order["intento_en"]).total_seconds()
                < 30
            ):
                raise HTTPException(
                    409, "Se está preparando este pago. Reintenta en unos segundos."
                )
        else:
            cur.execute(
                "SELECT id,nombre,precio_centavos FROM mezcales WHERE id=%s AND "
                + PUBLISHED
                + " AND COALESCE(extra_json::jsonb->>'availability','available')='available' FOR SHARE",
                (product_id,),
            )
            item = cur.fetchone()
            if not item:
                raise HTTPException(404, "Producto no disponible.")
            cur.execute(
                """INSERT INTO ordenes(producto_id,producto_nombre,monto_centavos,cliente_id,creado_en,checkout_key)
                           VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    item["id"],
                    item["nombre"],
                    item["precio_centavos"],
                    user["id"],
                    stamp(),
                    key,
                ),
            )
            order = cur.fetchone()
            delivery = {k: v for k, v in shipping.items() if v is not None}
            envelope = app.state.vault.seal(f"ordenes:{order['id']}", delivery)
            cur.execute(
                "UPDATE ordenes SET entrega_segura=%s WHERE id=%s",
                (envelope, order["id"]),
            )
            event(cur, user["id"], order["id"], "orden.creada", f"cliente:{user['id']}")
        cur.execute("UPDATE ordenes SET intento_en=now() WHERE id=%s", (order["id"],))
        return order


def reconcile(app, payment: dict, payment_id: str) -> int | None:
    external = str(payment.get("external_reference", ""))
    if (
        payment.get("status") != "approved"
        or not external.isascii()
        or not external.isdecimal()
        or len(external) > 10
    ):
        return None
    with database(app) as (_, cur):
        cur.execute("SELECT * FROM ordenes WHERE id=%s FOR UPDATE", (int(external),))
        order = cur.fetchone()
        if not order or not matches(
            payment, order, app.state.settings.collector_id, payment_id
        ):
            logging.getLogger("valerion").warning(
                "pago_sin_conciliar orden=%s pago=%s", external, payment_id
            )
            return None
        if order["pago_id"] and order["pago_id"] != payment_id:
            event(
                cur,
                order["cliente_id"],
                order["id"],
                "pago.requiere_revision",
                "mercadopago",
                "web",
                motivo="otro pago aprobado",
            )
            return None
        cur.execute(
            "INSERT INTO pago_recibos VALUES(%s,%s) ON CONFLICT DO NOTHING RETURNING orden_id",
            (payment_id, order["id"]),
        )
        if not cur.fetchone():
            return None
        before = order["estado"]
        after = "pagada" if before == "pendiente" else before
        cur.execute(
            "UPDATE ordenes SET estado=%s,pago_id=%s,pago_verificado=true,actualizado_en=%s WHERE id=%s",
            (after, payment_id, stamp(), order["id"]),
        )
        event(
            cur,
            order["cliente_id"],
            order["id"],
            "pago.confirmado",
            "mercadopago",
            anterior=before,
            nuevo=after,
        )
        if order["cliente_id"]:
            cur.execute(
                "INSERT INTO correo_pendiente(orden_id) VALUES(%s) ON CONFLICT DO NOTHING",
                (order["id"],),
            )
        return order["id"]


def attach(app, templates, require_admin, require_write, current_session):
    @app.post("/comprar/{product_id}")
    def checkout(
        product_id: int,
        request: Request,
        csrf: Annotated[str | None, Form(max_length=128)] = None,
        checkout_key: Annotated[UUID | None, Form()] = None,
    ):
        user = current(request)
        if not user:
            return RedirectResponse("/cuenta/login", 303)
        if not csrf or not hmac.compare_digest(csrf.encode(), user["csrf"].encode()):
            raise HTTPException(403, "Recarga la ficha del producto para comprar.")
        if not checkout_key:
            raise HTTPException(422, "Recarga la ficha para iniciar la compra.")
        if not app.state.gateway:
            raise HTTPException(503, "Mercado Pago no está configurado.")
        if not all(user.get(k) for k in ("direccion", "cp", "ciudad")):
            raise HTTPException(400, "Completa tu dirección de envío antes de comprar.")
        throttle(app, f"checkout:{user['id']}", 15)
        order = reserve(app, user, product_id, checkout_key)
        if order["checkout_url"]:
            return RedirectResponse(order["checkout_url"], 303)
        origin = app.state.settings.origin
        data = {
            "items": [
                {
                    "id": str(product_id),
                    "title": order["producto_nombre"],
                    "quantity": 1,
                    "unit_price": float(Decimal(order["monto_centavos"]) / 100),
                    "currency_id": "MXN",
                }
            ],
            "back_urls": {
                "success": origin + "/cuenta",
                "failure": f"{origin}/producto/{product_id}?pago=fallido",
                "pending": f"{origin}/producto/{product_id}?pago=pendiente",
            },
            "external_reference": str(order["id"]),
            "metadata": {"checkout_key": str(checkout_key)},
            "notification_url": origin + "/api/pagos/webhook?source_news=webhooks",
            "payer": {"email": user["email"]},
        }
        if origin.startswith("https:"):
            data["auto_return"] = "approved"
        try:
            url, preference_id = app.state.gateway.preference(data, str(checkout_key))
        except HTTPException:
            with database(app) as (_, cur):
                cur.execute(
                    "UPDATE ordenes SET intento_en=NULL WHERE id=%s AND checkout_url IS NULL",
                    (order["id"],),
                )
            raise
        with database(app) as (_, cur):
            cur.execute(
                "UPDATE ordenes SET checkout_url=COALESCE(checkout_url,%s),preferencia_id=COALESCE(preferencia_id,%s) WHERE id=%s RETURNING checkout_url",
                (url, preference_id, order["id"]),
            )
            return RedirectResponse(cur.fetchone()["checkout_url"], 303)

    @app.post("/api/pagos/webhook", include_in_schema=False)
    def webhook(request: Request, background_tasks: BackgroundTasks):
        params = request.query_params
        if not app.state.gateway:
            raise HTTPException(503, "Integración no configurada.")
        payment_id = params.get("data.id", "") or params.get("id", "")
        verify_signature(
            app.state.settings.webhook_secret,
            payment_id,
            request.headers.get("x-request-id", ""),
            request.headers.get("x-signature", ""),
        )
        if (params.get("type") or params.get("topic") or "payment") == "payment":
            order_id = reconcile(app, app.state.gateway.payment(payment_id), payment_id)
            if order_id:
                background_tasks.add_task(mail.deliver, app, order_id)
        return Response(status_code=200)

    @app.put("/api/admin/ordenes/{orden_id}/estado", tags=["Ventas"])
    def update_state(
        orden_id: int, body: OrderState, admin: Annotated[dict, Depends(require_write)]
    ):
        with database(app) as (_, cur):
            cur.execute(
                "SELECT cliente_id,estado FROM ordenes WHERE id=%s FOR UPDATE",
                (orden_id,),
            )
            order = cur.fetchone()
            if not order:
                raise HTTPException(404, "Orden no encontrada.")
            if order["estado"] != body.estado:
                cur.execute(
                    "UPDATE ordenes SET estado=%s,actualizado_en=%s WHERE id=%s",
                    (body.estado, stamp(), orden_id),
                )
                event(
                    cur,
                    order["cliente_id"],
                    orden_id,
                    "estado.modificado",
                    app.state.username,
                    "web",
                    anterior=order["estado"],
                    nuevo=body.estado,
                )
        return {"ok": True, "estado": body.estado}

    @app.put("/api/admin/ordenes/{orden_id}/entrega", tags=["Ventas"])
    def change_delivery(
        orden_id: int,
        body: DeliveryUpdate,
        admin: Annotated[dict, Depends(require_write)],
    ):
        changes = body.model_dump(exclude_unset=True)
        with database(app) as (_, cur):
            cur.execute(
                "SELECT cliente_id,entrega_segura,estado FROM ordenes WHERE id=%s FOR UPDATE",
                (orden_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Orden no encontrada.")
            if row["estado"] in {"entregada", "cancelada"}:
                raise HTTPException(409, "La orden está cerrada.")
            binding = f"ordenes:{orden_id}"
            data = app.state.vault.open(binding, row["entrega_segura"])
            changed = [k for k, v in changes.items() if data.get(k) != v]
            if changed:
                data.update(changes)
                if not all(data.get(k) for k in ("direccion", "ciudad", "cp")):
                    raise HTTPException(
                        422, "Se requiere la dirección completa para corregir un envío."
                    )
                data.pop("direccion_envio", None)
                cur.execute(
                    "UPDATE ordenes SET entrega_segura=%s,actualizado_en=%s WHERE id=%s",
                    (app.state.vault.seal(binding, data), stamp(), orden_id),
                )
                event(
                    cur,
                    row["cliente_id"],
                    orden_id,
                    "orden.entrega_modificada",
                    app.state.username,
                    campos=changed,
                )
        return {"ok": True}

    @app.get("/api/admin/ventas", tags=["Ventas"])
    def sales(
        admin: Annotated[dict, Depends(require_admin)],
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(500, ge=1, le=500),
    ):
        with database(app) as (_, cur):
            cur.execute(
                """SELECT o.id,o.estado,o.monto_centavos,o.pago_id,o.creado_en,o.actualizado_en,
                           o.entrega_segura,COALESCE(o.producto_nombre,m.nombre) producto,c.nombre cliente,c.email
                           FROM ordenes o LEFT JOIN mezcales m ON m.id=o.producto_id LEFT JOIN clientes c ON c.id=o.cliente_id
                           ORDER BY o.id DESC LIMIT %s OFFSET %s""",
                (page_size, (page - 1) * page_size),
            )
            orders = cur.fetchall()
            cur.execute("""SELECT count(*) AS orders,coalesce(sum(monto_centavos) FILTER(WHERE estado<>'cancelada'),0) AS amount_centavos,
                        (SELECT count(*) FROM clientes WHERE activo) AS customers FROM ordenes""")
            metrics = cur.fetchone()
        for order in orders:
            data = app.state.vault.open(
                f"ordenes:{order['id']}", order.pop("entrega_segura")
            )
            order["direccion_envio"] = data.get("direccion_envio") or ", ".join(
                str(data[k])
                for k in (
                    "direccion",
                    "cp",
                    "ciudad",
                    "telefono",
                    "latitud",
                    "longitud",
                )
                if data.get(k) not in (None, "")
            )
        return {
            "orders": orders,
            "metrics": metrics,
            "page": page,
            "page_size": page_size,
        }

    @app.get("/admin/ventas", include_in_schema=False)
    def sales_page(request: Request):
        if not current_session(request):
            return RedirectResponse("/admin/login", 303)
        return templates.TemplateResponse(
            request=request, name="sales.html", context={"username": app.state.username}
        )
