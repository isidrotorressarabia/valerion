"""Regression tests at the HTTP/SQL boundary. No network calls to payment or mail providers."""
from dataclasses import replace
from xml.etree import ElementTree as ET
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from uuid import uuid4
import pytest
import httpx
from fastapi import HTTPException
from cryptography.exceptions import InvalidTag
from conftest import product
from test_accounts import register
from test_web import create
from infrastructure import database
from services.logistics import upgrade
from services.operations import rotate
from services.payments import MercadoPago, verify_signature
from services.privacy import DeliveryVault
from services.settings import Settings

ADDRESS = {"nombre":"Persona de prueba","direccion":"Calle de ejemplo 42","cp":"82000","ciudad":"Mazatlán",
           "telefono":"+52 669 1234567","latitud":23.2,"longitud":-106.42,"canal_preferido":"whatsapp"}


class FakeGateway:
    def __init__(self):
        self.preferences=[]
        self.payment_data={}
        self.lookups=0

    def preference(self,data,key):
        self.preferences.append(data)
        return "https://www.mercadopago.com.mx/checkout/test","pref-test"

    def payment(self,id):
        self.lookups+=1
        return self.payment_data


@pytest.fixture
def buyer(client,headers):
    app=client.app
    gateway=FakeGateway()
    app.state.settings=replace(app.state.settings,webhook_secret="mock-signature-key",collector_id="78")
    app.state.gateway=gateway
    item=create(client,headers)
    h=register(client)
    assert client.put("/api/clientes/perfil",headers=h,json=ADDRESS).status_code==200
    yield item,h,gateway
    app.state.gateway=None


def form(client,item):
    page=client.get(item["ruta"])
    assert page.status_code==200
    return {key:re.search('name="'+key+'" value="([^"]+)"',page.text)[1] for key in ("csrf","checkout_key")}


def checkout(client,buyer):
    item,h,gateway=buyer
    body=form(client,item)
    res=client.post("/comprar/"+str(item["id"]),data=body,follow_redirects=False)
    assert res.status_code==303 and res.headers["location"].startswith("https://www.mercadopago.com.mx/")
    pref=gateway.preferences[0]
    gateway.payment_data=dict(id=123,external_reference=pref["external_reference"],status="approved",collector_id=78,
                              transaction_amount="890.50",currency_id="MXN",metadata=pref["metadata"])
    return body,pref


def notify(client,ts=None,valid=True):
    ts=str(ts or int(time.time()))
    signature=hmac.new(b"mock-signature-key",f"id:123;request-id:demo;ts:{ts};".encode(),hashlib.sha256).hexdigest()
    return client.post("/api/pagos/webhook?type=payment&data.id=123",json={},
                       headers={"x-request-id":"demo","x-signature":f"ts={ts},v1={signature if valid else '0'*64}"})


def test_checkout_paid_once_and_fulfilment(client,headers,buyer):
    item,h,gateway=buyer
    body,pref=checkout(client,buyer)
    assert client.post("/comprar/"+str(item["id"]),data=body,follow_redirects=False).status_code==303
    assert len(gateway.preferences)==1
    oid=pref["external_reference"]
    orders=client.get("/api/clientes/ordenes").json()["items"]
    assert len(orders)==1 and orders[0]["estado"]=="pendiente" and orders[0]["entrega"]["cp"]=="82000"
    assert client.put("/api/clientes/perfil",headers=h,json={**ADDRESS,"cp":"82100"}).status_code==200
    assert client.get("/api/clientes/ordenes").json()["items"][0]["entrega"]["cp"]=="82000"
    assert notify(client,valid=False).status_code==401 and gateway.lookups==0
    assert notify(client,ts=int(time.time())-600).status_code==401 and gateway.lookups==0
    assert notify(client).status_code==200
    assert client.put("/api/admin/ordenes/"+oid+"/estado",json={"estado":"enviada"},headers=headers).status_code==200
    assert notify(client).status_code==200
    orders=client.get("/api/admin/ventas").json()["orders"]
    assert orders[0]["estado"]=="enviada" and orders[0]["pago_id"]=="123"
    assert client.get("/admin/ventas").status_code==200
    with database(client.app) as (_,cur):
        for table in ("correo_pendiente","pago_recibos"):
            cur.execute("SELECT COUNT(*) n FROM "+table);assert cur.fetchone()["n"]==1
        cur.execute("SELECT count(*) n FROM cliente_eventos WHERE tipo='pago.confirmado'")
        assert cur.fetchone()["n"]==1


