# Análisis de WEB 3.0

El proyecto recibido ya enviaba imágenes a Cloudinary y usaba PostgreSQL. Sin embargo, abría conexiones por operación y conservaba pruebas, restauración y blueprint de SQLite. El bloqueo de intentos fallidos se revertía al lanzar la excepción dentro de la transacción; el tema del borrador no exigía sesión. Se corrigieron ambos puntos.

| Componente | Implementación y efecto |
|---|---|
| GitHub | Código, revisión y pruebas en PostgreSQL efímero |
| Render | FastAPI/Jinja, un worker; sin persistencia local de cargas nuevas |
| Neon | Productos, contenido, borradores, historial, sesiones, metadatos de imágenes y auditoría |
| Cloudinary | Archivos de imágenes y entrega por CDN |
| Pool | Psycopg 3, mínimo 0, máximo 5 por defecto, tiempo de espera 12 s, cola máxima 20, conexiones ociosas 60 s |
| Consultas | Parámetros SQL; búsqueda, categorías, orden y paginación en servidor; máximo 48 registros públicos por petición |
| Índices | Precio, nombre, categoría y destacados publicados; expiración de sesiones y auditoría; trigramas opcionales |
| Caché | En memoria del proceso: contenido/diseño 120 s y catálogo/categorías 15 s; tamaño limitado y copias independientes |
| Imágenes | Validación hasta 8 MB/20 MP, orientación EXIF, reducción a 2400 px, WebP calidad 85, metadatos eliminados |
| Entrega | Tamaños 160/640/1280, f_auto/q_auto solo en URLs Cloudinary convencionales; otras URLs se conservan |
| Sesiones | Cookie HttpOnly/SameSite, CSRF, validación de origen y huella de credenciales; bloqueo tras cinco fallos por IP durante cinco minutos |
| Respaldo | Siete tablas, manifiesto, integridad SHA-256 y archivos Cloudinary opcionales |

La caché se invalida tras cambios del panel. Un contador impide que una lectura en curso vuelva a insertar datos antiguos después de invalidarla. Con varios procesos habría cachés independientes: se mantiene un solo worker; para escalado horizontal usa invalidación compartida y vuelve a medir.

Las listas no incluyen los bloques extensos ni galería completa: el editor recupera el producto individual antes de editar o duplicar. Los borradores nunca aparecen en la API pública. La API de lista cambió de arreglo a `{items,total,page,page_size,pages,...}`; las interfaces incluidas ya están adaptadas. Cualquier integración externa debe actualizarse.

Se conserva una huella SHA-256 de la imagen normalizada y un public_id determinista. Las cargas repetidas reutilizan registros; `overwrite=false` evita sobrescribir activos existentes. La eliminación es explícita, requiere 24 horas de antigüedad y revisa productos, contenido, borrador, publicación e historial. Un bloqueo transaccional compartido serializa cambios de referencias y limpieza. No es una transacción distribuida con Cloudinary: si falla la red o la base tras una operación remota puede ser necesario reconciliar un archivo; los errores no eliminan productos.

Los logs registran ruta de plantilla, duración, estado e ID de petición; no cuerpos, consultas, contraseñas ni DSN. No se publica un traceback al visitante. El diagnóstico privado muestra el pool, cachés y últimas 50 acciones. No equivale a un sistema externo de alertas ni a un registro de auditoría inmutable.

Se conservó FastAPI/Jinja porque cambiar la interfaz de framework no resuelve el coste de conexiones, consultas o transferencia. El sitio continúa siendo catálogo con enlaces de compra/consulta; no agrega pasarela de pagos, inventario transaccional, pedidos ni múltiples roles de administrador.
