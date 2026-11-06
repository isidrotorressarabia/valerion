# Migración y despliegue de Valerion 4.0

## Antes de actualizar una tienda activa

1. Reserva una ventana sin escrituras, detén servicios y jobs de la versión anterior y conserva la revisión de código desplegada. Genera una copia completa de PostgreSQL y comprueba que se puede restaurar en una rama de ensayo. Guarda también las imágenes de Cloudinary y las fotografías antiguas en `data/uploads`. El respaldo del panel exporta siete tablas del sitio. Las cuentas, pedidos y entregas se recuperan desde PostgreSQL.
2. Configura una clave AES de 32 bytes por entorno. En desarrollo nuevo, `python configurar.py` la crea. Para una instalación existente genera el valor una sola vez desde una terminal privada. Guarda la salida en el almacén de secretos y configura `DELIVERY_KEYS`. Conserva las claves antiguas junto con su periodo de uso, separadas de los respaldos. Evita imprimir secretos en los registros del despliegue.

   ```powershell
   python -c "import base64,secrets;print('v1:'+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
   ```

3. En ensayo ejecuta `python -m services.operations migrate` con la identidad de migración. El proceso usa una transacción y un bloqueo compartido entre instancias. Crea el esquema comercial, conserva los IDs, incorpora nombres históricos cuando existe el producto y cifra las columnas heredadas disponibles. Copia dirección, CP, ciudad, teléfono y coordenadas de clientes, y dirección de envío, teléfono y coordenadas de órdenes. Después retira las columnas de origen; cualquier error revierte la transacción. Si una vista externa depende de una columna retirada, PostgreSQL detiene la migración: adapta esa vista antes de repetir. Un control cifrado vincula la base con el juego de claves.
4. Compara recuentos e IDs antes/después. Con una cuenta autorizada revisa domicilios heredados, órdenes antiguas, productos publicados/borradores y archivos `/media/`. Verifica roles y que el respaldo del panel se sigue descargando. Para un historial antiguo que ya carezca del nombre o la dirección, la aplicación muestra únicamente los datos recuperables.
5. Repite en producción tras validar el ensayo. Configura `AUTO_MIGRATE=false` en el servicio web y conserva el comando de migración en un job separado. Un cambio de huella del administrador al actualizar requiere que vuelva a iniciar sesión. Ante una reversión, detén el tráfico y restaura código **y** base compatibles como una unidad. Si ya hubo compras nuevas, concilia esas operaciones con el proveedor antes de cualquier restauración.

## Configuración del servicio

| Variable | Contrato |
|---|---|
| `APP_ENV`, `PUBLIC_BASE_URL` | En producción usa `production` y el origen público HTTPS exacto de la tienda, sin rutas. Se usa para enlaces canónicos, cobros, robots y CORS. |
| `ALLOWED_HOSTS` | Dominios adicionales explícitos separados por coma, incluidos los de la plataforma si se utilizan. El origen canónico ya está autorizado. |
| `DATABASE_URL` | PostgreSQL 16+ con TLS. En producción se exige un modo TLS y se verifica nombre y certificado incluso cuando una URL heredada usa `sslmode=require`. Se usan las CA del sistema o `sslrootcert` explícito para una CA privada. |
| `DELIVERY_KEYS` | Lista `identificador:clave_base64url` separada por comas. La primera clave cifra nuevas escrituras. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Administrador del negocio y contraseña propia de 12 a 256 caracteres. Mantén el acceso al panel restringido también en el perímetro si tu operación lo permite. |
| `CLOUDINARY_URL`, `CLOUDINARY_FOLDER` | Cuenta de medios y carpeta de la instalación existente. Solo las imágenes registradas como propias están sujetas a borrado desde el panel. |
| `MERCADOPAGO_ACCESS_TOKEN`, `MERCADOPAGO_WEBHOOK_SECRET`, `MERCADOPAGO_COLLECTOR_ID` | Habilitan Checkout Pro. Usa el token, firma y número del vendedor de la **misma cuenta y entorno**. El catálogo puede funcionar con la integración de cobros deshabilitada. |
| `SMTP_EMAIL`, `SMTP_PASSWORD` | Remitente y credencial de aplicación Gmail para confirmaciones mediante SMTP con TLS. |
| `DB_POOL_MAX`, `AUTO_MIGRATE` | Pool de 1–10 conexiones por proceso, 5 por defecto. Migración automática en desarrollo. |
| `EDGE_SHARED_SECRET` | Opcional. Secreto de al menos 32 caracteres para autenticar las cabeceras del balanceador si necesitas limitar por IP real. |
| `SEED_DEMO` | Usa `false` para producción. |

