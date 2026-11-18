"""Cola durable de confirmaciones y correos transaccionales.

El SMTP nunca se ejecuta con una transacción abierta: primero se reserva el envío
(arrendamiento de pocos minutos), después se envía y por último se registra el resultado.
Un fallo programa el siguiente intento con retroceso exponencial.
"""

import asyncio
import logging
import smtplib
import ssl
from decimal import Decimal
from email.message import EmailMessage

from fastapi import FastAPI

from infrastructure import database

LOG = logging.getLogger("valerion")
MAX_ATTEMPTS = 8
INVALID_DOMAIN = "@invalid.local"  # cuentas eliminadas (anonimizadas)


def configured(app: FastAPI) -> bool:
    config = app.state.settings
    return bool(config.smtp_email and config.smtp_password)


def _send(config, msg: EmailMessage) -> None:
    with smtplib.SMTP_SSL(
        "smtp.gmail.com", 465, timeout=8, context=ssl.create_default_context()
    ) as smtp:
        smtp.login(config.smtp_email, config.smtp_password)
        smtp.send_message(msg)


def send_plain(app: FastAPI, to: str, subject: str, body: str) -> bool:
    """Envío inmediato sin cola, para enlaces de un solo uso (el cliente puede pedir otro)."""
    config = app.state.settings
    if not configured(app) or to.endswith(INVALID_DOMAIN):
        return False
    msg = EmailMessage()
    msg["From"] = config.smtp_email
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        _send(config, msg)
    except (OSError, smtplib.SMTPException):
        LOG.warning("correo_no_enviado asunto=%s", subject[:60])
        return False
    return True


def deliver(app: FastAPI, order_id: int) -> None:
    config = app.state.settings
    if not configured(app):
        return
    with database(app) as (_, cur):
        cur.execute(
            """UPDATE correo_pendiente SET intentos=intentos+1,reservado_hasta=now()+interval '3 minutes'
               WHERE orden_id=%s AND enviado_en IS NULL
                 AND (reservado_hasta IS NULL OR reservado_hasta<now()) RETURNING orden_id""",
            (order_id,),
        )
        if not cur.fetchone():
            return
        cur.execute(
            """SELECT o.id,o.producto_nombre,o.monto_centavos,c.email,c.nombre
               FROM ordenes o JOIN clientes c ON c.id=o.cliente_id WHERE o.id=%s""",
            (order_id,),
        )
        order = cur.fetchone()
    sent = False
    skip = order is None or order["email"].endswith(INVALID_DOMAIN)
    if not skip:
        msg = EmailMessage()
        msg["From"] = config.smtp_email
        msg["To"] = order["email"]
        msg["Subject"] = f"Valerion · Pago confirmado de tu orden #{order_id}"
        msg["Message-ID"] = (
            f"<valerion-orden-{order_id}@{config.smtp_email.split('@')[-1]}>"
        )
        total = Decimal(order["monto_centavos"]) / 100
        msg.set_content(
            f"Hola {order['nombre']},\n\nConfirmamos tu pago de ${total:.2f} MXN por "
            f"{order['producto_nombre']}. Puedes seguir tu orden #{order_id} en Mi cuenta.\n\nValerion"
        )
        try:
            _send(config, msg)
            sent = True
        except (OSError, smtplib.SMTPException):
            LOG.warning("confirmacion_correo_pendiente orden=%s", order_id)
    with database(app) as (_, cur):
        if sent or skip:
            cur.execute(
                "UPDATE correo_pendiente SET enviado_en=now(),reservado_hasta=NULL WHERE orden_id=%s",
                (order_id,),
            )
        else:
            cur.execute(
                """UPDATE correo_pendiente SET reservado_hasta=NULL,
                   proximo_intento=now()+make_interval(mins=>LEAST(power(2,intentos)::int,720))
                   WHERE orden_id=%s""",
                (order_id,),
            )


def retry_pending(app: FastAPI, limit: int = 20) -> int:
    """Reintenta confirmaciones vencidas. Seguro con varias instancias gracias al arrendamiento."""
    if not configured(app):
        return 0
    with database(app) as (_, cur):
        cur.execute(
            """SELECT orden_id FROM correo_pendiente
               WHERE enviado_en IS NULL AND intentos<%s AND proximo_intento<=now()
                 AND (reservado_hasta IS NULL OR reservado_hasta<now())
               ORDER BY proximo_intento LIMIT %s""",
            (MAX_ATTEMPTS, limit),
        )
        ids = [r["orden_id"] for r in cur.fetchall()]
    for order_id in ids:
        deliver(app, order_id)
    return len(ids)


async def worker(app: FastAPI, interval: int = 120) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(retry_pending, app)
        except Exception:
            LOG.exception("mail_worker_error")


def notify_shipped(app: FastAPI, order_id: int) -> None:
    if not configured(app):
        return
    with database(app) as (_, cur):
        cur.execute(
            """SELECT o.id,o.producto_nombre,c.email,c.nombre FROM ordenes o
               JOIN clientes c ON c.id=o.cliente_id WHERE o.id=%s AND o.estado='enviada'""",
            (order_id,),
        )
        order = cur.fetchone()
    if order:
        send_plain(
            app,
            order["email"],
            f"Valerion · Tu orden #{order_id} va en camino",
            f"Hola {order['nombre']},\n\nTu orden #{order_id} ({order['producto_nombre']}) ya fue enviada. "
            "Puedes consultar su estado en Mi cuenta. La entrega es solo para personas mayores de 18 años; "
            "podría solicitarse una identificación oficial al recibir.\n\nValerion",
        )


def notify_owner(app: FastAPI, order_id: int) -> None:
    """Avisa al dueño de un pago confirmado, sin transacción abierta durante el SMTP."""
    config = app.state.settings
    if not config.notify_email or not configured(app):
        return
    with database(app) as (_, cur):
        cur.execute(
            "SELECT id,estado,producto_nombre,monto_centavos FROM ordenes WHERE id=%s",
            (order_id,),
        )
        order = cur.fetchone()
    if not order:
        return
    total = Decimal(order["monto_centavos"]) / 100
    revisar = " (REVISAR: orden cancelada)" if order["estado"] == "cancelada" else ""
    if not send_plain(
        app,
        config.notify_email,
        f"Valerion · Pago recibido, orden #{order_id}{revisar}",
        f"Pago de ${total:.2f} MXN por {order['producto_nombre']}.\n"
        f"Orden #{order_id}, estado: {order['estado']}. Revísala en /admin/ventas.",
    ):
        LOG.warning("aviso_dueno_fallido orden=%s", order_id)