@pytest.mark.parametrize("diff",[{"currency_id":"USD"},{"transaction_amount":"0.01"},{"collector_id":"99"},
                                  {"metadata":{}},{"external_reference":"x"},{"id":999}])
def test_unrelated_payments_cannot_credit(client,buyer,diff):
    _,_,gateway=buyer
    checkout(client,buyer)
    gateway.payment_data.update(diff)
    assert notify(client).status_code==200
    order=client.get("/api/clientes/ordenes").json()["items"][0]
    assert order["estado"]=="pendiente" and not order["pago_id"]


def test_denied_checkout_and_private_history(client,headers,buyer):
    item,h,gateway=buyer
    body=form(client,item)
    assert client.post("/comprar/"+str(item["id"]),data={"checkout_key":body["checkout_key"]}).status_code==403
    assert client.post("/comprar/"+str(item["id"]),data=body,headers={"origin":"https://otro.mx"}).status_code==403
    for overrides in ({"availability":"sold_out"},{"availability":"request"},{"published":False}):
        other=create(client,headers,**overrides)
        assert client.post("/comprar/"+str(other["id"]),data=body).status_code==404
    checkout(client,buyer)
    register(client,"otra@example.com")
    assert client.get("/api/clientes/ordenes").json()["items"]==[]
    assert client.get("/api/admin/ventas").status_code==200  # separate admin session still present
    client.cookies.delete("valerion_session")
    assert client.get("/api/admin/clientes/1/historial").status_code==401
    assert client.put("/api/admin/ordenes/1/estado",json={"estado":"pagada"},headers=h).status_code==401


def test_encrypted_delivery_and_audit(client,buyer,headers):
    _,h,_=buyer
    _,pref=checkout(client,buyer)
    oid=pref["external_reference"]
    for diff in ({"latitud":300},{"telefono":"----------"},{"cp":"00x00"},{"direccion":"calle\nxyz"}):
        assert client.put("/api/clientes/perfil",headers=h,json={**ADDRESS,**diff}).status_code==422
    assert client.put("/api/admin/ordenes/"+oid+"/entrega",headers=headers,json={"cp":"82100"}).status_code==200
    cid=client.get("/api/clientes/me").json()["id"]
    assert client.post(f"/api/admin/clientes/{cid}/interacciones",headers=headers,
                       json={"canal":"tienda","tipo":"consulta"}).status_code==201
    events=client.get(f"/api/admin/clientes/{cid}/historial").json()["eventos"]
    assert {e["tipo"] for e in events}>={"orden.entrega_modificada","entrega.modificada","consulta"}
    assert ADDRESS["direccion"] not in json.dumps(events)
    with database(client.app) as (_,cur):
        for table in ("ordenes","clientes"):
            cur.execute("SELECT entrega_segura FROM "+table)
            token=cur.fetchone()["entrega_segura"]
            assert ADDRESS["direccion"] not in token and ADDRESS["telefono"] not in token
            with pytest.raises(InvalidTag): client.app.state.vault.open("wrong:1",token)


def test_legacy_migration_rotation(client,headers):
    h=register(client); cid=client.get("/api/clientes/me").json()["id"]
    item=create(client,headers)
    with database(client.app) as (_,cur):
        cur.execute("ALTER TABLE clientes ADD direccion text; ALTER TABLE clientes ADD latitud numeric; ALTER TABLE ordenes ADD direccion_envio text")
        cur.execute("UPDATE clientes SET direccion=%s,latitud=23.21 WHERE id=%s",(ADDRESS["direccion"],cid))
        cur.execute("""INSERT INTO ordenes(producto_id,cliente_id,monto_centavos,creado_en,direccion_envio)
                       VALUES(%s,%s,900,'2026-01-01','Entrega histórica') RETURNING id""",(item["id"],cid))
        oid=cur.fetchone()["id"]
        upgrade(cur,client.app.state.vault)
        upgrade(cur,client.app.state.vault)
    me=client.get("/api/clientes/me").json(); assert me["direccion"]==ADDRESS["direccion"] and me["latitud"]=="23.21"
    assert client.get("/api/clientes/ordenes").json()["items"][0]["entrega"]["direccion_envio"]=="Entrega histórica"
    new=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    import os
    client.app.state.vault=DeliveryVault("new:"+new+","+os.environ["DELIVERY_KEYS"])
    assert rotate(client.app)==2
    client.app.state.vault=DeliveryVault("new:"+new)
    assert client.get("/api/clientes/me").json()["direccion"]==ADDRESS["direccion"]
    assert rotate(client.app)==0


