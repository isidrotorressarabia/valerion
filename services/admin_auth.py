"""Revocable administrator sessions, password verification and mutation authorization."""

import hashlib
import hmac
import secrets
import time
from typing import Annotated
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, ConfigDict
from infrastructure import database, audit
from accounts import throttle

COOKIE = "valerion_session"
SESSION_SECONDS = 8 * 60 * 60


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usuario: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


def current_session(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 256:
        return None
    hashed = hashlib.sha256(token.encode()).hexdigest()
    with database(request.app) as (conn, cursor):
        cursor.execute(
            "SELECT * FROM sessions WHERE token_hash=%s AND expires>%s AND credential_fingerprint=%s",
            (hashed, int(time.time()), request.app.state.fingerprint),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def require_admin(request: Request) -> dict:
    session = current_session(request)
    if not session:
        raise HTTPException(401, "Inicia sesión para administrar el catálogo.")
    return session


def require_write(
    request: Request, session: Annotated[dict, Depends(require_admin)]
) -> dict:
    if not hmac.compare_digest(
        request.headers.get("x-csrf-token", "").encode(), session["csrf"].encode()
    ):
        raise HTTPException(403, "Token de seguridad inválido. Recarga el panel.")
    return session


def attach(app: FastAPI) -> None:
    @app.post("/api/auth/login", tags=["Autenticación"])
    def login(body: Login, request: Request, response: Response):
        client = request.client.host if request.client else "local"
        timestamp = int(time.time())
        failure = None
        # Secondary global budget for password spraying from rotating IPs.
        if hmac.compare_digest(body.usuario.encode(), app.state.username.encode()):
            throttle(app, "admin-login-user:" + app.state.username, 50)
        with database(app) as (_, cursor):
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", ("login:" + client,)
            )
            cursor.execute("SELECT * FROM login_attempts WHERE client=%s", (client,))
            failed = cursor.fetchone()
            if (
                failed
                and failed["failures"] >= 5
                and timestamp - failed["last_attempt"] < 300
            ):
                failure = 429
            else:
                candidate = hashlib.scrypt(
                    body.password.encode(), salt=app.state.salt, n=16384, r=8, p=1
                )
                ok = hmac.compare_digest(
                    candidate, app.state.password_hash
                ) and hmac.compare_digest(
                    body.usuario.encode(), app.state.username.encode()
                )
                if not ok:
                    cursor.execute(
                        "INSERT INTO login_attempts(client,failures,last_attempt) VALUES(%s,1,%s) ON CONFLICT(client) DO UPDATE SET failures=CASE WHEN login_attempts.last_attempt<%s THEN 1 ELSE login_attempts.failures+1 END,last_attempt=EXCLUDED.last_attempt",
                        (client, timestamp, timestamp - 300),
                    )
                    failure = 401
                else:
                    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    cursor.execute(
                        "DELETE FROM login_attempts WHERE client=%s OR last_attempt<%s",
                        (client, timestamp - 86400),
                    )
                    cursor.execute(
                        "DELETE FROM sessions WHERE expires<=%s", (timestamp,)
                    )
                    old = request.cookies.get(COOKIE)
                    if old:
                        cursor.execute(
                            "DELETE FROM sessions WHERE token_hash=%s",
                            (hashlib.sha256(old.encode()).hexdigest(),),
                        )
                    cursor.execute(
                        "INSERT INTO sessions VALUES(%s,%s,%s,%s)",
                        (
                            hashlib.sha256(token.encode()).hexdigest(),
                            csrf,
                            timestamp + SESSION_SECONDS,
                            app.state.fingerprint,
                        ),
                    )
                    audit(cursor, "auth.login", actor=app.state.username)
        if failure:
            raise HTTPException(
                failure,
                "Demasiados intentos. Espera 5 minutos."
                if failure == 429
                else "Usuario o contraseña incorrectos.",
                headers={"Retry-After": "300"} if failure == 429 else None,
            )
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=app.state.secure_cookie,
            samesite="strict",
            max_age=SESSION_SECONDS,
            path="/",
        )
        return {"usuario": app.state.username, "csrf_token": csrf}

    @app.get("/api/auth/me", tags=["Autenticación"])
    def me(session: Annotated[dict, Depends(require_admin)]):
        return {"usuario": app.state.username, "csrf_token": session["csrf"]}

    @app.post("/api/auth/logout", status_code=204, tags=["Autenticación"])
    def logout(response: Response, session: Annotated[dict, Depends(require_write)]):
        with database(app) as (conn, cursor):
            cursor.execute(
                "DELETE FROM sessions WHERE token_hash=%s", (session["token_hash"],)
            )
        response.delete_cookie(
            COOKIE,
            path="/",
            httponly=True,
            samesite="strict",
            secure=app.state.secure_cookie,
        )

    @app.post("/api/admin/sesiones/revocar", status_code=204)
    def revoke_sessions(session: Annotated[dict, Depends(require_write)]):
        with database(app) as (_, cur):
            cur.execute("DELETE FROM sessions")
            audit(cur, "admin.sessions.revoke", actor=app.state.username)
        return Response(status_code=204)
