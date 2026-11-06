"""Profiles, customer-owned purchase history and permissioned interaction records."""

from typing import Annotated, Literal
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from infrastructure import audit, database
from services.logistics import FIELDS, event

Channel = Literal["email", "telefono", "whatsapp", "tienda", "web"]


class DeliveryUpdate(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False
    )
    direccion: str | None = Field(None, max_length=400)
    ciudad: str | None = Field(None, max_length=120)
    cp: str | None = Field(None, pattern=r"^([0-9]{5})?$")
    telefono: str | None = Field(None, pattern=r"^(\+?[0-9 ()-]{7,25})?$")
    latitud: float | None = Field(None, ge=-90, le=90)
    longitud: float | None = Field(None, ge=-180, le=180)

    @field_validator("direccion", "ciudad", "telefono")
    @classmethod
    def clean(cls, value: str | None) -> str | None:
        if value and any(not c.isprintable() for c in value):
            raise ValueError("El campo contiene caracteres de control.")
        return value

    @field_validator("telefono")
    @classmethod
    def phone(cls, value: str | None) -> str | None:
        if value and not 7 <= sum(ch.isdigit() for ch in value) <= 15:
            raise ValueError("Revisa el número de teléfono.")
        return value

    @model_validator(mode="after")
    def coordinates(self):
        if ("latitud" in self.model_fields_set) != (
            "longitud" in self.model_fields_set
        ) or (self.latitud is None) != (self.longitud is None):
            raise ValueError("Proporciona o elimina ambas coordenadas.")
        return self


class Interaction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canal: Channel
    tipo: Literal["consulta", "seguimiento", "soporte", "contacto"]


def save_profile(app: FastAPI, customer_id: int, body) -> None:
    with database(app) as (_, cur):
        cur.execute(
            "SELECT * FROM clientes WHERE id=%s AND activo FOR UPDATE", (customer_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(401, "Inicia sesión de nuevo.")
        binding = f"clientes:{customer_id}"
        shipping = app.state.vault.open(binding, row["entrega_segura"])
        changed = []
        for key in FIELDS:
            if key in body.model_fields_set and shipping.get(key) != getattr(body, key):
                shipping[key] = getattr(body, key)
                changed.append(key)
        envelope = (
            app.state.vault.seal(binding, shipping)
            if changed
            else row["entrega_segura"]
        )
        contact = (
            body.preferencia_contacto
            if "preferencia_contacto" in body.model_fields_set
            else row["preferencia_contacto"]
        )
        channel = body.canal_preferido or row["canal_preferido"]
        cur.execute(
            """UPDATE clientes SET nombre=%s,preferencia_contacto=%s,canal_preferido=%s,
                       entrega_segura=%s WHERE id=%s""",
            (body.nombre, contact, channel, envelope, customer_id),
        )
        actor = f"cliente:{customer_id}"
        if changed:
            event(cur, customer_id, None, "entrega.modificada", actor, campos=changed)
        audit(cur, "account.profile", str(customer_id), actor)
        event(cur, customer_id, None, "perfil.actualizado", actor)


def purchase_page(app, cid: int, page: int) -> dict:
    with database(app) as (_, cur):
        cur.execute(
            """SELECT id,producto_id,producto_nombre AS producto,monto_centavos,estado,pago_id,
                       creado_en,actualizado_en,entrega_segura FROM ordenes
                       WHERE cliente_id=%s ORDER BY id DESC LIMIT 21 OFFSET %s""",
            (cid, (page - 1) * 20),
        )
        rows = cur.fetchall()
    result = []
    for row in rows[:20]:
        data = app.state.vault.open(f"ordenes:{row['id']}", row.pop("entrega_segura"))
        result.append({**row, "entrega": data})
    return {"items": result, "page": page, "siguiente": len(rows) > 20}


def attach_crm(app, customer, require_admin, require_write):
    @app.get("/api/clientes/ordenes")
    def my_orders(
        user: Annotated[dict, Depends(customer)], page: int = Query(1, ge=1, le=100000)
    ):
        return purchase_page(app, user["id"], page)

    @app.get("/api/admin/clientes/{cid}/historial")
    def history(
        cid: int,
        admin: Annotated[dict, Depends(require_admin)],
        page: int = Query(1, ge=1, le=100000),
    ):
        with database(app) as (_, cur):
            cur.execute("SELECT id FROM clientes WHERE id=%s", (cid,))
            if not cur.fetchone():
                raise HTTPException(404, "Cliente no encontrado.")
            cur.execute(
                """SELECT id,orden_id,tipo,canal,actor,detalle,fecha FROM cliente_eventos
                           WHERE cliente_id=%s ORDER BY id DESC LIMIT 21 OFFSET %s""",
                (cid, (page - 1) * 20),
            )
            events = cur.fetchall()
        return {
            "compras": purchase_page(app, cid, page),
            "eventos": events[:20],
            "mas_eventos": len(events) > 20,
        }

    @app.post("/api/admin/clientes/{cid}/interacciones", status_code=201)
    def interaction(
        cid: int, body: Interaction, admin: Annotated[dict, Depends(require_write)]
    ):
        with database(app) as (_, cur):
            cur.execute("SELECT id FROM clientes WHERE id=%s FOR KEY SHARE", (cid,))
            if not cur.fetchone():
                raise HTTPException(404, "Cliente no encontrado.")
            event(cur, cid, None, body.tipo, app.state.username, body.canal)
        return {"ok": True}
