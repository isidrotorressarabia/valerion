# WEB 3.1 · Cuentas y seguridad

Esta edición combina la infraestructura de WEB 3.0 con el modelo de cuentas de WEB v2.1 SAFE. No contiene endpoints de cobros, checkout ni logística. No activa Mercado Pago mediante variables de entorno.

## Incorporado

- `/cuenta/registro`, `/cuenta/login` y `/cuenta`: nombre, correo y preferencia de contacto informativo, desactivada por defecto.
- Cambio de contraseña que exige la contraseña actual y cierra las sesiones anteriores.
- `/admin/clientes`: búsqueda y paginación, suspensión/reactivación y revocación de sesiones.
- `/admin/sistema`: cierre de todas las sesiones del administrador.
- CSRF en modificaciones autenticadas, control de origen, cookies HttpOnly/SameSite y Secure en producción, contraseñas con scrypt y sal individual, tokens de sesión guardados mediante hash.
- Límite de solicitudes de acceso por IP y por correo, registro limitado por IP y cambios de contraseña limitados por cuenta. La ventana es de cinco minutos. En redes compartidas varios usuarios consumen el mismo límite por IP.
- Respuestas de autenticación sin revelar si el usuario está suspendido; las APIs privadas no exponen hashes, sales ni tokens de sesión.
- Las sesiones de clientes duran ocho horas; un nuevo acceso cierra la sesión previa. Los eventos de perfil y contraseña registran IDs, sin guardar esos datos en los logs.

El correo se guarda como identificador, pero no se verifica su propiedad mediante email. No hay envío automático de mensajes, recuperación de contraseña por correo ni edición de correo desde el perfil. La preferencia de contacto solo se registra; no se envía ninguna campaña.

## Actualización desde SAFE o WEB 3.0

1. Conserva una copia completa de PostgreSQL con pg_dump o una copia de la base en Neon antes de actualizar. Haz primero una prueba en otra base.
2. Mantén DATABASE_URL, CLOUDINARY_URL, ADMIN_USERNAME y ADMIN_PASSWORD en las variables privadas de Render. No copies archivos .env al repositorio.
3. Despliega el contenido de esta carpeta. El arranque conserva la tabla `clientes` de SAFE y agrega el campo de preferencia y CSRF para sesiones. Las cuentas mantienen sus contraseñas existentes. Las sesiones antiguas sin CSRF se revocan; los clientes vuelven a iniciar sesión.
4. Las tablas de módulos históricos que no se usan en esta edición no se borran ni se exponen por API.
5. Valida el registro, acceso, edición de perfil, cambio de contraseña y suspensión desde una cuenta administradora. Comprueba que un cliente no accede al panel administrativo.

## Alcance de los respaldos

El ZIP del panel sigue siendo un respaldo del catálogo, contenido, diseño, imágenes registradas e historial de la versión 3.0. **No incluye clientes, hashes de contraseñas ni tablas de módulos históricos**. Usa pg_dump para una copia completa de la base, guárdala en almacenamiento privado y protegido, y prueba su recuperación en una base separada. El exportador del sitio no sustituye esa copia completa.

No ejecutes restaurar.py sobre una instalación con datos de clientes o módulos históricos: ese restaurador solo reemplaza las tablas del sitio y puede entrar en conflicto con relaciones existentes. Usa la recuperación de PostgreSQL para ese escenario. El restaurador ahora bloquea ese uso antes de modificar datos.

## Validación de esta edición

Se ejecutaron ocho pruebas nuevas de cuentas: perfil/contraseña, separación de roles/suspensión, límite de intentos/origen, aislamiento de cuentas/cierre de sesiones, migración de sesiones antiguas, revocación administrativa/ausencia de checkout y validación de datos. Las ocho aprobaron; la última verifica que la restauración del sitio no toque instalaciones con cuentas.

La suite combinada obtuvo 22 aprobadas y un fallo del adaptador de PostgreSQL embebido al procesar ROLLBACK en una prueba heredada. No se considera validada esa prueba en este entorno: ejecuta la suite completa en PostgreSQL 16 nativo mediante el workflow de GitHub antes de producción. Se conserva el test que detecta el fallo; no se omite ni se cambia su resultado.

Se verificó además el flujo completo de perfiles en Chromium: registro, edición, preferencias, cambio de contraseña, acceso y suspensión administrativa. En escritorio (1366×900) y móvil (390×844), sin errores JavaScript ni desbordamiento horizontal en las vistas comprobadas. Los archivos y resultados de desarrollo no contienen credenciales de producción. Ninguna cuenta remota fue modificada.

Referencia de seguridad: [OWASP, gestión de sesiones](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html). Esta implementación no constituye una auditoría independiente ni incluye MFA.
