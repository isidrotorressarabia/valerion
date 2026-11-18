# Auditoría de Valerion · edición 4.0

**Alcance.** Se comparó la aplicación de trabajo dentro del ZIP adjunto con esta edición. Se conservaron la identidad visual, los datos editables, el catálogo y sus rutas públicas, cuentas y administración, publicación de borradores, historial del editor, archivos estáticos, imágenes, respaldos y checkout. En el código de partida se inventariaron 61 combinaciones de método y ruta; las 61 existen en la entrega, que suma nueve entradas nuevas. La comprobación de rutas se complementó con pruebas de comportamiento.

## Hallazgos y correcciones

| Hallazgo observado | Corrección aplicada | Comprobación |
|---|---|---|
| Un aviso de pago podía marcar una orden como pagada sin firma, conciliación del importe o vendedor, o protección de su estado de entrega. El correo decía «Total Pagado» al crear la preferencia. | Webhook firmado con HMAC-SHA256, ventana de tiempo y consulta del pago en la API autenticada. Se comparan referencia, importe en centavos, MXN, vendedor y vinculación con la preferencia. Una transacción registra el recibo único y el evento. El correo se encola tras el pago confirmado. | Se rechazaron firmas inválidas y antiguas, importes, monedas, vendedores y referencias ajenos. Un reenvío conservó el envío ya avanzado y generó un único recibo. Se probó el reintento SMTP con transporte simulado. |
| La compra aceptaba un producto solicitado directamente por ID, sin comprobar publicación o disponibilidad ni token CSRF de formulario. Un doble envío podía crear varias órdenes. | Validación de sesión activa, CSRF, producto publicado/disponible, dirección y precio de base de datos. Se reutiliza la orden y su preferencia ante reenvíos de la misma intención de compra. | Pruebas de borradores, agotados, consultas sin venta, origen ajeno, omisión de CSRF y fallo de proveedor seguido de reintento. |
| La instalación limpia carecía de campos que el checkout consultaba; el perfil no permitía registrar el domicilio solicitado y faltaba la plantilla de ventas. | Migración transaccional, formulario de entrega y panel de ventas. AES-256-GCM vincula cada domicilio cifrado con su tabla y registro. Los cambios de perfil conservan la dirección histórica de la compra. | Se migraron datos heredados, se repitió la migración y se rotó la clave. Cambiar la vinculación del cifrado provocó fallo de autenticación. Un segundo cliente no pudo leer los pedidos del primero. |
| El sitemap enumeraba borradores y construía enlaces a partir del host de la petición. Las páginas privadas podían heredar canónicas y metadatos públicos. | Origen configurado, hosts explícitos, consulta de publicación en XML y en detalle. Canónicas limpias, redirecciones 301 y `noindex` en rutas privadas. | Se comprobó un borrador por ID y slug, un encabezado Host ajeno y parámetros de seguimiento en la ficha. |
| El límite de cuerpo dependía de Content-Length. El despliegue original confiaba en cabeceras reenviadas desde cualquier IP. | Límite sobre bytes efectivamente recibidos, límites diferenciados, origen y CORS explícitos, CSP, HSTS en producción, credenciales en cookies HttpOnly y Secure y revocación en servidor. Acceso al esquema y DML separables. | Se envió un cuerpo fragmentado sin Content-Length y se rechazó al superar el límite. Se probaron CSRF, orígenes y separación de roles. |
| El endpoint público de detalle del API devolvía 405 y el restaurador rechazaba incluso instalaciones con la tabla de órdenes vacía. | Se recuperó el GET público documentado y se protegió la restauración según la presencia de ventas o clientes. Se mantienen hashes de respaldo, transacciones y secuencias. | La suite de regresión del catálogo, respaldos y recuperación volvió a pasar. |
| El análisis de paquetes señaló avisos en Pillow 12.3.0, usado para procesar imágenes subidas. | Actualización a Pillow 13.0.0. Se mantienen límites de tamaño/píxeles, lista de formatos, decodificación y recodificación WebP sin EXIF. El exportador solo descarga del host y cuenta Cloudinary permitidos, sin redirecciones ni proxy de entorno. | La suite recubrió archivos inválidos, duplicados y referencias. La revisión final de 32 dependencias Python con pip-audit devolvió cero avisos conocidos. |

