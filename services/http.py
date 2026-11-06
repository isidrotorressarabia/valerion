"""Bounded ASGI request buffering and centralized browser/transport policy."""

import ipaddress
import hmac
import json
import logging
import time
from uuid import uuid4
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Scope, Receive, Send, Message
from services.settings import Settings
from infrastructure import revision_context

LOG = logging.getLogger("valerion")
WRITES = {"POST", "PUT", "PATCH", "DELETE"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: blob:; "
    "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; "
    "form-action 'self' https://www.mercadopago.com.mx https://sandbox.mercadopago.com.mx"
)


class BrowserPolicy:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope["path"], scope["method"]
        revision_context.set({})
        incoming = Headers(scope=scope)
        edge_token = incoming.get("x-valerion-edge", "")
        if self.settings.edge_secret and hmac.compare_digest(
            edge_token.encode(), self.settings.edge_secret.encode()
        ):
            try:
                client_ip = ipaddress.ip_address(
                    incoming.get("x-valerion-client-ip", "")
                )
            except ValueError:
                pass
            else:
                scope["client"] = (str(client_ip), 0)
        started = time.monotonic()
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        sent_headers = False

        async def secured(message: Message) -> None:
            nonlocal sent_headers
            if message["type"] == "http.response.start":
                sent_headers = True
                h = MutableHeaders(scope=message)
                h["X-Request-ID"] = request_id
                h["X-Frame-Options"] = "DENY"
                h["X-Content-Type-Options"] = "nosniff"
                h["Referrer-Policy"] = "strict-origin-when-cross-origin"
                h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                h["Content-Security-Policy"] = scope["state"].get("docs_csp", CSP)
                if self.settings.production or scope["scheme"] == "https":
                    h["Strict-Transport-Security"] = "max-age=31536000"
                public = method in {"GET", "HEAD"} and message["status"] == 200
                if (
                    public
                    and path.startswith("/static/build/")
                    and path != "/static/build/manifest.json"
                ):
                    h["Cache-Control"] = "public,max-age=31536000,immutable"
                elif public and (
                    path.startswith(("/static/", "/media/"))
                    or path in ("/robots.txt", "/sitemap.xml")
                    or path.startswith("/sitemaps/")
                ):
                    h["Cache-Control"] = "public,max-age=300"
                elif public and path == "/theme.css" and not scope.get("query_string"):
                    h["Cache-Control"] = "public,max-age=30,must-revalidate"
                elif scope["state"].get("public_html") and message["status"] in {
                    200,
                    304,
                }:
                    h["Cache-Control"] = "public,max-age=0,must-revalidate"
                else:
                    h["Cache-Control"] = "private,no-store"
                if path.startswith(
                    (
                        "/admin",
                        "/cuenta",
                        "/api/",
                        "/docs",
                        "/redoc",
                        "/openapi",
                        "/comprar",
                        "/ready",
                        "/health",
                    )
                ):
                    h["X-Robots-Tag"] = "noindex, nofollow"
                route = getattr(scope.get("route"), "path", "unmatched")
                LOG.info(
                    json.dumps(
                        {
                            "event": "request",
                            "id": request_id,
                            "method": method,
                            "route": route,
                            "status": message["status"],
                            "ms": round((time.monotonic() - started) * 1000, 1),
                        }
                    )
                )
            await send(message)

        async def reject(code: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code=code)(
                scope, receive, secured
            )

        async def dispatch(read):
            try:
                await self.app(scope, read, secured)
            except Exception as exc:
                LOG.error("request_error id=%s type=%s", request_id, type(exc).__name__)
                if sent_headers:
                    raise
                await reject(500, "No se pudo completar la operación.")

        if method not in WRITES:
            await dispatch(receive)
            return
        host = incoming.get("host", "")
        allowed = {self.settings.origin}
        if not self.settings.production:
            allowed.add(scope["scheme"] + "://" + host)
        if path != "/api/pagos/webhook" and (
            (incoming.get("origin") and incoming["origin"] not in allowed)
            or incoming.get("sec-fetch-site") == "cross-site"
        ):
            await reject(403, "Origen no permitido.")
            return
        media = incoming.get("content-type", "").partition(";")[0].lower().strip()
        if (
            path.startswith("/api/")
            and method in {"POST", "PUT", "PATCH"}
            and path != "/api/pagos/webhook"
        ):
            expected = (
                "multipart/form-data" if path == "/api/imagenes" else "application/json"
            )
            if media != expected:
                await reject(415, "Tipo de contenido no permitido.")
                return
        limit = 8 * 1024 * 1024 + 65536 if path == "/api/imagenes" else 2 * 1024 * 1024
        if path.startswith("/comprar/") or path == "/api/pagos/webhook":
            limit = 65536
        raw_length = incoming.get("content-length")
        if raw_length and (
            not raw_length.isascii()
            or not raw_length.isdecimal()
            or len(raw_length) > 12
        ):
            await reject(400, "Longitud inválida.")
            return
        if raw_length and int(raw_length) > limit:
            await reject(413, "Solicitud demasiado grande.")
            return
        data = bytearray()
        while True:
            chunk = await receive()
            if chunk["type"] == "http.disconnect":
                return
            if len(data) + len(chunk.get("body", b"")) > limit:
                await reject(413, "Solicitud demasiado grande.")
                return
            data.extend(chunk.get("body", b""))
            if not chunk.get("more_body", False):
                break
        buffered = True

        async def replay():
            nonlocal buffered
            if buffered:
                buffered = False
                return {"type": "http.request", "body": bytes(data), "more_body": False}
            return await receive()

        await dispatch(replay)