## Google Cloud Run

1. Usa un registro de Artifact Registry, PostgreSQL administrado con backups cifrados y recuperación a un punto en el tiempo, y Secret Manager. Asigna acceso de lectura únicamente a los secretos que necesita cada identidad. La aplicación cifra los campos de logística. Para nombres, correos y el resto de la base, el cifrado físico del volumen, WAL y respaldos depende de la configuración del proveedor.
2. Usa una identidad PostgreSQL para migraciones con permisos de esquema y otra para el servicio web con SELECT/INSERT/UPDATE/DELETE y uso de las secuencias que necesita. Mantén la propiedad de tablas y triggers en el rol de migración. Limita `cliente_eventos` a SELECT e INSERT para el rol web; los triggers también bloquean UPDATE, DELETE y TRUNCATE. Envía registros operativos a un destino con retención protegida para detectar intervenciones de propietarios de la base.
3. Construye el Dockerfile para Linux amd64. Escucha en `0.0.0.0:$PORT`, usa un UID sin privilegios y verifica hashes de los paquetes Python. El contenido dinámico vive en PostgreSQL y las nuevas cargas en Cloudinary. Si conservas imágenes `/media/`, cópialas a almacenamiento persistente, móntalo en `/app/data/uploads` o migra sus referencias a Cloudinary antes de pasar tráfico. El contenedor excluye uploads locales de la imagen.
4. Define el origen público, credenciales y secretos tanto en el job como en el servicio. Ejecuta el job de migración y comprueba su salida antes del cambio de tráfico. Ejemplo de comandos para una infraestructura **ya provisionada**. Sustituye los valores de ejemplo y añade los secretos de pagos/correo si se usan. En PowerShell, cada comando se ejecuta en una sola línea.

   ```text
   gcloud builds submit --tag REGION-docker.pkg.dev/PROYECTO/REPOSITORIO/valerion:4.0 .
   gcloud run jobs deploy valerion-migrar --image REGION-docker.pkg.dev/PROYECTO/REPOSITORIO/valerion:4.0 --region REGION --service-account CUENTA_MIGRACION --command python --args=-m,services.operations,migrate --set-env-vars APP_ENV=production,PUBLIC_BASE_URL=https://DOMINIO --set-secrets DATABASE_URL=valerion-db-migrar:1,DELIVERY_KEYS=valerion-entrega:1,ADMIN_PASSWORD=valerion-admin:1,CLOUDINARY_URL=valerion-medios:1
   gcloud run jobs execute valerion-migrar --region REGION --wait
   gcloud run deploy valerion --image REGION-docker.pkg.dev/PROYECTO/REPOSITORIO/valerion:4.0 --region REGION --service-account CUENTA_WEB --port 8080 --concurrency 20 --max-instances 4 --allow-unauthenticated --ingress internal-and-cloud-load-balancing --set-env-vars APP_ENV=production,PUBLIC_BASE_URL=https://DOMINIO,AUTO_MIGRATE=false,DB_POOL_MAX=5 --set-secrets DATABASE_URL=valerion-db-web:1,DELIVERY_KEYS=valerion-entrega:1,ADMIN_PASSWORD=valerion-admin:1,CLOUDINARY_URL=valerion-medios:1
   ```

