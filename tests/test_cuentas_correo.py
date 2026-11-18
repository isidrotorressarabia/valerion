"""Recuperación de contraseña, verificación de correo, consentimiento, ARCO y cola de correo.

Todas las pruebas capturan `services.mail._send`: no hay SMTP real.
Requieren TEST_DATABASE_URL, igual que el resto de la suite.
"""
import re
from dataclasses import replace

import pytest

from infrastructure import database
from services import mail
from test_accounts import register
from test_enterprise import buyer, checkout, notify  # noqa: F401  (fixtures y helpers)

PASSWORD = "UnaClave-Larga-123"


@pytest.fixture
def outbox(client, monkeypatch):
    app = client.app
    app.state.settings = replace(
        app.state.settings, smtp_email="tienda@example.com", smtp_password="mock"
    )
    sent = []
    monkeypatch.setattr(mail, "_send", lambda config, msg: sent.append(msg))
    return sent


def token_of(msg):
    return re.search(r"#t=([\w-]+)", msg.get_content())[1]


def test_email_verification_flow_and_checkout_gate(client, outbox, buyer):  # noqa: F811
    item, h, gateway = buyer
    me = client.get("/api/clientes/me").json()
    assert me["verificacion_requerida"] and not me["email_verificado"]
    # Sin verificar no se puede comprar.
    page = client.get(item["ruta"]).text
    body = {k: re.search(f'name="{k}" value="([^"]+)"', page)[1] for k in ("csrf", "checkout_key")}
    assert client.post(f"/comprar/{item['id']}", data=body, follow_redirects=False).status_code == 403
    # Reenviar enlace y verificarlo (un solo uso).
    assert client.post("/api/clientes/verificacion/reenviar", headers=h, json={}).status_code == 202
    token = token_of(outbox[-1])
    assert client.post("/api/clientes/verificar", json={"token": token}).status_code == 200
    assert client.post("/api/clientes/verificar", json={"token": token}).status_code == 400
    assert client.get("/api/clientes/me").json()["email_verificado"] is True
    assert client.post(f"/comprar/{item['id']}", data=body, follow_redirects=False).status_code == 303


def test_password_reset_flow(client, outbox):
    register(client)
    outbox.clear()
    # Un correo desconocido recibe la misma respuesta y no genera mensajes.
    assert client.post("/api/clientes/recuperar", json={"email": "nadie@example.com"}).status_code == 202
    assert outbox == []
    assert client.post("/api/clientes/recuperar", json={"email": "persona@example.com"}).status_code == 202
    token = token_of(outbox[-1])
    assert client.post("/api/clientes/restablecer", json={"token": token, "nueva": "NuevaClave-Larga-456"}).status_code == 200
    assert client.post("/api/clientes/restablecer", json={"token": token, "nueva": "OtraClave-Larga-789"}).status_code == 400
    assert client.get("/api/clientes/me").status_code == 401  # sesiones cerradas
    bad = client.post("/api/clientes/login", json={"email": "persona@example.com", "password": PASSWORD})
    assert bad.status_code == 401
    ok = client.post("/api/clientes/login", json={"email": "persona@example.com", "password": "NuevaClave-Larga-456"})
    assert ok.status_code == 200 and client.get("/api/clientes/me").json()["email_verificado"] is True


def test_recovery_unavailable_without_smtp(client):
    assert client.post("/api/clientes/recuperar", json={"email": "persona@example.com"}).status_code == 503


def test_consent_and_age_are_enforced_server_side(client):
    app = client.app
    app.state.settings = replace(app.state.settings, consent_required=True)
    base = {"nombre": "Persona de prueba", "email": "persona@example.com", "password": PASSWORD}
    assert client.post("/api/clientes/registro", json=base).status_code == 422
    assert client.post("/api/clientes/registro", json={**base, "acepto_aviso": "on"}).status_code == 422
    assert client.post("/api/clientes/registro", json={**base, "acepto_aviso": "on", "mayor_edad": "on"}).status_code == 201
    with database(app) as (_, cur):
        cur.execute("SELECT mayor_edad_declarada,version_legal,aceptacion_legal_en FROM clientes")
        row = cur.fetchone()
    assert row["mayor_edad_declarada"] and row["version_legal"] and row["aceptacion_legal_en"]


