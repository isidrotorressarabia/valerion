"""Configuración local sin dependencias; nunca sobrescribe un .env existente."""
from pathlib import Path
import os
import secrets

ROOT = Path(__file__).resolve().parent


def ensure_config() -> Path:
    path = ROOT / ".env"
    if not path.exists():
        password = secrets.token_urlsafe(18)
        content = (
            "# Credenciales locales. No compartir ni incluir en Git.\n"
            f"ADMIN_USERNAME=admin\nADMIN_PASSWORD={password}\n"
            "DATABASE_URL=\nCLOUDINARY_URL=\nCLOUDINARY_FOLDER=valerion_catalogo\nDB_POOL_MAX=5\nAPP_ENV=development\nSEED_DEMO=false\nCOOKIE_SECURE=false\n"
        )
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(content)
            print("Configuración creada. Usuario: admin. Consulta ADMIN_PASSWORD en .env.")
        except FileExistsError:
            pass
    return path


if __name__ == "__main__":
    print(f"Configuración disponible en: {ensure_config()}")
