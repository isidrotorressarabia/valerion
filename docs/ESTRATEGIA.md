# Operación y crecimiento

Empieza con un worker y un pool máximo de cinco conexiones. No aumentes el pool para ocultar consultas lentas: revisa primero índices, caché y latencias. PostgreSQL y Cloudinary conservan los datos independientemente del proceso de Render; los respaldos siguen siendo necesarios.

Observa en Render errores 5xx, latencia, memoria y reinicios; en Neon, almacenamiento y consumo de cómputo; en Cloudinary, almacenamiento, transformaciones y transferencia. Las imágenes se transforman a un conjunto fijo de tamaños para evitar variantes ilimitadas. Las imágenes antiguas que solo tienen URL no se consideran eliminables por el panel.

Ejecuta `scripts/carga.py` en tu entorno de pruebas con concurrencias 1, 5 y 10. Compara p50, p95 y porcentaje de errores con un catálogo representativo. La medición calienta `/health`; mide por separado la primera visita tras la suspensión. No confundas solicitudes por segundo con visitantes simultáneos.

El registro de auditoría conserva acciones sin eliminación automática; revisa su crecimiento y archívalo mediante respaldos según tu política de retención. El panel muestra las últimas 50. La vista de imágenes muestra hasta 500 cargas; para volúmenes mayores añade paginación de activos antes de basar toda la operación en ella.

Si necesitas respuesta inmediata en todo momento, evalúa un servicio que no se suspenda. Si aumenta la carga, mide con PostgreSQL nativo, considera índices adicionales a partir de EXPLAIN y una caché compartida antes de usar múltiples workers. No se incluyen promesas de capacidad ni estimaciones de usuarios basadas solo en la cantidad de productos.