def test_seo_visibility_canonical_and_script_escaping(client,headers):
    item=create(client,headers,nombre="Mezcal Espadín",descripcion="Texto </script><script>alert(1)</script> de prueba")
    hidden=create(client,headers,published=False)
    legacy=client.get(f"/producto/{item['id']}?pago=pendiente&utm_source=x",follow_redirects=False)
    assert legacy.status_code==301 and legacy.headers["location"]==item["ruta"]+"?pago=pendiente"
    page=client.get(item["ruta"]+"?utm_source=test")
    origin=client.app.state.settings.origin
    assert f'rel="canonical" href="{origin}{item["ruta"]}"' in page.text
    graph=json.loads(re.search(r'type="application/ld\+json">(.*?)</script>',page.text,re.S)[1])["@graph"]
    assert [v["@type"] for v in graph]==["OnlineStore","Product","BreadcrumbList"]
    assert graph[1]["offers"]["priceCurrency"]=="MXN" and graph[1]["offers"]["price"]=="890.50"
    assert "<script>alert" not in page.text
    locations=[v.text for v in ET.fromstring(client.get("/sitemap.xml").content).findall(".//{*}loc")]
    assert origin+item["ruta"] in locations and origin+hidden["ruta"] not in locations
    for path in ("/cuenta/registro","/admin/preview"):
        private=client.get(path)
        assert "noindex" in private.headers["x-robots-tag"] and 'rel="canonical"' not in private.text
    assert client.get("/sitemap.xml",headers={"host":"evil.example"}).status_code==400
    assert client.get(hidden["ruta"]).status_code==404


def test_body_limit_without_content_length_and_cors(client):
    res=client.post("/api/clientes/login",content=(b"x"*65536 for _ in range(34)),headers={"Content-Type":"application/json"})
    assert res.status_code==413 and res.headers["x-content-type-options"]=="nosniff"
    preflight=client.options("/api/clientes/me",headers={"Origin":"https://evil.example","Access-Control-Request-Method":"PUT"})
    assert "access-control-allow-origin" not in preflight.headers
    assert client.get("/docs").status_code==200 and "sha256-" in client.get("/docs").headers["content-security-policy"]


def test_transport_rejects_foreign_checkout_destination():
    gateway=MercadoPago("test")
    gateway.http.close()
    gateway.http=httpx.Client(base_url="https://api.mercadopago.com",transport=httpx.MockTransport(
        lambda request:httpx.Response(201,json={"init_point":"https://www.mercadopago.com.mx.attacker.example/"})))
    with pytest.raises(HTTPException) as error:
        gateway.preference({},str(uuid4()))
    assert error.value.status_code==502
    gateway.http.close()


@pytest.mark.parametrize("origin",["http://valerion.example","https://valerion.example/ruta","https://u:p@valerion.example"])
def test_production_requires_trusted_https_origin(origin):
    with pytest.raises(RuntimeError):
        Settings.load({"APP_ENV":"production","PUBLIC_BASE_URL":origin})

def test_preference_failure_same_form_reuses_order(client,buyer,monkeypatch):
    item,h,gateway=buyer
    body=form(client,item)
    endpoint=f"/comprar/{item['id']}"
    with monkeypatch.context() as m:
        def offline(data,key):
            raise HTTPException(502,"Reintenta")
        m.setattr(gateway,"preference",offline)
        assert client.post(endpoint,data=body).status_code==502
    assert client.post(endpoint,data=body,follow_redirects=False).status_code==303
    assert len(client.get("/api/clientes/ordenes").json()["items"])==1


