# Servidor de chat · identidad, persistencia y niveles de desarrollo

Base creada a partir de la **especificación maestra de producción v1**. Incluye
infraestructura, migraciones, persistencia e identidad REST. Ya existen registro,
verificación, recuperación, sesiones y edición de perfil. **El MVP completo del
chat sigue pendiente**: aún no hay claves públicas, invitaciones ni mensajería.
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
../.venv/bin/mypy chat
```

Las pruebas unitarias de configuración generan secretos temporales y necesitan
OpenSSL; las de Compose necesitan el CLI pero no el daemon. Para PostgreSQL y
Redis reales, desde la raíz: `sh scripts/test_containers.sh`. Usa el proyecto
aislado `chat-tests`, no publica puertos y conserva sus volúmenes al terminar.

La continuación de N1 añade transacciones, escritura idempotente de mensajes y
lectura de historial con caducidad/pertenencia. N2 añade las API de identidad y
sus pruebas de concurrencia. La mensajería sigue siendo un componente interno.
Las decisiones no fijadas por la especificación se explican en DEC-25–DEC-44.

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

Producción necesita dominio/DNS, SMTP, claves públicas de bootstrap y secretos
reales, además de superar todos los niveles y puertas de calidad. No basta con
cambiar `APP_ENV`. La configuración suministrada falla deliberadamente en
producción mientras contenga valores de ejemplo.