5. Configura el balanceador HTTPS, certificado, DNS y redirección HTTP→HTTPS. El servicio de ejemplo requiere el balanceador para recibir tráfico público. Usa límites de conexiones que respeten el presupuesto PostgreSQL: con cuatro instancias, un proceso y pool 5, reserva hasta 20 conexiones para la web además de jobs. Ajusta memoria, concurrencia y máximo de instancias con una carga representativa. `/health` comprueba el proceso y `/ready` hace una consulta a PostgreSQL.
6. Para una política por IP real, configura el balanceador para sobrescribir `X-Valerion-Client-IP` con su variable verificada `{client_ip_address}` y `X-Valerion-Edge` con el secreto compartido. Guarda el mismo secreto en `EDGE_SHARED_SECRET` en el servicio. Uvicorn ignora las cabeceras de proxy genéricas. Sin esta integración toma la IP del socket, por lo que varios clientes detrás de un proxy pueden compartir cuota. Aplica protección DDoS y límites volumétricos en Cloud Armor antes de que las solicitudes lleguen al proceso.
7. Separa staging y producción. Prueba el flujo completo con cuenta de vendedor/comprador de pruebas: preferencia, pago sandbox, validación de firma reciente y vendedor, actualización exacta de la orden, regreso a Mi cuenta y correo. Activa notificaciones **Webhooks de pagos** en `https://DOMINIO/api/pagos/webhook`. Una integración antigua que solo envíe IPN sin firma requiere configurar Webhooks. El parámetro `external_reference` conserva el ID de la orden. Nuevas preferencias vinculan además la compra mediante `metadata.checkout_key`. Si el proveedor omite esos metadatos, se consulta la orden comercial autenticada y se comprueba que el pago pertenece a la preferencia guardada. Revisa los registros `pago_sin_conciliar` y los eventos `pago.requiere_revision`. Una orden histórica que ya tenía dirección en texto se conserva como instantánea. La corrección administrativa de envío está en `PUT /api/admin/ordenes/{id}/entrega`, con sesión y CSRF válidos; requiere dirección completa al sustituir una instantánea antigua.
8. Programa un job con la misma imagen y `python -m services.operations mail` para reintentar confirmaciones pendientes. Tras la aprobación el webhook registra el pago, la auditoría y la intención de correo en la misma transacción. SMTP puede entregar dos veces si el proceso cae entre la aceptación del mensaje y el commit; se reutiliza un Message-ID estable. Supervisa pendientes e intentos por orden.

## Render y desarrollo

El blueprint conserva el despliegue existente en Render, verifica dependencias por hash y requiere el origen canónico y la clave de entrega. Si usas migraciones externas configura `AUTO_MIGRATE=false` después del job. Cloudinary, Neon, pagos y SMTP se configuran con variables privadas de la plataforma. Valida en staging qué nombre de host usa el monitor de salud y autorízalo si difiere del canónico.

## Rotación y recuperación

1. Añade la clave nueva al principio de `DELIVERY_KEYS` y conserva todas las anteriores. Despliega la configuración en **todos** los escritores antes de recifrar.
2. Ejecuta `python -m services.operations rotate-delivery`. Recifra por lotes de 100, valida la etiqueta de autenticación y actualiza el control de clave al concluir. Es reanudable. Comprueba perfiles y pedidos, y ejecútalo de nuevo hasta obtener cero registros pendientes.
3. Retira de la aplicación la clave antigua cuando ninguna instancia ni fila la use. Conserva una copia protegida para recuperar respaldos anteriores. Las copias previas a la migración pueden contener logística en claro; aplica la política de acceso, cifrado y retención del proveedor a esas copias.
4. El respaldo del sitio se puede restaurar sobre un destino sin clientes ni pedidos. Para recuperar ventas y perfiles usa una copia completa de PostgreSQL y las claves correspondientes, seguida de una conciliación con Mercado Pago.

## Validación del dominio

Envía `/sitemap.xml` a Search Console. Comprueba una ficha publicada y otra retirada, las canónicas, los datos estructurados y el acceso desde móvil. Mide LCP, INP y CLS con tráfico real tras el lanzamiento. Las visitas de laboratorio y las pruebas locales verifican implementación; la indexación y las posiciones se observan en el dominio público.

Referencias oficiales: [contenedor de Cloud Run](https://docs.cloud.google.com/run/docs/container-contract), [ingreso restringido](https://docs.cloud.google.com/run/docs/securing/ingress), [cabeceras del balanceador](https://docs.cloud.google.com/load-balancing/docs/https/custom-headers-global), [notificaciones de Mercado Pago](https://www.mercadopago.com.mx/developers/es/docs/checkout-pro-preferences/additional-settings/optional-notifications), [datos estructurados para comercios](https://developers.google.com/search/docs/appearance/structured-data/merchant-listing).
