# Servidor de chat · identidad, persistencia y niveles de desarrollo

Base creada a partir de la **especificación maestra de producción v1**. Incluye
infraestructura, migraciones, identidad REST, claves públicas y lecturas paginadas.
Ya existen registro, verificación, recuperación, sesiones y edición de perfil.
N4 añade invitaciones y transiciones de conversación. **El MVP completo del chat
sigue pendiente**: aún no hay envíos por WebSocket ni interfaz cliente. El siguiente
nivel es N5; la emisión y resolución de votos corresponde a N6.
`/health/ready` y `/metrics` se mantienen en la red interna.

- [Niveles, dependencias y criterios de aceptación](docs/NIVELES_PRODUCCION.md)
- [Decisiones, contradicciones y respuestas](docs/DECISIONES.md)
- [Operación y despliegue](docs/OPERACION.md)
- [Especificación original](docs/especificacion_maestra_chat_produccion.docx)
- [Validación realizada](docs/VALIDACION.md)

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
| POST `/auth/ws-ticket` | Requiere email verificado; ticket temporal para el futuro WS de N5 |

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
`parse_message_send` prepara el contrato futuro, sin habilitar envío WS todavía.
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

N4 abre el voto y bloquea las escrituras internas durante sus 30 s, pero **todavía
no permite votar ni resuelve el resultado**: eso se implementará en N6. Hasta
entonces, su fila puede conservar status=open después del plazo. La publicación
de eventos y cancelación de offers requieren N5, pues aún no existe el servidor WS.

Producción necesita dominio/DNS, SMTP, claves públicas de bootstrap y secretos
reales, además de superar todos los niveles y puertas de calidad. No basta con
cambiar `APP_ENV`. La configuración suministrada falla deliberadamente en
producción mientras contenga valores de ejemplo.
