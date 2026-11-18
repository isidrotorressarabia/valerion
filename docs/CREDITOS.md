# Créditos visuales y técnicos

- `static/img/emblem.svg`, `bottle.svg`, `hero.svg` y `placeholder.svg`: composiciones vectoriales originales realizadas para este proyecto. Son conceptos visuales y no fotografías de una botella comercial.
- Fotografía de campo de agave: **stephan de MARANTHI**, Unsplash. Página de referencia: https://unsplash.com/photos/a-large-field-of-pineapple-trees-on-top-of-a-hill-1SQJZJEnt7w
- URL directa usada por los productos de ejemplo: https://images.unsplash.com/photo-1732043846982-5522e1e2626c?auto=format&fit=crop&w=1000&q=85
- La fotografía aparece identificada por Unsplash como libre bajo su licencia: https://unsplash.com/license
- `agave-reference.jpg` es una copia local de esa fotografía, usada como respaldo cuando la URL externa no carga. Representa un campo en Jalisco; no certifica procedencia, especie ni relación comercial con Valerion.
- Las imágenes incluidas son recursos iniciales; sustituye las portadas por fotografías reales de tus productos desde el administrador.
- Si una URL nueva falla, se muestra `placeholder.svg`. Las URLs externas las carga el navegador. Los archivos subidos desde el equipo sí se procesan con Pillow y se envían como WebP a Cloudinary; las rutas locales antiguas requieren migración.
- `source.unsplash.com/featured/` fue retirado; se utilizan URLs de `images.unsplash.com`.
- No se utilizan logos, botellas ni fotografías de los competidores en la identidad de Valerion.

Documentación técnica utilizada:

- FastAPI, plantillas: https://fastapi.tiangolo.com/advanced/templates/
- FastAPI, ciclo de vida: https://fastapi.tiangolo.com/advanced/events/
- FastAPI, pruebas: https://fastapi.tiangolo.com/tutorial/testing/
