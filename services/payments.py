"""Mercado Pago adapter. All outbound endpoints are fixed by the application."""

from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import logging
import re
import time
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException

PAYMENT_HOSTS = ("www.mercadopago.com.mx", "sandbox.mercadopago.com.mx")
log = logging.getLogger("valerion")


class MercadoPago:
    def __init__(self, token: str):
        self.http = httpx.Client(
            base_url="https://api.mercadopago.com",
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(12, connect=4),
            headers={"Authorization": f"Bearer {token}"},
        )

    def call(self, method: str, path: str, **options) -> dict:
        try:
            response = self.http.request(method, path, **options)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(
                502, "Mercado Pago no respondió correctamente. Reintenta."
            ) from None

    def preference(self, data: dict, key: str) -> tuple[str, str]:
        result = self.call(
            "POST",
            "/checkout/preferences",
            json=data,
            headers={"X-Idempotency-Key": key},
        )
        url = result.get("init_point", "")
        pref = result.get("id")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc not in PAYMENT_HOSTS:
            raise HTTPException(502, "Destino de pago inválido.")
        if not isinstance(pref, str) or not pref or len(pref) > 256:
            raise HTTPException(502, "Preferencia de pago inválida.")
        return url, pref

    def payment(self, payment_id: str) -> dict:
        payment = self.call("GET", "/v1/payments/" + payment_id)
        payment["_verified_preference"] = None
        merchant_id = str((payment.get("order") or {}).get("id", ""))
        if (
            payment.get("status") == "approved"
            and not (payment.get("metadata") or {}).get("checkout_key")
            and re.fullmatch(r"[0-9]{1,20}", merchant_id)
        ):
            order = self.call("GET", "/merchant_orders/" + merchant_id)
            if (
                str(order.get("id")) == merchant_id
                and order.get("external_reference") == payment.get("external_reference")
                and any(
                    str(p.get("id")) == payment_id for p in order.get("payments", [])
                )
            ):
                payment["_verified_preference"] = order.get("preference_id")
        return payment


def verify_signature(
    secret: str, data_id: str, request_id: str, signature: str
) -> None:
    parts = dict(
        item.strip().split("=", 1) for item in signature.split(",") if "=" in item
    )
    ts, digest = parts.get("ts", ""), parts.get("v1", "")
    if (
        not secret
        or not re.fullmatch(r"[0-9]{1,20}", data_id)
        or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,160}", request_id)
        or not re.fullmatch(r"[0-9]{10}(?:[0-9]{3})?", ts)
        or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
    ):
        raise HTTPException(401, "Notificación inválida.")
    seconds = int(ts) / (1000 if len(ts) == 13 else 1)
    signed = f"id:{data_id};request-id:{request_id};ts:{ts};"
    expected = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    if abs(time.time() - seconds) > 300 or not hmac.compare_digest(
        expected, digest.lower()
    ):
        raise HTTPException(401, "Notificación inválida.")


def matches(payment: dict, order: dict, collector: str, payment_id: str) -> bool:
    try:
        amount = Decimal(str(payment["transaction_amount"]))
        return (
            str(payment["id"]) == payment_id
            and amount.is_finite()
            and amount * 100 == order["monto_centavos"]
            and payment.get("currency_id") == "MXN"
            and str(payment.get("collector_id")) == collector
            and (
                order["checkout_key"] is None
                or (payment.get("metadata") or {}).get("checkout_key")
                == str(order["checkout_key"])
                or (
                    not (payment.get("metadata") or {}).get("checkout_key")
                    and bool(order["preferencia_id"])
                    and order["preferencia_id"] == payment.get("_verified_preference")
                )
            )
        )
    except (KeyError, InvalidOperation, ValueError):
        return False
