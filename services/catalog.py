"""Validated catalogue models, public representations and product persistence."""

from datetime import datetime, timezone
from decimal import Decimal
import re
import json
from typing import Any
from psycopg import Cursor
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator
from cms import ProductExtra
from content import FIELD_MAP
from services import seo

MEDIA_NAME = re.compile(r"^[a-f0-9]{32}\.webp$")
STATIC_IMAGES = {
    "/static/img/" + name
    for name in (
        "emblem.svg",
        "hero.svg",
        "bottle.svg",
        "placeholder.svg",
        "agave-reference.jpg",
    )
}


def image_reference(value: str) -> str:
    if value in STATIC_IMAGES or (
        value.startswith("/media/") and MEDIA_NAME.fullmatch(value[7:])
    ):
        return value
    url = TypeAdapter(HttpUrl).validate_python(value)
    if url.scheme != "https" or url.username or url.password:
        raise ValueError("Usa HTTPS o una imagen cargada desde el panel.")
    return str(url)


class ContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valores: dict[str, str] = Field(min_length=1)

    @field_validator("valores")
    @classmethod
    def validate_content(cls, values):
        cleaned = {}
        for key, value in values.items():
            spec = FIELD_MAP.get(key)
            if not spec:
                raise ValueError("Campo de contenido desconocido.")
            value = value.strip()
            if (not value and not spec["optional"]) or len(value) > 4000:
                raise ValueError(f"{spec['label']}: revisa longitud y contenido.")
            cleaned[key] = image_reference(value) if spec["type"] == "image" else value
        return cleaned


class ProductInput(ProductExtra):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    nombre: str = Field(min_length=2, max_length=100)
    precio: Decimal = Field(
        gt=0, le=Decimal("999999.99"), max_digits=8, decimal_places=2
    )
    descripcion: str = Field(min_length=10, max_length=2000)
    link_venta: HttpUrl | None = None
    imagen_url: str = Field(max_length=2083)

    @field_validator("link_venta", mode="before")
    @classmethod
    def empty_link(cls, value):
        return None if value == "" else value

    @field_validator("imagen_url")
    @classmethod
    def image_url(cls, value):
        return image_reference(value)

    @field_validator("link_venta")
    @classmethod
    def secure_url(cls, value):
        if value is not None and (
            value.scheme != "https" or value.username or value.password
        ):
            raise ValueError("Usa una URL HTTPS sin credenciales.")
        return value


class Product(ProductInput):
    ruta: str
    id: int
    creado_en: str
    actualizado_en: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def public_product(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ruta": seo.product_path(row),
        "id": row["id"],
        "nombre": row["nombre"],
        "precio": f"{Decimal(row['precio_centavos']) / 100:.2f}",
        "descripcion": row["descripcion"],
        "link_venta": row["link_venta"],
        "imagen_url": row["imagen_url"],
        "creado_en": row["creado_en"],
        "actualizado_en": row["actualizado_en"],
        **ProductExtra.model_validate_json(row["extra_json"]).model_dump(mode="json"),
    }


def values(product: ProductInput) -> tuple:
    return (
        product.nombre,
        int(product.precio * 100),
        product.descripcion,
        str(product.link_venta) if product.link_venta else None,
        str(product.imagen_url),
    )


def insert_product(cursor: Cursor, product: ProductInput) -> int:
    timestamp = now_iso()
    cursor.execute(
        "INSERT INTO mezcales(nombre,precio_centavos,descripcion,link_venta,imagen_url,creado_en,actualizado_en,extra_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (*values(product), timestamp, timestamp, extra_json(product)),
    )
    return cursor.fetchone()["id"]


def extra_json(product: ProductInput) -> str:
    return json.dumps(
        {k: product.model_dump(mode="json")[k] for k in ProductExtra.model_fields},
        ensure_ascii=False,
    )
