#!/usr/bin/env bash
# Respaldo completo de PostgreSQL (incluye clientes, pedidos, pagos y eventos).
# Uso: DATABASE_URL=... BACKUP_PASSPHRASE=... ./scripts/pg_respaldo.sh [carpeta_destino]
# Genera un .sql.gz cifrado con GPG (AES256). Guarda la frase y DELIVERY_KEYS en lugares distintos.
set -euo pipefail
: "${DATABASE_URL:?Define DATABASE_URL}"
: "${BACKUP_PASSPHRASE:?Define BACKUP_PASSPHRASE}"
dest="${1:-.}"
mkdir -p "$dest"
file="$dest/valerion_$(date -u +%Y%m%dT%H%M%SZ).sql.gz.gpg"
pg_dump --no-owner --no-privileges --dbname="$DATABASE_URL" \
  | gzip -9 \
  | gpg --batch --yes --pinentry-mode loopback --passphrase "$BACKUP_PASSPHRASE" --symmetric --cipher-algo AES256 -o "$file"
echo "Respaldo creado: $file"
echo "Restauración de prueba: gpg -d $file | gunzip | psql <base_de_prueba>"
