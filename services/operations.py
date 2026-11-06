"""Explicit operator entry points for migrations, transactional mail and key rotation."""

import argparse
import asyncio
import os
from psycopg import sql
from infrastructure import database
from services.mail import deliver


def rotate(app) -> int:
    vault = app.state.vault
    changed = 0
    for table in ("clientes", "ordenes"):
        while True:
            with database(app) as (_, cur):
                cur.execute(
                    sql.SQL("""SELECT id,entrega_segura FROM {} WHERE entrega_segura IS NOT NULL
                                      AND split_part(entrega_segura,'.',1)<>%s ORDER BY id LIMIT 100 FOR UPDATE""").format(
                        sql.Identifier(table)
                    ),
                    (vault.active,),
                )
                rows = cur.fetchall()
                for row in rows:
                    binding = f"{table}:{row['id']}"
                    value = vault.seal(
                        binding, vault.open(binding, row["entrega_segura"])
                    )
                    cur.execute(
                        sql.SQL("UPDATE {} SET entrega_segura=%s WHERE id=%s").format(
                            sql.Identifier(table)
                        ),
                        (value, row["id"]),
                    )
                changed += len(rows)
            if not rows:
                break
    with database(app) as (_, cur):
        cur.execute(
            "SELECT value FROM metadata WHERE key='delivery_key_check' FOR UPDATE"
        )
        data = vault.open("metadata:delivery_key_check", cur.fetchone()["value"])
        cur.execute(
            "UPDATE metadata SET value=%s WHERE key='delivery_key_check'",
            (vault.seal("metadata:delivery_key_check", data),),
        )
    return changed


async def run(command: str) -> None:
    os.environ["AUTO_MIGRATE"] = "true" if command == "migrate" else "false"
    from main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        if command == "rotate-delivery":
            print("Registros recifrados:", rotate(app))
        elif command == "mail":
            if (
                not app.state.settings.smtp_email
                or not app.state.settings.smtp_password
            ):
                raise RuntimeError(
                    "Configura el remitente SMTP antes de procesar la cola."
                )
            with database(app) as (_, cur):
                cur.execute(
                    "SELECT orden_id FROM correo_pendiente WHERE enviado_en IS NULL ORDER BY intentos,orden_id LIMIT 100"
                )
                ids = [r["orden_id"] for r in cur.fetchall()]
            for order_id in ids:
                deliver(app, order_id)
            print("Confirmaciones procesadas:", len(ids))
        else:
            print("Migración verificada.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "rotate-delivery", "mail"])
    asyncio.run(run(parser.parse_args().command))
