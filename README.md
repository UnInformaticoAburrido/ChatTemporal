# Servidor de chat · identidad, persistencia y niveles de desarrollo

Base creada a partir de la **especificación maestra de producción v1**. Incluye
infraestructura, migraciones, identidad REST, claves públicas y lecturas paginadas.
Ya existen registro, verificación, recuperación, sesiones y edición de perfil.
N5 añade mensajería WebSocket stored/ephemeral sobre las invitaciones de N4.
N6 completa las votaciones y la conservación/eliminación automática de la gracia.
N7 añade transferencia de claves, replay de historial y Web Push genérico.
**El MVP completo sigue pendiente**: faltan la interfaz cliente y los niveles
N8–N9. El siguiente paso es N8: operación y seguridad.
`/health/ready` y `/metrics` se mantienen en la red interna.

- [Niveles, dependencias y criterios de aceptación](docs/NIVELES_PRODUCCION.md)
- [Decisiones, contradicciones y respuestas](docs/DECISIONES.md)
- [Operación y despliegue](docs/OPERACION.md)
- [Especificación original](docs/especificacion_maestra_chat_produccion.docx)
- [Validación realizada](docs/VALIDACION.md)
- [N6 documentado por subapartados](docs/N6_VOTACIONES.md)
- [N7 documentado por subapartados](docs/N7_RECUPERACION_PUSH.md)

## Estructura

```text
docker-compose.yml              producción: solo Caddy publica 80/443
docker-compose.local.yml        desarrollo: solo 127.0.0.1:18080 (configurable)
docker-compose.test.yml         pruebas en un proyecto separado
python/                        fuentes FastAPI, worker, dependencias y pruebas
BD/postgresql/                 configuración y migraciones Alembic
BD/redis/                      configuración sin persistencia y scripts
caddy/                         proxy, TLS y rutas públicas
prometheus/                    recogida de métricas y alertas iniciales
scripts/                       preparación y comprobaciones
secrets/                       archivos locales ignorados por Git
docs/                          niveles, decisiones, operación y especificación
```

Se usa **`BD` en mayúsculas** tal como se solicita; PostgreSQL y Redis se separan
en subcarpetas descriptivas. Los procesos `migrate` y `worker` comparten `python/`
porque ejecutan el mismo código de la aplicación. Los volúmenes de datos se
mantienen separados de los directorios de fuentes.

## Arranque local

Requisitos: Docker Engine, Docker Compose **2.24.4 o posterior** (por `!override`),
Python 3.11+ y OpenSSL en el host. El usuario necesita acceso al daemon Docker.
Los contenedores Python y Redis usan UID/GID 1000; adaptar permisos si el host
usa otro usuario, sin hacer públicos los secretos.

```bash
python3 scripts/init_local.py
docker compose -f docker-compose.yml -f docker-compose.local.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build -d --wait --wait-timeout 180
curl http://127.0.0.1:18080/health/live
```

La preparación local no sobrescribe claves existentes. En este espacio de trabajo
ya se ha ejecutado; puedes comenzar por `docker compose ... up`. SMTP local queda
como proveedor pendiente: **el registro devuelve 503 y hace rollback hasta
configurar un SMTP válido**. No se simulan envíos de correo en la aplicación.
No existe una interfaz web de chat en esta entrega.

Para construir las imágenes y arrancar por primera vez: `make up`.
Una vez construidas, iniciar o volver a iniciar con:

```bash
make start
```

Usa las imágenes existentes, aplica la configuración y espera a que los servicios
estén disponibles. Su equivalente sin Make es:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up --no-build -d --wait --wait-timeout 180
```

Para parar conservando contenedores y datos: `make stop`. Después, `make start`
los vuelve a levantar. Si cambias dependencias o Dockerfile, utiliza `make up`
para reconstruir. No hace falta repetir la generación de secretos.

Si venías de la configuración anterior y Caddy mostraba
`exec /usr/bin/caddy: operation not permitted`, ejecuta **`sudo make up` una vez**.
Esto construye la imagen corregida de Caddy sin la capacidad de archivo que
impedía su ejecución con las restricciones del contenedor. Los siguientes
arranques pueden hacerse con `sudo make start` si tu usuario necesita sudo para Docker.

El puerto local por defecto es **18080**. Si está ocupado, puedes elegir otro:

```bash
CHAT_HTTP_PORT=18081 make start
```

En ese caso, accede a `http://localhost:18081/health/live` y usa la misma variable
en los siguientes arranques. Puedes guardarla en `.env` exclusivamente para
desarrollo local. CORS incluye automáticamente el puerto elegido en los orígenes
loopback de desarrollo. Los puertos de producción continúan siendo 80/443.

