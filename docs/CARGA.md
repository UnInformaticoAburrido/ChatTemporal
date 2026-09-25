# Carga online de N9

`scripts/load_chat.py` verifica tráfico real REST/WS cifrado con el cliente de
referencia. Es un perfil **online, unidireccional y de bucle cerrado**: cada pareja
espera la confirmación antes del siguiente mensaje. No representa por sí solo
offline, reconexiones, Push, registro o carga máxima sostenible.

El operador aporta el pico esperado, duración y cadencia. La herramienta abre
**dos veces el pico de usuarios indicado**: una pareja por usuario del pico,
con dos cuentas distintas por pareja. Todas esperan una barrera antes de enviar,
y conservan las conexiones hasta que termine el grupo. Cualquier fallo hace
fallar el resultado; un timeout o 429 no se transforma en éxito mediante reintentos.

## Preparar cuentas dedicadas

Usar exclusivamente un entorno y cuentas autorizados para carga. Cada cuenta
debe estar verificada, tener sesión vigente durante toda la prueba y haber
publicado la clave pública correspondiente a su privada. Cada pareja necesita
una conversación active en el modo indicado, sin voto pendiente; esperar el
cierre de los treinta segundos de votación. Para stored, el historial inicial
debe estar vacío. No reutilizar cuentas ni conversaciones entre parejas.

Guardar fuera de Git, por ejemplo `secrets/load_accounts.json`, con permisos 0600:

```json
[
  {
    "conversation_id": "UUID_CANONICO",
    "mode": "stored",
    "sender_token": "ACCESS_TOKEN_EMISOR",
    "recipient_token": "ACCESS_TOKEN_RECEPTOR",
    "sender_private_key": "BASE64URL_PRIVADA_32_BYTES",
    "recipient_private_key": "BASE64URL_PRIVADA_32_BYTES"
  }
]
```

Los valores son marcadores, no credenciales válidas. Se permite combinar parejas
stored y ephemeral; el informe registra la distribución. El proceso comprueba
identidades distintas, claves publicadas y pertenencia/estado antes del tráfico.
No crea cuentas, cambia claves, acepta conversaciones ni altera cuotas del servidor.
No borra mensajes ni cuentas al terminar: el operador gestiona sus datos de prueba
respetando la política de retención. El historial stored debe ser nuevo para otra
ejecución; no se elimina automáticamente para facilitar un reintento.

## Ejecutar

Instalar `python/requirements-dev.lock` en el entorno de pruebas. Desde la raíz,
con las variables de escenario definidas por el operador:

```sh
.venv/bin/python scripts/load_chat.py \
  --base-url "$CHAT_STAGING_ORIGIN" \
  --accounts-file secrets/load_accounts.json \
  --expected-peak "$CHAT_EXPECTED_PEAK" \
  --duration "$CHAT_LOAD_SECONDS" \
  --interval "$CHAT_LOAD_INTERVAL" \
  --output artifacts/n9/load.json
```

`duration` e `interval` están en segundos. El intervalo es el mínimo entre inicios
de mensajes por pareja; si el servidor tarda más, disminuye la tasa efectiva.
TLS se verifica siempre; `--ca-file` permite una CA de staging. HTTP se admite
solo para loopback con `--allow-local-http`, usado por las pruebas automatizadas.
Las credenciales nunca viajan en argumentos del proceso ni en el informe.

## Interpretar y completar la homologación

El informe contiene conexiones máximas, intentos, envíos, confirmaciones,
parejas verificadas y latencia p50/p95/máxima hasta `message.delivered`, incluyendo
el handshake ephemeral y el descifrado/ACK del cliente. Después de cada entrega
se contrasta el estado REST. Al final se descarga y descifra todo el historial
stored, comprobando IDs únicos, contenido íntegro y ausencia de mensajes perdidos;
ephemeral debe rechazar el historial con HISTORY_NOT_STORED. Se rechazan cursores
repetidos y cuentas con claves distintas. Los errores del informe son códigos
controlados, sin respuestas, URLs, tokens, claves ni contenido.

Código de salida 0 significa que este escenario terminó correctamente; 1 indica
fallo o configuración inválida. `production_certified` siempre es false.
El ensayo local usa dos sockets y datos sintéticos para probar la herramienta;
**no acredita capacidad de producción** ni constituye un pico previsto.

Antes de cerrar N9, registrar SHA e imágenes exactos, hardware del servidor y del
generador, origen de la estimación de pico, duración, mezcla y cadencia reales.
Capturar recursos, latencia y purga en Prometheus. Añadir los escenarios offline,
reconexión, tormenta de tickets y duración sostenida necesarios para el producto,
además de los criterios SMTP/Push/HTTPS/UI de [N9](N9_HOMOLOGACION_RELEASE.md).
