"""Durable confirmation queue. Deliver only after an authenticated, verified payment."""

import logging
import smtplib
import ssl
from decimal import Decimal
from email.message import EmailMessage
from fastapi import FastAPI
from infrastructure import database


def deliver(app: FastAPI, order_id: int) -> None:
    config = app.state.settings
    if not config.smtp_email or not config.smtp_password:
        return
    with database(app) as (_, cur):
        cur.execute(
            """SELECT o.id,o.producto_nombre,o.monto_centavos,c.email,c.nombre
                       FROM correo_pendiente q JOIN ordenes o ON o.id=q.orden_id
                       JOIN clientes c ON c.id=o.cliente_id
                       WHERE q.orden_id=%s AND q.enviado_en IS NULL FOR UPDATE OF q SKIP LOCKED""",
            (order_id,),
        )
        order = cur.fetchone()
        if not order:
            return
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
            with smtplib.SMTP_SSL(
                "smtp.gmail.com", 465, timeout=8, context=ssl.create_default_context()
            ) as smtp:
                smtp.login(config.smtp_email, config.smtp_password)
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException):
            logging.getLogger("valerion").warning(
                "confirmacion_correo_pendiente orden=%s", order_id
            )
            cur.execute(
                "UPDATE correo_pendiente SET intentos=intentos+1 WHERE orden_id=%s",
                (order_id,),
            )
        else:
            cur.execute(
                "UPDATE correo_pendiente SET enviado_en=now(),intentos=intentos+1 WHERE orden_id=%s",
                (order_id,),
            )