## Orden y espera

```text
PostgreSQL saludable ─┐
                     ├── migrate: validación + espera + Alembic (exit 0)
Redis saludable ─────┘          ├── python: espera + esquema + API saludable
                                └── worker: espera + esquema + primera purga
caddy-init: permisos de volúmenes ────────────────────────────┐
python y worker saludables ──────────────────────────────────┴── Caddy
python saludable ── Prometheus (perfil observability)
```

Si falla una migración, la API nueva no arranca. Los procesos vuelven a comprobar
dependencias y versión del esquema incluso cuando Docker los reinicia sin
ejecutar Compose. Tras una caída, `/health/ready` devuelve 503 y Caddy comprueba
esa ruta cada 5 segundos para retirar tráfico; `/health/live` sigue comprobando
solo el proceso. `depends_on` no es un supervisor continuo de dependencias.

Los bind mounts `:ro` consumen el código original del host. Tras editar Python,
reiniciar `python`/`worker`; no hay autoreload en producción. Si cambian las
dependencias, reconstruir la imagen. Las migraciones nuevas se ejecutan con el
procedimiento explícito de [operación](docs/OPERACION.md).

## Comprobaciones

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r python/requirements-dev.lock
cd python
../.venv/bin/python -m pytest -q -m 'not integration'
../.venv/bin/ruff check .
../.venv/bin/mypy chat chat_client
```

Las pruebas unitarias de configuración generan secretos temporales y necesitan
OpenSSL; las de Compose necesitan el CLI pero no el daemon. Para PostgreSQL y
Redis reales, desde la raíz: `sh scripts/test_containers.sh`. Usa el proyecto
aislado `chat-tests`, no publica puertos y conserva sus volúmenes al terminar.

La continuación de N1 añade transacciones, escritura idempotente de mensajes y
lectura de historial con caducidad/pertenencia. N2 añade las API de identidad y
sus pruebas de concurrencia. N3 añade claves, contratos estrictos, lecturas
paginadas y cliente criptográfico de referencia. El envío sigue siendo una
primitiva interna. Las decisiones se explican en DEC-25–DEC-52.

Si Docker no está disponible y ya tienes binarios PostgreSQL/Redis y el entorno
Python de pruebas, puedes ejecutar una alternativa aislada:

```bash
CHAT_TEST_PG_BIN=/ruta/a/postgresql/bin \
CHAT_TEST_REDIS_SERVER=/ruta/a/redis-server \
CHAT_TEST_PYTHON="$PWD/.venv/bin/python" \
sh scripts/test_local_services.sh
```

Ejecutar como usuario normal. El script crea servicios nuevos en `/tmp`, con
sockets Unix privados y sin puertos TCP; aplica Alembic y ejecuta la suite de
integración. Al terminar detiene ambos procesos y muestra el directorio de
evidencias. No usa los secretos ni las bases existentes del proyecto. Esta
alternativa no sustituye la validación de las imágenes de producción.

## Identidad REST (N2)

Todas las rutas usan `/api/v1`, JSON estricto y errores con X-Request-ID.

| Rutas | Comportamiento |
|---|---|
| POST `/users/register` | nick/email; frase BIP-39 solo en esta respuesta, sesión y correo de verificación |
| GET/PATCH/DELETE `/users/me` | Perfil propio autenticado; cambiar email exige verificarlo de nuevo |
| GET `/users/{id}` | Perfil público solo propio o con una relación previamente aceptada |
| POST `/users/verify-email`, `/users/resend-verification` | Código de correo de un uso; reenvío invalida el anterior |
| POST `/auth/exchange`, `/auth/recover` | Nueva sesión y revocación de las anteriores |
| POST `/auth/refresh`, `/auth/logout` | Refresh rotado y cierre de sesión; reuse revoca la familia |
| POST `/auth/ws-ticket` | Requiere email verificado; ticket de un uso para `/ws/v1` |

Para actualizar una instalación existente, reconstruye la imagen Python y aplica
`0003_email_hash` siguiendo [la operación](docs/OPERACION.md). `make start` por sí
solo no instala las nuevas bibliotecas. SMTP_URL acepta `smtp://` con STARTTLS o
`smtps://` con TLS implícito. Configura credenciales en el archivo secreto, nunca en
Git. El correo contiene el código para `/users/verify-email`; aún no hay una página
web de confirmación. Las pruebas de identidad capturan correo sin enviar a terceros.

