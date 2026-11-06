# Despliegue web

## 1. Preservar lo existente

Antes de actualizar un sitio activo, exporta sus tablas con `pg_dump` o crea una rama/copia en Neon y conserva las imágenes de Cloudinary. El ZIP anterior tenía configuración privada: esta entrega la excluye. Si aquel archivo se compartió fuera de un entorno de confianza, cambia ADMIN_PASSWORD y rota las credenciales de Neon y Cloudinary en sus paneles. No se han usado ni rotado esas credenciales durante este trabajo.

## 2. Neon

Crea o selecciona el proyecto y la base PostgreSQL. Para el servidor copia la cadena **pooled** (host con `-pooler`) y conserva `sslmode=require` o una verificación TLS más estricta. Usa una rama distinta para pruebas. La aplicación necesita permisos para crear/alterar sus tablas e índices en el esquema elegido.

Al iniciar, aplica migraciones aditivas sobre las tablas existentes; conserva IDs, productos, contenido y documentos de diseño. No cambia PostgreSQL por otra base. El índice opcional de búsqueda usa `pg_trgm`; si la extensión no está disponible, la búsqueda funciona sin ese índice. Verifica su existencia si el catálogo crece.

No conviertas una base SQLite renombrándola: esta versión espera PostgreSQL. Los respaldos anteriores en JSON o SQLite no son el formato WEB 3.0; requieren una migración separada.

## 3. Cloudinary

Copia `CLOUDINARY_URL` desde tu cuenta (incluye API key, secret y cloud name) a las variables privadas de Render. Mantén `CLOUDINARY_FOLDER=valerion_catalogo` si actualizas la misma instalación. Usa cuentas/carpetas distintas para staging y producción.

Las cargas se realizan desde el servidor. No necesitas un preset unsigned ni exponer API_SECRET en JavaScript. Se conserva `public_id` para las cargas nuevas. Las imágenes anteriores continúan mostrándose por URL, pero no se eliminan automáticamente desde el panel porque no tienen registro de propiedad.

Las antiguas rutas `/media/` dependen de archivos locales: vuelve a cargar esas imágenes desde el administrador para migrarlas a Cloudinary antes del despliegue. `external_references` del respaldo permite detectarlas. Las imágenes estáticas incluidas en `static/img` forman parte del repositorio.

## 4. GitHub

Descomprime el proyecto y coloca su contenido en la raíz de un repositorio, preferentemente privado. Revisa `python scripts/verificar_entrega.py` y `git status` antes de subirlo. No agregues `.env`, `.venv`, bases, respaldos ni fotografías privadas. `.gitignore` no quita archivos que ya estén versionados: si ocurrió, retíralos del índice, rota secretos y revisa el historial.

El workflow `tests.yml` crea PostgreSQL 16 efímero y ejecuta las pruebas. No usa secretos de producción. Activa las comprobaciones requeridas para tu rama principal si deseas bloquear cambios que no pasan las pruebas.

## 5. Render

Desde Render crea un Blueprint conectado al repositorio y selecciona `render.yaml`. Revisa que el servicio esté en **Free**, sin disco. Si el proyecto está dentro de una subcarpeta del repositorio, configura esa carpeta como Root Directory o mueve el contenido a la raíz.

| Variable | Valor |
|---|---|
| `DATABASE_URL` | URL pooled de Neon con TLS |
| `CLOUDINARY_URL` | Credencial privada de Cloudinary |
| `ADMIN_USERNAME` | Usuario elegido |
| `ADMIN_PASSWORD` | Contraseña propia de 12–256 caracteres |
| `APP_ENV` | `production` |
| `COOKIE_SECURE` | `true` |
| `SEED_DEMO` | `false` |
| `DB_POOL_MAX` | `5` inicialmente; rango 1–10 |
| `CLOUDINARY_FOLDER` | Carpeta estable y exclusiva de la instalación |
| `ALLOWED_HOSTS` | Opcional: dominio.onrender.com,dominio.com |

El comando incluido usa un worker, límite de 64 conexiones concurrentes HTTP y encabezados de proxy para reconocer HTTPS. `--forwarded-allow-ips '*'` está destinado a Render detrás de su proxy; no lo copies a un servidor expuesto directamente. En otros entornos confía solo en las IP del proxy. El bloqueo de acceso usa la IP que proporciona ese proxy.

`/health` comprueba el proceso sin consultar Neon; `/ready` verifica la base de datos. Render usa `/health`, de modo que los controles de vida no mantienen consultas constantes a Neon. Un fallo de base se muestra como error temporal y debe revisarse con `/ready` y los logs.

## 6. Verificación en staging antes del cambio

1. Confirma que GitHub Actions pasa con PostgreSQL nativo.
2. Abre inicio, catálogo, producto y administrador en escritorio y móvil.
3. Crea un borrador, carga una imagen real en tu cuenta de pruebas y publica un producto.
4. Publica una sección/contacto y prueba los enlaces de correo, teléfono y WhatsApp.
5. Descarga datos e imágenes; valida y restaura una copia en otra base de pruebas.
6. Reinicia Render y comprueba que los cambios persisten.
7. Ejecuta carga moderada en staging y observa latencia, errores, memoria y consumo de los proveedores.

No uses una base de producción como `TEST_DATABASE_URL`. Mantén el despliegue de prueba aislado. Si falla la migración, vuelve al código anterior y a la copia previa según lo que haya cambiado; no improvises borrados de tablas.

## Fuentes oficiales

Consultadas para esta entrega: [Render Free](https://render.com/docs/free), [Blueprint](https://render.com/docs/blueprint-spec), [Python en Render](https://render.com/docs/python-version), [Neon pooling](https://neon.com/docs/connect/connection-pooling), [Cloudinary upload](https://cloudinary.com/documentation/image_upload_api_reference), [PostgreSQL en GitHub Actions](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).

Render Free puede suspender el proceso tras 15 minutos de inactividad y su sistema de archivos es efímero. Las cuotas de los proveedores cambian: revisa sus paneles; esta configuración no elimina esos límites ni asegura disponibilidad continua.