def test_export_and_erase_account(client):
    h = register(client)
    data = client.get("/api/clientes/me/datos")
    assert data.status_code == 200 and "attachment" in data.headers["content-disposition"]
    assert data.json()["perfil"]["email"] == "persona@example.com" and data.json()["pedidos"] == []
    wrong = client.request("DELETE", "/api/clientes/me", headers=h, json={"password": "incorrecta"})
    assert wrong.status_code == 400
    assert client.request("DELETE", "/api/clientes/me", headers=h, json={"password": PASSWORD}).status_code == 200
    assert client.post("/api/clientes/login", json={"email": "persona@example.com", "password": PASSWORD}).status_code == 401
    with database(client.app) as (_, cur):
        cur.execute("SELECT nombre,email,activo,eliminado_en FROM clientes")
        row = cur.fetchone()
    assert row["nombre"] == "Cuenta eliminada" and row["email"].endswith("@invalid.local")
    assert not row["activo"] and row["eliminado_en"]
    register(client)  # el correo queda libre para una cuenta nueva


def test_legal_pages_and_footer(client):
    for path in ("/privacidad", "/terminos", "/envios-devoluciones"):
        page = client.get(path)
        assert page.status_code == 200 and "COMPLETAR" in page.text  # sin datos legales configurados
    assert "/privacidad" in client.get("/").text


def test_mail_queue_backoff_lease_and_notifications(client, headers, outbox, buyer, monkeypatch):  # noqa: F811
    app = client.app
    item, h, gateway = buyer
    with database(app) as (_, cur):  # la cuenta de la prueba ya está verificada
        cur.execute("UPDATE clientes SET email_verificado_en=now()")
    app.state.settings = replace(app.state.settings, notify_email="dueno@example.com")
    _, pref = checkout(client, buyer)
    # SMTP caído: primer intento falla y se programa el siguiente.
    down = {"on": True}

    def flaky(config, msg):
        if down["on"]:
            raise OSError()
        outbox.append(msg)

    monkeypatch.setattr(mail, "_send", flaky)
    assert notify(client).status_code == 200
    with database(app) as (_, cur):
        cur.execute("SELECT intentos,enviado_en,proximo_intento>now() AS espera FROM correo_pendiente")
        row = cur.fetchone()
    assert row["intentos"] == 1 and row["enviado_en"] is None and row["espera"]
    assert mail.retry_pending(app) == 0  # aún en retroceso
    down["on"] = False
    with database(app) as (_, cur):
        cur.execute("UPDATE correo_pendiente SET proximo_intento=now()")
    assert mail.retry_pending(app) == 1
    assert any("Pago confirmado" in str(msg["Subject"]) for msg in outbox)
    assert mail.retry_pending(app) == 0  # ya enviado
    # Un arrendamiento vigente impide un segundo envío simultáneo.
    with database(app) as (_, cur):
        cur.execute("UPDATE correo_pendiente SET enviado_en=NULL,reservado_hasta=now()+interval '1 hour'")
    before = len(outbox)
    mail.deliver(app, int(pref["external_reference"]))
    assert len(outbox) == before
    # Aviso al dueño y correo de envío.
    mail.notify_owner(app, int(pref["external_reference"]))
    assert any("Pago recibido" in str(msg["Subject"]) for msg in outbox)
    res = client.put(f"/api/admin/ordenes/{pref['external_reference']}/estado", json={"estado": "enviada"}, headers=headers)
    assert res.status_code == 200
    assert any("va en camino" in str(msg["Subject"]) for msg in outbox)
    # Las transiciones inválidas se rechazan.
    res = client.put(f"/api/admin/ordenes/{pref['external_reference']}/estado", json={"estado": "pagada"}, headers=headers)
    assert res.status_code == 409
