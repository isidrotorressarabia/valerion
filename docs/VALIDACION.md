# Validación histórica de WEB 3.0

Para la edición actual consulta ACTUALIZACION_3_1.md. Los resultados siguientes corresponden a la base 3.0, antes de integrar las cuentas.

Fecha: 21 de octubre de 2026. Las pruebas se ejecutaron sobre datos sintéticos, sin usar las credenciales del ZIP original ni acceder a las cuentas de producción.

## Pruebas automatizadas

Se ejecutaron 16 casos pytest con FastAPI TestClient y PostgreSQL embebido PGlite por protocolo TCP: catálogo paginado, búsqueda literal, categorías, destacados, borradores, detalle con galería, invalidación de caché, bloqueo de acceso, cierre de sesión, CSRF/origen, privacidad del tema, publicación/conflictos/historial/contacto, entradas inválidas, normalización/deduplicación de imágenes, errores de Cloudinary, protección frente a eliminación, respaldo/restauración/secuencias, reversión de transacciones, migraciones repetibles, pool y salud. Incluyen copias con bytes y recarga de imágenes con mapeo de URLs usando servicios simulados.

Resultado de la ejecución completa secuencial: 16 aprobadas. Hay un aviso de deprecación de Starlette por el adaptador httpx de pruebas; no afecta los endpoints. Cloudinary fue simulado; no se realizaron cargas, descargas ni eliminaciones en una cuenta real.

PGlite ejecuta PostgreSQL en WebAssembly y multiplexa conexiones. En una de las ejecuciones se observó un error del adaptador al procesar ROLLBACK; la ejecución aislada pasó. No se usa PGlite en producción ni se considera esta prueba una certificación de concurrencia de PostgreSQL nativo. El workflow incluido ejecuta la misma suite con PostgreSQL 16 nativo; debe pasar en tu repositorio antes del despliegue. No se ha ejecutado GitHub Actions desde este entorno.

## Navegador

Chromium headless: escritorio 1440×1000 y móvil 390×844. Se verificaron navegación de catálogo, página siguiente, búsqueda, categoría, inicio de sesión, paginación del administrador, recuperación de galería al editar, guardado, búsqueda del cambio, diagnóstico y descarga real del ZIP desde el panel. Sin errores JavaScript detectados y sin desbordamiento horizontal en las vistas móviles comprobadas. Se inspeccionaron visualmente el sitio y el panel de sistema.

## Carga local

Catálogo de 100 productos publicados. Un proceso, pool de una conexión, caché caliente y PostgreSQL embebido en el mismo equipo. Cada corrida envió 100 solicitudes repartidas entre inicio y tres consultas de catálogo. No incluye latencia de internet, Neon, Cloudinary, TLS, memoria de Render ni arranque en frío.

| Concurrencia | Solicitudes | Errores | p50 | p95 |
|---|---:|---:|---:|---:|
| 1 | 100 | 0 | 1.82 ms | 3.21 ms |
| 5 | 100 | 0 | 11.11 ms | 16.88 ms |
| 10 | 100 | 0 | 23.61 ms | 52.81 ms |

Estas cifras solo comprueban el recorrido local y la caché con ese conjunto de solicitudes. No representan la capacidad de Render Free ni permiten prometer visitantes simultáneos. El script reproducible `scripts/carga.py` está incluido para repetir en staging con datos representativos.

## Pendiente en tus cuentas

- Ejecutar CI con PostgreSQL 16 nativo y migrar una copia de tu base actual.
- Validar TLS/URL pooled de Neon y credenciales de Cloudinary.
- Probar carga y eliminación de un archivo real en una carpeta de pruebas.
- Descargar y restaurar un respaldo completo en otra base/cuenta.
- Verificar persistencia tras reiniciar Render, proxy HTTPS, dominios y arranque en frío.
- Rotar credenciales que hayan quedado expuestas en distribuciones anteriores.

La entrega incluye código y configuración; no se ha publicado un sitio ni modificado bases, archivos remotos o repositorios reales.
