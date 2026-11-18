"""Keep both interactive documentation pages under scoped browser policies."""

import base64
import hashlib
import re
from fastapi import Request
from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
    get_redoc_html,
)
from services.http import CSP


def protect(request, html, redoc=False):
    scripts = re.findall(
        r"<script\b[^>]*>(.*?)</script>", html.body.decode(), re.S | re.I
    )
    hashes = " ".join(
        "'sha256-"
        + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()
        + "'"
        for s in scripts
        if s.strip()
    )
    policy = CSP.replace("script-src 'self'", f"script-src 'self' {hashes}").replace(
        "style-src 'self'", "style-src 'self' 'unsafe-inline'"
    )
    policy = policy.replace(
        "img-src 'self' https: blob:", "img-src 'self' https: blob: data:"
    )
    request.state.docs_csp = policy + ("; worker-src 'self' blob:" if redoc else "")
    return html


def attach_docs(app):
    @app.api_route("/docs", methods=["GET", "HEAD"], include_in_schema=False)
    def swagger(request: Request):
        return protect(
            request,
            get_swagger_ui_html(
                openapi_url="/openapi.json",
                title="Valerion · API",
                oauth2_redirect_url="/docs/oauth2-redirect",
                swagger_js_url="/static/vendor/swagger-ui-dist-5.34.1/swagger-ui-bundle.js",
                swagger_css_url="/static/vendor/swagger-ui-dist-5.34.1/swagger-ui.css",
                swagger_ui_parameters={"validatorUrl": None},
                swagger_favicon_url="/static/img/emblem.svg",
            ),
        )

    @app.api_route(
        "/docs/oauth2-redirect", methods=["GET", "HEAD"], include_in_schema=False
    )
    def oauth(request: Request):
        return protect(request, get_swagger_ui_oauth2_redirect_html())

    @app.api_route("/redoc", methods=["GET", "HEAD"], include_in_schema=False)
    def redoc(request: Request):
        return protect(
            request,
            get_redoc_html(
                openapi_url="/openapi.json",
                title="Valerion · API",
                redoc_js_url="/static/vendor/redoc-2.5.4/redoc.standalone.js",
                with_google_fonts=False,
                redoc_favicon_url="/static/img/emblem.svg",
            ),
            True,
        )
