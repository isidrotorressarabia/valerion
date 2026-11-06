"""Trusted origins and integration settings loaded once per application."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit
from collections.abc import Mapping
from psycopg.conninfo import conninfo_to_dict, make_conninfo


@dataclass(frozen=True)
class Settings:
    production: bool
    origin: str
    hosts: tuple[str, ...]
    webhook_secret: str = field(repr=False)
    collector_id: str
    smtp_email: str
    smtp_password: str = field(repr=False)
    edge_secret: str = field(repr=False)

    @classmethod
    def load(cls, config: Mapping[str, str]) -> "Settings":
        environment = config.get("APP_ENV", "development")
        if environment not in {"development", "production", "test"}:
            raise RuntimeError("APP_ENV debe ser development, test o production.")
        production = environment == "production"
        raw = config.get("PUBLIC_BASE_URL", "").rstrip("/")
        if production and not raw:
            raise RuntimeError(
                "Configura PUBLIC_BASE_URL con el origen HTTPS de la tienda."
            )
        origin = raw or "http://127.0.0.1:8000"
        parts = urlsplit(origin)
        if (
            not parts.hostname
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
            or parts.scheme not in ({"https"} if production else {"https", "http"})
        ):
            raise RuntimeError(
                "PUBLIC_BASE_URL debe contener únicamente el origen permitido."
            )
        hosts = {
            parts.hostname,
            *(
                s.strip()
                for s in config.get("ALLOWED_HOSTS", "").split(",")
                if s.strip()
            ),
        }
        if not production:
            hosts.update({"localhost", "127.0.0.1", "testserver"})
        if any("*" in host or "/" in host or ":" in host for host in hosts):
            raise RuntimeError("ALLOWED_HOSTS requiere nombres de host explícitos.")
        secret = config.get("MERCADOPAGO_WEBHOOK_SECRET", "")
        collector = config.get("MERCADOPAGO_COLLECTOR_ID", "")
        if config.get("MERCADOPAGO_ACCESS_TOKEN") and (
            not secret or not collector.isdecimal()
        ):
            raise RuntimeError(
                "Configura firma de webhook e identificador del vendedor para activar Mercado Pago."
            )
        edge_secret = config.get("EDGE_SHARED_SECRET", "")
        if edge_secret and len(edge_secret) < 32:
            raise RuntimeError("EDGE_SHARED_SECRET requiere al menos 32 caracteres.")
        return cls(
            production,
            origin,
            tuple(sorted(hosts)),
            secret,
            collector,
            config.get("SMTP_EMAIL", ""),
            config.get("SMTP_PASSWORD", ""),
            edge_secret,
        )


def database_dsn(url: str, production: bool) -> str:
    if not url:
        raise RuntimeError("Configura DATABASE_URL.")
    try:
        info = conninfo_to_dict(url)
    except Exception:
        raise RuntimeError("DATABASE_URL tiene un formato inválido.") from None
    if not production:
        return url
    if info.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
        raise RuntimeError("La conexión de producción necesita TLS.")
    # Preserve legacy Neon DSNs, strengthen server certificate and hostname checks.
    return make_conninfo(
        url, sslmode="verify-full", sslrootcert=info.get("sslrootcert", "system")
    )