## Claves y contratos (N3)

Rutas autenticadas bajo `/api/v1`, con cuota general y sesión vigente:

| Rutas | Comportamiento |
|---|---|
| PUT `/users/me/keys` | Crea o sustituye `{public_key, protocol_version:1}`; pública de 32 bytes en Base64URL sin padding |
| POST `/users/me/keys/rotate` | Reemplaza la pública existente; conserva una sola fila por usuario |
| GET `/users/{id}/keys` | Solo propia o con conversación compartida permitida; no devuelve nick ni id del propietario |
| GET `/conversations`, `/conversations/{id}` | Listado/detalle autorizado; `peer=null` hasta aceptación |
| GET `/conversations/{id}/messages` | Ciphertext stored no caducado; ephemeral devuelve 409 HISTORY_NOT_STORED |

Listados: `?limit=50&cursor=...`, máximo 100, respuesta `{items, next_cursor}`.
`next_cursor=null` indica fin. El cursor no concede permisos y no debe modificarse
ni reutilizarse en otro historial. El orden descendente desempata por UUID; el
listado de conversaciones puede cambiar si reciben actividad entre páginas.

Cliente de referencia local, desde `python/` con dependencias de desarrollo
instaladas (o `pip install -r requirements-client.lock`):

```python
from chat_client.crypto import KeyPair, encrypt_text, decrypt_text

alice, bob = KeyPair.generate(), KeyPair.generate()
message = encrypt_text("Hola", alice, bob.public_key)
assert decrypt_text(message, bob) == "Hola"
```

Este módulo no guarda privadas ni llama a la red. El cliente final deberá proteger
su almacenamiento local e integrar la UI. `max_characters` permite cambiar el
límite local de 256; el servidor solo valida estructura y bytes cifrados.
`parse_message_send` valida el contrato utilizado por el servidor WS de N5.
Las pruebas N3 conservan fixtures de lectura; las de N4 crean conversaciones por REST.

## Invitaciones y conversaciones (N4)

Rutas autenticadas bajo `/api/v1`:

| Rutas | Comportamiento |
|---|---|
| GET `/invitations/me` | Crea bajo demanda y devuelve dos códigos estables de 83 caracteres; exige email verificado |
| POST `/invitations/regenerate` | Revoca ambos códigos y crea una nueva identidad/generación en una transacción |
| POST `/invitations/redeem` | Recibe `{code}`; exige email verificado y devuelve conversación pending con `host_public_key` |
| POST `/conversations/{id}/accept` | Solo host; activa la conversación y abre un voto de 30 s con censo congelado |
| POST `/conversations/{id}/upgrade` | Solo host en active; cambia ephemeral→stored para mensajes posteriores |
| DELETE `/conversations/{id}` | Cierre unilateral permanente |
| POST `/conversations/{id}/leave` | Marca al usuario como left y cierra el intercambio 1:1 |

