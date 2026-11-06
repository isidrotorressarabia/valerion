"""Transactional upgrade for legacy order/profile records and delivery history."""

from decimal import Decimal
from psycopg import Cursor, sql
from services.privacy import DeliveryVault

FIELDS = ("direccion", "cp", "ciudad", "telefono", "latitud", "longitud")


def _import_columns(
    cur: Cursor, vault: DeliveryVault, table: str, fields: tuple[str, ...]
) -> None:
    cur.execute(
        """SELECT column_name FROM information_schema.columns
                   WHERE table_schema=current_schema() AND table_name=%s""",
        (table,),
    )
    legacy = sorted({r["column_name"] for r in cur.fetchall()} & set(fields))
    if not legacy:
        return
    columns = sql.SQL(",").join(map(sql.Identifier, legacy))
    query = sql.SQL("SELECT id, {} FROM {} WHERE id>%s ORDER BY id LIMIT 250").format(
        columns, sql.Identifier(table)
    )
    last_id = -1
    while True:
        cur.execute(query, (last_id,))
        rows = cur.fetchall()
        if not rows:
            break
        for row in rows:
            last_id = row.pop("id")
            values = {
                k: str(v) if isinstance(v, Decimal) else v
                for k, v in row.items()
                if v is not None
            }
            if values:
                cur.execute(
                    sql.SQL("UPDATE {} SET entrega_segura=%s WHERE id=%s").format(
                        sql.Identifier(table)
                    ),
                    (vault.seal(f"{table}:{last_id}", values), last_id),
                )
    # Remove old plaintext only after copying, inside the same PostgreSQL transaction.
    for name in legacy:
        cur.execute(
            sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(
                sql.Identifier(table), sql.Identifier(name)
            )
        )


def upgrade(cur: Cursor, vault: DeliveryVault) -> None:
    cur.execute("""ALTER TABLE clientes ADD COLUMN IF NOT EXISTS entrega_segura text;
        ALTER TABLE clientes ADD COLUMN IF NOT EXISTS canal_preferido text NOT NULL DEFAULT 'email';
        ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS cliente_id integer REFERENCES clientes(id);
        ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS entrega_segura text;
        ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS actualizado_en text;
        ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS producto_nombre text;
        ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS checkout_key uuid;
        ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS checkout_url text;
        ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS preferencia_id text;
        ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS intento_en timestamptz;
        ALTER TABLE ordenes ADD COLUMN IF NOT EXISTS pago_verificado boolean NOT NULL DEFAULT false;
        CREATE UNIQUE INDEX IF NOT EXISTS ordenes_checkout_key ON ordenes(checkout_key);
        CREATE INDEX IF NOT EXISTS ordenes_cliente ON ordenes(cliente_id,id DESC);
        CREATE INDEX IF NOT EXISTS ordenes_pago ON ordenes(pago_id) WHERE pago_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS account_limits_expiry ON account_limits(expires);
        CREATE TABLE IF NOT EXISTS cliente_eventos(
           id bigserial PRIMARY KEY, cliente_id integer REFERENCES clientes(id), orden_id integer REFERENCES ordenes(id),
           tipo text NOT NULL, canal text NOT NULL, actor text NOT NULL, detalle jsonb NOT NULL DEFAULT '{}',
           fecha timestamptz NOT NULL DEFAULT now());
        CREATE INDEX IF NOT EXISTS eventos_cliente ON cliente_eventos(cliente_id,id DESC);
        CREATE TABLE IF NOT EXISTS pago_recibos(pago_id text PRIMARY KEY, orden_id integer NOT NULL UNIQUE REFERENCES ordenes(id));
        CREATE TABLE IF NOT EXISTS correo_pendiente(orden_id integer PRIMARY KEY REFERENCES ordenes(id),
           enviado_en timestamptz, intentos integer NOT NULL DEFAULT 0);""")
    cur.execute("""INSERT INTO metadata(key,value) VALUES('public_revision','1') ON CONFLICT DO NOTHING;
                   CREATE OR REPLACE FUNCTION bump_public_revision() RETURNS trigger LANGUAGE plpgsql AS $$
                   BEGIN UPDATE metadata SET value=(value::bigint+1)::text WHERE key='public_revision'; RETURN NULL; END $$;
                   CREATE OR REPLACE FUNCTION immutable_crm_event() RETURNS trigger LANGUAGE plpgsql AS $$
                   BEGIN RAISE EXCEPTION 'El historial es de solo inserción'; END $$;
                   CREATE OR REPLACE TRIGGER immutable_events BEFORE UPDATE OR DELETE OR TRUNCATE ON cliente_eventos
                   FOR EACH STATEMENT EXECUTE FUNCTION immutable_crm_event();""")
    for table in ("mezcales", "studio", "site_content"):
        cur.execute(
            sql.SQL("""CREATE OR REPLACE TRIGGER revision_publica AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON {}
                               FOR EACH STATEMENT EXECUTE FUNCTION bump_public_revision()""").format(
                sql.Identifier(table)
            )
        )
    # A sentinel detects an incorrect key even if no legacy columns remain.
    cur.execute("SELECT value FROM metadata WHERE key='delivery_key_check'")
    sentinel = cur.fetchone()
    if sentinel:
        vault.open("metadata:delivery_key_check", sentinel["value"])
    else:
        cur.execute(
            "INSERT INTO metadata(key,value) VALUES('delivery_key_check',%s)",
            (vault.seal("metadata:delivery_key_check", {"v": 1}),),
        )
    _import_columns(cur, vault, "clientes", FIELDS)
    _import_columns(
        cur, vault, "ordenes", ("direccion_envio", "telefono", "latitud", "longitud")
    )
    cur.execute("""UPDATE ordenes o SET producto_nombre=m.nombre FROM mezcales m
                   WHERE o.producto_nombre IS NULL AND o.producto_id=m.id""")
    cur.execute("""INSERT INTO metadata(key,value) VALUES('commerce_schema','4')
                   ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value WHERE metadata.value <> EXCLUDED.value""")


def event(
    cur: Cursor,
    customer: int | None,
    order: int | None,
    kind: str,
    actor: str,
    channel: str = "web",
    **detail: str | list[str],
) -> None:
    from psycopg.types.json import Jsonb

    cur.execute(
        """INSERT INTO cliente_eventos(cliente_id,orden_id,tipo,canal,actor,detalle)
                   VALUES(%s,%s,%s,%s,%s,%s)""",
        (customer, order, kind, channel, actor, Jsonb(detail)),
    )
