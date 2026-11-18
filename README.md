# Valerion · Tienda y administración

Versión 4.0 del proyecto adjunto. Incluye sitio público, catálogo, editor visual con publicación e historial, carga de imágenes, cuentas, CRM, Mercado Pago, ventas y respaldos. El backend usa FastAPI, Jinja y PostgreSQL. El frontend ya viene compilado y funciona sin Node.js.

## Ejecutar en Windows y VS Code

Desde la carpeta que contiene `main.py`, usa Python 3.12 y PowerShell.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe configurar.py
```

Abre el archivo local `.env` y completa `DATABASE_URL` con una base PostgreSQL 16 o posterior de desarrollo, y `CLOUDINARY_URL` con la credencial de la cuenta de imágenes. El configurador genera la contraseña administrativa y una clave aleatoria de cifrado. Para una instalación existente, conserva su configuración y sigue el procedimiento de migración antes de arrancar.

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Abre [la tienda](http://127.0.0.1:8000), [administración](http://127.0.0.1:8000/admin) o [Mi cuenta](http://127.0.0.1:8000/cuenta). También puedes usar `instalar.bat` y F5. Para comprar, configura el proveedor, publica un producto disponible y completa dirección, ciudad y código postal desde Mi cuenta.

## Operación

| Tarea | Entrada |
|---|---|
| Editar productos, imágenes y disponibilidad | `/admin` |
| Editar textos, diseño, contactos y publicar | `/admin/contenido`, `/admin/estudio` |
| Ver pedidos, domicilio y actualizar estado | `/admin/ventas` |
| Consultar clientes, compras y registrar interacciones | `/admin/clientes` |
| Consultar perfil, domicilio e historial propio | `/cuenta` |
| Diagnóstico, imágenes y respaldo del sitio | `/admin/sistema` |
| Documentación de la API | `/docs`, `/redoc` |
| Colección paginada, sitemap y robots | `/coleccion`, `/sitemap.xml`, `/robots.txt` |

Lee [la auditoría y los resultados de validación](docs/AUDITORIA_4.md) y [la guía de migración y producción](docs/PRODUCCION_4.md). La segunda cubre TLS, Secret Manager, roles de PostgreSQL, Cloud Run, Render, rotación de claves y recuperación.

## Repetir la validación

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
# Configura TEST_DATABASE_URL para una base PostgreSQL exclusiva de pruebas.
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\verificar_entrega.py
```

Cada caso de integración utiliza un esquema nuevo y lo elimina al terminar. Después de editar JavaScript o CSS ejecuta `python scripts/build_assets.py` para reconstruir las copias minificadas con huella de contenido. GitHub Actions configura PostgreSQL 16, ejecuta las pruebas y contiene una comprobación del arranque del contenedor. Los documentos con referencias a WEB 3.0 y 3.1 describen esas ediciones.
