"""Stable, crawlable storefront URLs and bounded XML sitemaps."""

import re
import unicodedata
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from fastapi import HTTPException, Response
from infrastructure import database, PUBLISHED

NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
BUCKET = 10000


def product_path(item: dict) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", item["nombre"])
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "mezcal"
    return f"/producto/{item['id']}/{slug}"


def collection_path(page: int) -> str:
    return "/coleccion" if page == 1 else f"/coleccion/pagina/{page}"


def metadata(origin: str, content: dict, product: dict | None = None) -> dict:
    brand = content["brand_name"]
    canonical = origin + (product_path(product) if product else "/")
    title = f"{product['nombre']} · {brand}" if product else content["page_title"]
    description = " ".join(
        (product["descripcion"] if product else content["meta_description"]).split()
    )[:165]
    image = urljoin(origin, product["imagen_url"] if product else content["hero_image"])
    store = {
        "@type": "OnlineStore",
        "@id": origin + "/#tienda",
        "name": brand,
        "url": origin + "/",
        "logo": urljoin(origin, content["logo_image"]),
    }
    graph = [store]
    if product:
        offer = {
            "@type": "Offer",
            "priceCurrency": "MXN",
            "price": product["precio"],
            "url": canonical,
            "seller": {"@id": store["@id"]},
        }
        status = {"available": "InStock", "sold_out": "OutOfStock"}.get(
            product["availability"]
        )
        if status:
            offer["availability"] = "https://schema.org/" + status
        graph.append(
            {
                "@type": "Product",
                "@id": canonical + "#producto",
                "url": canonical,
                "name": product["nombre"],
                "description": product["descripcion"],
                "image": [image]
                + [urljoin(origin, g["url"]) for g in product["gallery"]],
                "sku": str(product["id"]),
                "brand": {"@type": "Brand", "name": brand},
                "offers": offer,
            }
        )
        graph.append(
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": 1,
                        "name": "Inicio",
                        "item": origin + "/",
                    },
                    {
                        "@type": "ListItem",
                        "position": 2,
                        "name": "Colección",
                        "item": origin + "/coleccion",
                    },
                    {
                        "@type": "ListItem",
                        "position": 3,
                        "name": product["nombre"],
                        "item": canonical,
                    },
                ],
            }
        )
    return {
        "title": title,
        "description": description,
        "canonical": canonical,
        "image": image,
        "graph": {"@context": "https://schema.org", "@graph": graph},
    }


def xml(kind: str, entries: list[tuple[str, str | None]]) -> Response:
    root = ET.Element(kind, xmlns=NS)
    for url, modified in entries:
        item = ET.SubElement(root, "url" if kind == "urlset" else "sitemap")
        ET.SubElement(item, "loc").text = url
        if modified:
            ET.SubElement(item, "lastmod").text = modified
    return Response(
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
        media_type="application/xml",
    )


def attach(app):
    @app.get("/robots.txt", include_in_schema=False)
    def robots():
        return Response(
            "User-agent: *\nDisallow: /admin\nDisallow: /cuenta\nDisallow: /api/\n"
            f"Disallow: /comprar/\nSitemap: {app.state.settings.origin}/sitemap.xml\n",
            media_type="text/plain",
        )

    def part(bucket: int):
        origin = app.state.settings.origin
        with database(app) as (_, cur):
            cur.execute(
                "SELECT id,nombre,actualizado_en FROM mezcales WHERE "
                + PUBLISHED
                + " AND id>=%s AND id<%s ORDER BY id",
                (bucket * BUCKET, (bucket + 1) * BUCKET),
            )
            rows = cur.fetchall()
        if bucket and not rows:
            raise HTTPException(404, "Sitemap no encontrado.")
        urls = (
            [(origin + "/", None), (origin + "/coleccion", None)] if bucket == 0 else []
        )
        return xml(
            "urlset",
            urls + [(origin + product_path(r), r["actualizado_en"]) for r in rows],
        )

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap():
        with database(app) as (_, cur):
            cur.execute(
                "SELECT DISTINCT id / %s AS bucket FROM mezcales WHERE "
                + PUBLISHED
                + " ORDER BY bucket",
                (BUCKET,),
            )
            buckets = sorted({0, *(r["bucket"] for r in cur.fetchall())})
        if buckets == [0]:
            return part(0)
        return xml(
            "sitemapindex",
            [
                (f"{app.state.settings.origin}/sitemaps/{bucket}.xml", None)
                for bucket in buckets
            ],
        )

    @app.get("/sitemaps/{bucket}.xml", include_in_schema=False)
    def section(bucket: int):
        if not 0 <= bucket <= 2147483647 // BUCKET:
            raise HTTPException(404)
        return part(bucket)