def test_mail_retry_and_immutable_audit(client,buyer,monkeypatch):
    from services.mail import deliver
    import psycopg
    checkout(client,buyer)
    app=client.app
    assert notify(client).status_code==200
    app.state.settings=replace(app.state.settings,smtp_email="test@example.com",smtp_password="mock")
    sent=[]
    class SMTP:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def login(self,*args):pass
        def send_message(self,msg):sent.append(msg)
    def down(*args,**kwargs):raise OSError()
    monkeypatch.setattr("services.mail.smtplib.SMTP_SSL",down)
    deliver(app,1)
    monkeypatch.setattr("services.mail.smtplib.SMTP_SSL",lambda *args,**kwargs:SMTP())
    deliver(app,1)
    deliver(app,1)
    assert len(sent)==1 and "Pago confirmado" in str(sent[0]["Subject"])
    with database(app) as (_,cur):
        cur.execute("SELECT intentos,enviado_en FROM correo_pendiente")
        row=cur.fetchone()
        assert row["intentos"]==2 and row["enviado_en"]
    with pytest.raises(psycopg.Error):
        with database(app) as (_,cur):
            cur.execute("DELETE FROM cliente_eventos")


def test_public_cache_revalidation_and_cross_worker_revision(client,headers):
    item=create(client,headers)
    client.cookies.clear()
    response=client.get(item["ruta"])
    assert "public" in response.headers["Cache-Control"]
    assert client.get(item["ruta"],headers={"If-None-Match":response.headers["etag"]}).status_code==304
    assert client.get("/api/mezcales").json()["total"]==1
    # A different process edits the row; local Python caches are deliberately untouched.
    with database(client.app) as (_,cur):
        cur.execute("UPDATE mezcales SET extra_json=%s WHERE id=%s",('{"published":false}',item["id"]))
    assert client.get("/api/mezcales").json()["total"]==0
    assert client.get(item["ruta"],headers={"If-None-Match":response.headers["etag"]}).status_code==404
    register(client)
    assert "no-store" in client.get("/").headers["Cache-Control"]


def test_crawlable_pagination(client,headers):
    for i in range(13):
        create(client,headers,nombre=f"Producto {i}")
    assert 'href="/coleccion/pagina/2"' in client.get("/").text
    page=client.get("/coleccion/pagina/2")
    assert page.status_code==200 and "Página 2" in page.text and 'href="/coleccion"' in page.text
    assert client.get("/coleccion/pagina/999").status_code==404


def test_provider_error_and_legacy_numeric_reference(client,headers,buyer):
    item,_,gateway=buyer
    with database(client.app) as (_,cur):
        cur.execute("""INSERT INTO ordenes(producto_id,cliente_id,monto_centavos,creado_en)
                       VALUES(%s,1,89050,'2026-10-15') RETURNING id""",(item["id"],))
        oid=cur.fetchone()["id"]
    gateway.payment_data=dict(id=123,external_reference=str(oid),status="approved",collector_id=78,
                              transaction_amount="890.50",currency_id="MXN")
    assert notify(client).status_code==200
    assert client.get("/api/admin/ventas").json()["orders"][0]["estado"]=="pagada"

def test_authoritative_merchant_order_fallback(client,buyer):
    item,_,fake=buyer
    checkout(client,buyer)
    payload={**fake.payment_data,"metadata":{},"order":{"id":44}}
    def provider(request):
        if request.url.path=="/v1/payments/123":
            return httpx.Response(200,json=payload)
        assert request.url.path=="/merchant_orders/44"
        return httpx.Response(200,json={"id":44,"external_reference":"1","preference_id":"pref-test","payments":[{"id":123}]})
    gateway=MercadoPago("test")
    gateway.http.close()
    gateway.http=httpx.Client(base_url="https://api.mercadopago.com",transport=httpx.MockTransport(provider))
    client.app.state.gateway=gateway
    try:
        assert notify(client).status_code==200
        assert client.get("/api/clientes/ordenes").json()["items"][0]["estado"]=="pagada"
    finally:
        gateway.http.close()
