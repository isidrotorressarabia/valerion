"""Versioned AES-256-GCM envelopes with per-record associated data."""

import base64
import json
import os
import re
from typing import Any
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class DeliveryVault:
    def __init__(self, setting: str):
        self.keys: dict[str, AESGCM] = {}
        for value in setting.split(","):
            key_id, separator, encoded = value.strip().partition(":")
            if (
                not separator
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", key_id)
                or key_id in self.keys
            ):
                raise ValueError(
                    "Configura DELIVERY_KEYS como identificador:clave_base64url."
                )
            raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
            if len(raw) != 32:
                raise ValueError("Cada clave de entrega debe tener 32 bytes.")
            self.keys[key_id] = AESGCM(raw)
        self.active = next(iter(self.keys))

    def seal(self, binding: str, data: dict[str, Any]) -> str:
        nonce = os.urandom(12)
        plaintext = json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        ciphertext = self.keys[self.active].encrypt(nonce, plaintext, binding.encode())
        return (
            self.active
            + "."
            + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        )

    def open(self, binding: str, envelope: str | None) -> dict[str, Any]:
        if envelope is None:
            return {}
        key_id, encoded = envelope.split(".", 1)
        payload = base64.b64decode(encoded, altchars=b"-_", validate=True)
        return json.loads(
            self.keys[key_id].decrypt(payload[:12], payload[12:], binding.encode())
        )