Antes de aceptar, `peer=null` para ambos participantes; canjear no revela nick ni
user_id del host. `host_public_key` es la pública Base64URL de 32 bytes (protocolo v1).
El host debe haber publicado una clave: sin ella, redeem devuelve 409
`HOST_KEY_UNAVAILABLE` y no crea conversación. No se permite canjear el código propio.
Cada canje correcto crea una conversación nueva; no es una operación idempotente.
Regenerar invalida los códigos, pero conserva las conversaciones ya creadas.

Los reintentos de accept recuperan el mismo voto; upgrade ya aplicado y close
ya realizado no alteran sus fechas. Leave se puede repetir, pero después de salir
el usuario pierde acceso al detalle, historial y claves de esa relación.
El censo y plazo de aceptación quedan persistidos con la migración
**0004_vote_electorate**, que debe aplicarse antes de arrancar esta versión
(ver [operación](docs/OPERACION.md)). No se reconstruyen censos de votos históricos.

N4 abre el voto y bloquea las escrituras internas durante sus 30 s. N6 permite
votar, consultar y resolver el resultado automáticamente. N5 publica los eventos
de conversación y cancela offers pendientes; los mensajes
ya confirmados antes de un cierre o upgrade conservan su semántica original.

## Mensajería WebSocket (N5)

1. Obtener `POST /api/v1/auth/ws-ticket` con Bearer y email verificado.
2. Conectar a `/ws/v1?ticket=...`. El ticket dura 30 s y solo admite un uso;
   nunca colocar el JWT en la URL. Se recibe `session.ready`.
3. En stored, enviar `message.send` directamente. La aceptación se comprueba con
   **GET `/api/v1/messages/{message_id}/status`**: pending/delivered/failed/expired.
   No existe un evento `message.accepted`. Reintentar el mismo UUID y payload no
   duplica contenido ni consume otra unidad de gracia.
4. En ephemeral, usar `message.offer` → `message.ready` → `message.send` →
   `message.new` → `message.ack` → `message.delivered`. La autorización ready
   queda ligada a la conexión receptora y vence con el plazo original de oferta.
   Tras fallo definitivo o reconexión, iniciar un nuevo intento con UUID nuevo.

Todos los frames son JSON con `type`, `request_id`, `conversation_id`, `timestamp`
y `payload`, conforme a §26. `message_id` y `request_id` son UUIDv4 diferentes en
su propósito. Los binarios se rechazan con 1003; el máximo inicial es 8192 bytes.
El heartbeat es ping/pong nativo (25 s, timeout 75 s), no mensajes JSON.

El servidor conserva ciphertext stored en PostgreSQL hasta su caducidad. Offline
se consulta el historial REST. El ciphertext ephemeral reside solo en RAM/Redis,
con TTL de entrega (60 s por defecto), y se borra tras ACK o desconexión. Una oferta
sin send no crea message_events. Un receptor que conecta a tiempo recibe sus ofertas
pendientes; Web Push se implementará en N7.

Pub/Sub distribuye eventos entre instancias. Las sesiones se revalidan en cada
operación y periódicamente en sockets ociosos. Si Redis pierde estado, se cierran
las conexiones y se exige un ticket nuevo. Pub/Sub no es una cola durable: tras
reconectar, consultar estado/historial REST. La reconexión con backoff de §26.2
corresponde al cliente final, todavía pendiente.

Aplicar **0005_delivery_mode** antes de arrancar API/worker. Guarda el modo histórico,
plazo y conexión de cada entrega, para que upgrade o pérdida de Redis no conviertan
un mensaje efímero en persistente. El worker reconcilia entregas huérfanas y plazos.
Ver [operación](docs/OPERACION.md)
y [evidencia de pruebas](docs/VALIDACION.md).

## Votaciones y gracia (N6)

1. Accept devuelve el identificador del voto `retain_grace_messages` y emite
   `vote.opened`. Durante sus 30 s se rechazan nuevos offer/send con `VOTE_OPEN`.
2. `POST /api/v1/votes/{id}/ballots`, con Bearer y `{"choice":true}` o
   `{"choice":false}`, devuelve `VoteSnapshot`. Repetir la elección es
   idempotente; cambiarla devuelve 409 `VOTE_CONFLICT`.
