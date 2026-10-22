# VALERION · WEB 3.1 — Perfiles y seguridad

Catálogo y administrador con FastAPI/Jinja, PostgreSQL de Neon y Cloudinary. Preparado para alojarse en Render y mantener el código en GitHub. Conserva el diseño y el editor de la versión anterior. Esta entrega no realiza un despliegue ni conecta las cuentas de producción.

## Novedades 3.1

Perfil de cliente, registro e inicio de sesión, preferencias de contacto, cambio de contraseña y administración de cuentas. Cobros desactivados; sin checkout ni datos de entrega. Consulta [ACTUALIZACION_3_1.md](docs/ACTUALIZACION_3_1.md) para migración, seguridad y resultados de pruebas de esta edición.

Los respaldos ZIP del panel cubren el sitio, no las cuentas ni tablas históricas. Realiza una copia completa de PostgreSQL antes de actualizar.

## Inicio

1. Instala Python 3.12, crea `.venv` e instala `requirements.txt`.
2. Ejecuta `python configurar.py`. Se crea `.env` con una contraseña aleatoria si aún no existe.
3. Completa `DATABASE_URL` y `CLOUDINARY_URL` en `.env`. Usa una rama de Neon para desarrollo, separada de producción.
4. Ejecuta `python -m uvicorn main:app --reload`. Abre `http://127.0.0.1:8000/admin`.

En Windows puedes ejecutar `instalar.bat`; después completa las variables antes de iniciar. No compartas `.env`. En Render configura las variables desde su panel; no subas ese archivo.

## Funciones

- Productos publicados y borradores, categorías, destacados, disponibilidad, búsqueda y paginación en SQL.
- Por producto: portada, hasta 24 imágenes de galería, 40 bloques de texto/imagen y 20 campos de ficha técnica.
- Editor de hasta 40 secciones: agregar, ordenar, ocultar, personalizar colores, composición, tipografía y espaciado. Vista previa, publicación e historial de 10 versiones.
- Contactos, correos, teléfonos, redes sociales y enlaces de WhatsApp con mensaje por producto. El visitante decide si envía el mensaje desde WhatsApp; el servidor no envía mensajes automáticamente.
- Pool PostgreSQL limitado, índices, caché pública con invalidación, imágenes WebP y tamaños de entrega acotados.
- Panel `/admin/sistema`: respaldos, diagnóstico, actividad y eliminación explícita de imágenes sin referencias.
- Respaldos de siete tablas con comprobación SHA-256, opción de incluir archivos de Cloudinary, validación y restauración transaccional.

## Guías

- [Despliegue GitHub → Render + Neon + Cloudinary](docs/DESPLIEGUE.md)
- [Análisis y decisiones de arquitectura](docs/ARQUITECTURA.md)
- [Respaldos y recuperación](docs/RESPALDOS.md)
- [Pruebas realizadas y límites](docs/VALIDACION.md)
- [Operación y crecimiento](docs/ESTRATEGIA.md)

## Pruebas

```bash
pip install -r requirements-dev.txt
# TEST_DATABASE_URL debe apuntar a una base PostgreSQL exclusiva de pruebas.
python -m pytest -q
python scripts/verificar_entrega.py
python scripts/carga.py --url http://127.0.0.1:8000 --requests 100 --concurrency 5
```

GitHub Actions ejecuta la integración contra PostgreSQL 16. Cada prueba crea y elimina su propio esquema. Cloudinary se simula en las pruebas: no consume créditos ni elimina archivos reales.

El blueprint usa `plan: free`, un proceso y ningún disco. Los límites y la suspensión de servicios gratuitos siguen aplicando. No se promete una cifra de usuarios simultáneos sin medir el despliegue real.
