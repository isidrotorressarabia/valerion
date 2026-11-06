# Respaldo y recuperación del sitio

En WEB 3.1 estos respaldos no incluyen cuentas ni tablas históricas externas. Consulta ACTUALIZACION_3_1.md: usa una copia íntegra de PostgreSQL para recuperar instalaciones con esos datos. El restaurador del sitio bloquea esas instalaciones para no hacer una recuperación parcial.

## Crear una copia

En **Sistema y respaldos** elige «Datos y enlaces» o «Datos e imágenes de Cloudinary». También puedes ejecutar:

```bash
python respaldar.py copia_2026_10_21.zip
python respaldar.py copia_con_imagenes.zip --imagenes
python restaurar.py copia_con_imagenes.zip
```

El último comando solo valida: no abre la base ni modifica Cloudinary. El respaldo incluye mezcales, site_content, studio (borrador/publicación), studio_history, metadata, media_assets y audit_log. No incluye contraseñas, variables de entorno, sesiones ni intentos de acceso.

El manifiesto registra conteos y hashes. Con `--imagenes` se descargan las imágenes de la cuenta Cloudinary configurada, tanto referenciadas como registradas sin uso. No sigue redirecciones ni descarga URLs externas arbitrarias. Estas últimas y las rutas `/media/` quedan enumeradas en `external_references`; debes conservarlas o migrarlas por separado. Las imágenes `/static/` se recuperan con el código del repositorio.

Límites del exportador: 50 MB de JSON, 30 MB por imagen y 200 MB de imágenes. Si creces más, utiliza `pg_dump` y una exportación de Cloudinary. Una descarga interrumpida no cuenta como respaldo válido. Los hashes detectan corrupción, pero no demuestran quién creó el archivo: restaura solo copias de confianza.

## Restaurar

1. Valida la copia con `python restaurar.py respaldo.zip`.
2. Prueba primero en otra base. Prepara el esquema iniciando esta versión una vez y deteniendo el servidor.
3. Detén todos los procesos web que usen la base de destino; verifica DATABASE_URL y CLOUDINARY_URL.
4. Ejecuta:

```bash
python restaurar.py respaldo.zip --aplicar --servidor-detenido
```

Se crea una copia previa de los datos en la carpeta del respaldo. Luego una transacción reemplaza las siete tablas, restablece las secuencias y revoca sesiones. Si falla la transacción se revierten los cambios de tablas. Reinicia el servidor; una nueva sesión será necesaria.

De forma predeterminada se restauran referencias a los archivos originales de Cloudinary. Si faltan esos archivos o cambias de cuenta y tu copia incluye los bytes, usa:

```bash
python restaurar.py copia_con_imagenes.zip --aplicar --servidor-detenido --recargar-imagenes
```

Esto carga nuevos archivos sin sobrescribir originales y actualiza las URLs en productos, contenido, borradores e historial, además de registrar los nuevos IDs. No recupera imágenes externas excluidas. Si una recarga o la transacción posterior falla, las cargas ya realizadas pueden permanecer como archivos sin referencias: revisa la carpeta configurada en Cloudinary antes de repetir. La copia previa automática contiene datos y enlaces; conserva por separado una copia previa con imágenes si vas a borrar archivos remotos.

No restaures con usuarios editando. El indicador `--servidor-detenido` es una confirmación del operador, no una comprobación remota de Render. El bloqueo SQL protege las escrituras de esta versión, pero no sustituye detener los servidores ni invalida la caché de otros procesos.

## GitHub

`backup.yml` ofrece ejecución manual en repositorios privados. Configura un entorno `production-backup` con los secretos DATABASE_URL y CLOUDINARY_URL. El resultado se guarda como artefacto privado durante siete días. Descárgalo y conserva otra copia privada fuera de GitHub. No hay una programación automática activada por esta entrega.

Comprueba periódicamente una restauración en otra base y verifica un producto con galería, una sección, contacto, tema e historial. Una copia descargada sin una prueba de recuperación no demuestra que el procedimiento operativo funcione.