3. `GET /api/v1/votes/{id}` devuelve el estado y el `my_vote` del usuario actual.
   Solo acceden electores que siguen perteneciendo a la conversación. Un primer
   voto fuera de plazo devuelve 410 `VOTE_EXPIRED`.
4. El worker cierra automáticamente al vencer el plazo. Se aprueba únicamente
   con más de la mitad del censo a favor; toda abstención cuenta como NO.
   Haber alcanzado mayoría antes no acorta los 30 s.
5. En stored, aprobar conserva la gracia hasta su caducidad normal; rechazar
   elimina su ciphertext sin borrar estado de entrega ni deduplicación. Los
   mensajes posteriores permanecen. En ephemeral, el cliente aplica el resultado
   localmente al recibir `vote.updated` o consultar GET después de reconectar.

Aplicar **0006_ballot_electorate** antes de reiniciar API y worker. El censo y los
votos ya emitidos se mantienen si un elector abandona o elimina su cuenta.
Pub/Sub no garantiza recuperación ni orden entre operaciones concurrentes:
consultar GET al vencer el plazo y tras cortes; un resultado terminal no debe
volver a open por un aviso atrasado. La interfaz cliente final sigue pendiente.
Detalles, decisiones y pruebas en [N6 por subapartados](docs/N6_VOTACIONES.md).

## Recuperación y Web Push (N7)

- El dispositivo nuevo obtiene una sesión por `/auth/exchange` y crea una
  transferencia con `POST /api/v1/key-transfers`. La sesión anterior queda
  revocada para chat; conserva únicamente permiso para subir el blob cifrado
  a su sucesor, hasta 24 h y sin renovar su access token.
- El dispositivo anterior cifra el bundle localmente y ejecuta
  `PUT /api/v1/key-transfers/{id}` con `{"encrypted_blob":"Base64URL"}`.
  Solo admite una carga, máximo 65536 bytes. El secreto se comparte directamente
  mediante QR; nunca se incluye en las peticiones al servidor.
- El nuevo descarga mediante GET, descifra localmente y confirma con DELETE.
  GET permite reintentos. DELETE elimina blob/metadatos y el permiso del anterior.
  El TTL total comienza en POST y no se renueva al subir o descargar.
- `chat_client.recovery` incluye cifrado/descifrado del bundle, contenido QR y
  `replay_history` para volver a cifrar una copia local en orden. Renderizar/escanear
  el QR y aplicar los mensajes recibidos corresponde a la interfaz cliente final.
- `recovery.replay.begin/item/end` transportan el historial por WebSocket entre
  participantes conectados en una conversación active. Se empieza en sequence=0,
  se termina con item_count exacto y se limita a 100 items/s por replay. No se
  insertan mensajes, eventos ni entregas normales. Tras un corte se reinicia con
  otro replay_id; si nadie conserva una copia local, el historial es irrecuperable.
- `POST /api/v1/push/subscriptions` recibe `{endpoint,p256dh,auth_secret}` y hace
  upsert propio; DELETE `/api/v1/push/subscriptions/{id}` revoca. Se aceptan
  endpoints HTTPS de puerto 443 y claves Web Push válidas. El worker envía
  notificaciones genéricas de stored y offers ephemeral offline desde una cola
  que contiene solo event_type/conversation_id/message_id y metadatos de reintento.

Aplicar **0007_recovery_push** antes de reiniciar API y worker. Se utilizan las
claves VAPID existentes. Las suscripciones se vinculan a la sesión vigente para
no seguir notificando un dispositivo sustituido. La cola y sus reintentos no
garantizan exactamente una notificación si un proceso cae después del envío.
La validación local no sustituye las pruebas con navegador/proveedor Push real.
Ver [N7 por subapartados](docs/N7_RECUPERACION_PUSH.md) y [operación](docs/OPERACION.md).

Producción necesita dominio/DNS, SMTP, claves públicas de bootstrap y secretos
reales, además de superar todos los niveles y puertas de calidad. No basta con
cambiar `APP_ENV`. La configuración suministrada falla deliberadamente en
producción mientras contenga valores de ejemplo.
