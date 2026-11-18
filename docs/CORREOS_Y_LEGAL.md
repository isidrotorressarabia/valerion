# Correos transaccionales y textos legales

## Correos

| Correo | Cuándo | Cómo se envía |
|---|---|---|
| Verificación de correo | Al registrarse (si hay SMTP) y al pedir reenvío | Inmediato, sin cola; el enlace vence en 3 días |
| Restablecer contraseña | `/cuenta/recuperar` | Inmediato, sin cola; vence en 30 min y es de un solo uso |
| Pago confirmado | Tras conciliar el pago | Cola durable con reintento |
| Orden enviada | Al pasar la orden a «enviada» | Inmediato |
| Aviso al dueño | Por cada pago (`NOTIFY_EMAIL`) | Inmediato |

- **Cola con reintento.** El SMTP ya no se ejecuta dentro de una transacción. Cada envío reserva la fila (arrendamiento de 3 min), envía y registra el resultado. Si falla, el siguiente intento se programa con retroceso exponencial (2, 4, 8… hasta 12 h) y se abandona tras 8 intentos (`python -m services.operations mail` fuerza un reintento manual).
- **Worker.** Corre cada 2 minutos dentro del proceso web (`MAIL_WORKER`, por defecto activo en producción). Es seguro con varias instancias.
- **Migración.** Esta versión sube `commerce_schema` a `5` (columnas nuevas en `correo_pendiente`, `clientes` y la tabla `cliente_tokens`). Con `AUTO_MIGRATE=false` ejecuta `python -m services.operations migrate` antes de arrancar. Las cuentas existentes se marcan como verificadas.
- **Verificación obligatoria.** Con SMTP configurado, comprar exige correo verificado. Sin SMTP no se exige.
- **Entregabilidad.** Gmail SMTP tiene límites diarios y poca reputación como remitente transaccional; para volumen real usa un proveedor con SPF/DKIM/DMARC de tu dominio.

## Textos legales

Las páginas `/privacidad`, `/terminos` y `/envios-devoluciones` son **una base de trabajo, no asesoría jurídica**. Un abogado debe revisarlas antes de operar. Se redactaron para describir lo que el sistema realmente hace (datos recabados, cifrado de entrega, anonimización al eliminar la cuenta, sin analítica de terceros) y conforme a la ley federal de datos personales vigente desde el 21 de marzo de 2025 (autoridad: Secretaría Anticorrupción y Buen Gobierno).

Los datos del responsable y las decisiones comerciales se definen con variables de entorno (`LEGAL_*`, ver `.env.example`). Mientras falten, las páginas muestran `[COMPLETAR: …]` y un aviso de borrador, y el arranque en producción registra `datos_legales_incompletos`.

Decisiones que te corresponden: política de devoluciones (sobre todo botellas abiertas), plazo para reportar daños, cobertura y costo de envío, si los precios incluyen impuestos, y si pedirás identificación al entregar.

### Consentimiento y edad
- Registro: dos casillas obligatorias (mayoría de edad y aceptación de Aviso y Términos). El servidor las exige en producción (`REQUIRE_LEGAL_CONSENT`) y guarda fecha, versión del texto (`LEGAL_VERSION` en `services/legal.py`) y la declaración.
- Compra: casilla de aceptación de Términos y envíos/devoluciones.
- Si cambias sustancialmente los textos, sube `LEGAL_VERSION`.
- La declaración de edad es una autodeclaración; no sustituye una verificación al entregar.

### Derechos ARCO (autoservicio)
`/cuenta/privacidad`: descargar tus datos (JSON) y eliminar la cuenta. Eliminar anonimiza nombre, correo y datos de entrega (también en los pedidos), cancela pedidos pendientes y se bloquea si hay pedidos pagados sin entregar. El registro del pedido (producto, monto, fechas) se conserva sin datos de contacto. Atiende por correo las solicitudes que no puedas resolver en línea dentro de los plazos de la ley (20 días hábiles para responder, 15 más para ejecutar).

## Assets
Se añadió `static/js/account-extra.js`. El manifiesto incluye una copia **sin minificar** para que la app funcione de inmediato; ejecuta `python scripts/build_assets.py` y confirma los cambios en `static/build` para generar la versión minificada (el CI compara ese directorio).
