# Carga online, reconexiones y fallos efímeros de N9

`scripts/load_chat.py` verifica tráfico real REST/WS cifrado con el cliente de
referencia. Los perfiles son **unidireccionales y de bucle cerrado**: cada pareja
espera la confirmación o el fallo previsto antes del siguiente mensaje.

| Perfil | Modos | Comportamiento |
|---|---|---|
| `online` (predeterminado) | stored/ephemeral | Conexiones mantenidas durante todo el tráfico |
| `stored-reconnect` | Solo stored | Receptor offline, reconexión del emisor, reintentos antes/después del ACK y recuperación por historial |
| `ephemeral-disconnect` | Solo ephemeral | Receptor desconectado antes del ACK, fallo confirmado, reenvío rechazado y nueva entrega tras reconectar |

No representan por sí solos Push, registro, todos los fallos ephemeral, cortes
abruptos de red ni carga máxima sostenible. Los perfiles de reconexión cierran los
sockets ordenadamente; no simula pérdida de paquetes o un enlace bloqueado.

El operador aporta el pico esperado, duración y cadencia. La herramienta abre
**dos veces el pico de usuarios indicado**: una pareja por usuario del pico,
con dos cuentas distintas por pareja. Todas esperan una barrera antes de enviar,
y, en online, conservan las conexiones hasta que termine el grupo. En reconexión,
esa cifra es el máximo inicial, **no concurrencia sostenida**. Cualquier fallo hace
fallar el resultado, salvo el fallo de entrega provocado y verificado en el perfil
efímero; un timeout o 429 no se transforma en éxito mediante reintentos.

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

Para el perfil de reconexión, usar un archivo nuevo con **solo conversaciones
stored vacías**, y añadir a la invocación anterior:

```sh
--profile stored-reconnect \
--reconnect-every "$CHAT_RECONNECT_EVERY" \
--offline-seconds "$CHAT_OFFLINE_SECONDS"
```

`reconnect-every` es un entero positivo: se corta en el primer mensaje y cada N
mensajes posteriores de cada pareja (predeterminado 10). `offline-seconds` es la
pausa mínima con ambos sockets cerrados (predeterminado 1 s); debe ser positiva
y no superar `timeout`. Se completa el mensaje en vuelo aunque se alcance la
duración solicitada. El ensayo requiere sesiones vigentes y cuotas suficientes;
no amplía límites del servidor ni oculta un 429 con reintentos automáticos.

Cada ciclo cierra al receptor, envía una vez, espera estado REST pending y cierra
al emisor. Después de la pausa, el emisor obtiene otro ticket y reenvía exactamente
el mismo ID/payload. El receptor reconecta y recupera/descifra por REST: no se espera
que WS retransmita automáticamente el historial. Solo después envía ACK. Se reenvía
una segunda vez tras la entrega, exigiendo el recibo asociado al nuevo request_id.
El historial debe conservar un único mensaje íntegro por ID. Los recibos repetidos
de mensajes ya confirmados no suman entregas; los IDs desconocidos hacen fallar.

Para comprobar la desconexión efímera, usar **solo conversaciones ephemeral** y
añadir `--profile ephemeral-disconnect`, con los mismos parámetros de cadencia
y pausa. En el primer ciclo y cada N entregas confirmadas, el receptor descifra
el mensaje y cierra su socket **antes del ACK**. El emisor debe recibir
`RECIPIENT_DISCONNECTED` y REST debe indicar failed. Tras la pausa, el receptor
reconecta con un ticket nuevo; reenviar el ID/payload fallido debe producir
`DELIVERY_TIMEOUT`, correlacionado con el request_id del reenvío, y conservar
failed en REST. El historial debe seguir rechazado con HISTORY_NOT_STORED.

Después se exige una entrega íntegra con **otro UUID**, incluyendo handshake y
ACK, aunque se haya agotado la duración durante el fallo. `reconnect-every` cuenta
entregas confirmadas; el mensaje fallido es un intento adicional. La pausa solo
desconecta al receptor. Es un cierre ordenado, no una simulación de red bloqueada.
La herramienta comprueba el contrato externo; las pruebas de integración también
inspeccionan PostgreSQL y Redis para demostrar que ambos desenlaces dejan solo
metadatos, sin fila de contenido ni payload transitorio.

## Interpretar y completar la homologación

El informe contiene conexiones máximas, intentos, envíos, confirmaciones,
parejas verificadas y latencia p50/p95/máxima hasta `message.delivered`, incluyendo
el handshake ephemeral y el descifrado/ACK del cliente. Después de cada entrega
se contrasta el estado REST. Al final se descarga y descifra todo el historial
stored, comprobando IDs únicos, contenido íntegro y ausencia de mensajes perdidos;
ephemeral debe rechazar el historial con HISTORY_NOT_STORED. Se rechazan cursores
repetidos y cuentas con claves distintas. Los errores del informe son códigos
controlados, sin respuestas, URLs, tokens, claves ni contenido.

En reconexión la latencia incluye la pausa offline, nuevos tickets, recuperación
por historial y comprobación del recibo del último reintento. `attempted`, `sent`
y `confirmed` cuentan **mensajes únicos**; `retries` cuenta los reenvíos adicionales
(dos por ciclo completado). `disconnect_cycles` y `reconnections` distinguen cortes
y sockets abiertos de nuevo. Recuperar el historial completo en cada ciclo añade
tráfico de lectura: tenerlo en cuenta al comparar perfiles. Contenido corrupto o
incompleto aborta el ensayo antes del ACK; nunca se informa como entrega correcta.

En `ephemeral-disconnect`, `expected_failures` cuenta fallos provocados y
verificados, separados de `confirmed`. Se exige `attempted = sent = confirmed +
expected_failures`, una reconexión y un reenvío rechazado por ciclo. Los errores
inesperados abortan. La latencia incluye únicamente mensajes confirmados, desde
su nuevo handshake hasta el recibo de entrega; excluye la pausa y el intento
fallido anterior. No interpretar ese percentil como tiempo de recuperación.

Código de salida 0 significa que este escenario terminó correctamente; 1 indica
fallo o configuración inválida. `production_certified` siempre es false.
El ensayo local usa dos sockets y datos sintéticos para probar la herramienta;
**no acredita capacidad de producción** ni constituye un pico previsto.

Antes de cerrar N9, registrar SHA e imágenes exactos, hardware del servidor y del
generador, origen de la estimación de pico, duración, mezcla y cadencia reales.
Capturar recursos, latencia y purga en Prometheus. Ejecutar los tres perfiles y añadir
otros fallos ephemeral, cortes abruptos, tormenta de tickets y duración sostenida necesarios para el producto,
además de los criterios SMTP/Push/HTTPS/UI de [N9](N9_HOMOLOGACION_RELEASE.md).