El uso de SQL parametrizado, escape HTML de Jinja, construcción de nodos con `textContent`, hashes de sesiones y varias restricciones de imágenes ya existía en el proyecto. Esta edición conserva esos controles y usa serialización segura para JSON-LD. El alcance de la revisión de paquetes es el entorno Python; los componentes de documentación también se distribuyen localmente, versionados y con sus licencias.

## SEO y rendimiento

- La colección y sus páginas siguientes tienen HTML y enlaces rastreables sin ejecutar JavaScript. Cada ficha expone título, descripción, Open Graph, canónica y JSON-LD con `OnlineStore`, `Product`, `Offer` y `BreadcrumbList`. El precio y la disponibilidad proceden del mismo modelo que la ficha visible. No se generan opiniones o puntuaciones ficticias.
- Un enlace antiguo como `/producto/12` redirige a la ficha vigente con slug. Renombrar un mezcal no rompe sus enlaces históricos. El sitemap se divide por rangos de IDs cuando crece el catálogo. La página personal y los borradores usan respuestas privadas.
- Los 14 archivos propios de CSS y JavaScript minificados suman 93 430 bytes, frente a 96 398 bytes de sus fuentes. Se sirven con nombres derivados del contenido, caché inmutable y compresión HTTP. Los documentos HTML anónimos admiten revalidación mediante ETag y 304. Las respuestas con cookies usan `private,no-store`.
- Se conserva la entrega de imágenes responsivas, con dimensiones declaradas, carga diferida fuera de la primera vista y prioridad alta para la imagen principal. La revisión compartida en PostgreSQL invalida cachés de catálogo, tema y contenido entre instancias. Las consultas de historial y CRM están paginadas e indexadas.

## Patrones y límites operativos

| Área | Implementación |
|---|---|
| Configuración y perímetro | Configuración tipada e inmutable, validación al inicio, middleware ASGI para tamaños y cabeceras, errores sin secretos y correlación de solicitudes. |
| Dominio y persistencia | Servicios separados de catálogo, identidad administrativa, CRM, logística, pagos y SEO. Transacciones PostgreSQL, bloqueos por fila/intención y restricciones únicas para los efectos económicos. |
| Integraciones | Adaptador HTTP con destinos fijos y tiempos de espera para Mercado Pago. Cola transaccional para SMTP. Cifrado autenticado con clave versionada y rotación por lotes. |
| Trazabilidad | Eventos con cliente, orden, actor, canal, hora y campos cambiados. Se guardan estados anterior/nuevo. El historial evita copiar domicilios o teléfonos en el registro de eventos. |
| Despliegue | Dockerfile sin privilegios, paquetes Linux verificados por hash, configuración de Render, guía Cloud Run y migración independiente del servicio web. |

## Evidencia de validación

| Prueba | Resultado |
|---|---|
| Suite incluida originalmente | 20 pasaban y 4 fallaban en el punto de partida. En la entrega pasan las 24. |
| Suite ampliada | **46 aprobadas**, incluidas las 24 originales. Se usó Python 3.12 y PostgreSQL embebido mediante PGlite con conexiones de psycopg y esquemas efímeros. La suite se incluye para repetirla contra PostgreSQL nativo. |
| Navegador Chromium | Se completaron 11 recorridos o verificaciones, entre ellos alta de cuenta, guardar y recargar domicilio, CRM, galería, acceso administrativo, documentación y navegación móvil. La ejecución final registró cero errores JavaScript sin capturar y cero infracciones de CSP en esos recorridos. |
| Carga local | 100 peticiones de lectura, concurrencia 5, un producto publicado y caché caliente. Cero errores, p50 de **12,84 ms** y p95 de **21,28 ms**. Miden respuestas HTTP locales; Core Web Vitals requiere medición en navegador y tráfico del dominio. |
| Servicios reales | Mercado Pago, Cloudinary y SMTP se simularon en integración. No se enviaron correos ni se efectuaron cobros reales. Se prepararon la imagen y el workflow de despliegue; el build Docker y el despliegue en la nube quedan para el entorno del operador. |

Antes de abrir ventas, realiza la migración sobre una copia de tu base y completa una compra sandbox con el vendedor, dominio y webhook definitivos. Verifica recuperación de respaldos y claves, mide Core Web Vitals en el dominio y confirma el sitemap en Search Console. [Guía de migración y producción](PRODUCCION_4.md)
