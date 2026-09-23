# Validación de la entrega

## Continuación N7 · 2026-09-23

Resultado final: **108 pruebas unitarias y 84 de integración correctas (192 total)**.
Ruff y `git diff --check` correctos; mypy estricto sin errores en 50 módulos.
No hay nuevas dependencias. Persisten las advertencias de deprecación de
Starlette/httpx/AnyIO y Uvicorn/websockets ya registradas; no se ocultan.

Servicios reales aislados: Python 3.14.4, PostgreSQL 18.6 y Redis 8.0.5. El entorno
de `/tmp` desapareció durante la continuación; se reconstruyó en `.venv` y
`artifacts/n7/runtime` con los locks existentes y paquetes extraídos, sin instalar
servicios del sistema. PostgreSQL/Redis usan sockets Unix privados, WS usa puertos
loopback y Push un servidor HTTPS local con certificado verificado. Los servicios
se detienen al terminar. Evidencia del último runner: `/tmp/chat-persistence-test.Xs63k7`.
Resúmenes de pruebas conservados en `artifacts/n7/unit.log` e `integration.log`,
ignorados por Git. Evidencia por apartado en [N7_RECUPERACION_PUSH.md](N7_RECUPERACION_PUSH.md).

Comprobaciones nuevas:

- Transferencia entre dispositivos usando bootstrap/exchange real, una sola sesión
  normal y permiso residual del anterior limitado a PUT para su sucesor. Se prueban
  bloqueo de REST/WS/POST, caducidad, otro login, DELETE y logout del destino.
- Blob máximo de 65536 bytes, 413 por exceso, carga concurrente única, campos
  estrictos, propietario/sid, GET repetido, TTL no renovado y eliminación inmediata.
  El secreto QR no forma parte del PUT ni del repr; ciphertext alterado y UUID/QR
  incompatibles no se descifran.
- Cliente que recifra historial local para una clave nueva, preservando orden y
  metadatos opcionales. Frames compatibles con el parser; descifrado real en cliente.
- Replay en dos instancias ASGI: begin/item/end, sequence e item_count exactos,
  finalización, conexiones vinculadas, receptor offline, rechazo de terceros y
  límite por replay. No se crean eventos/mensajes/deliveries normales ni jobs Push;
  Redis conserva solo metadata del replay con TTL.
- Suscripciones Push: upsert propio, conflicto de endpoint ajeno, revocación,
  claves inválidas y destinos locales/privados rechazados. DNS mixto público/privado
  falla cerrado. No se notifica a una suscripción de sesión sustituida.
- Stored offline genera cola al aceptar el mensaje; retry no duplica jobs. Recibos
  por suscripción evitan repetir éxitos al reintentar fallos. 404/410 revocan.
  Ephemeral crea Push solo para offers offline; no se crea message_event por offer.
- Vector conocido RFC 8291 exacto y petición HTTPS real a receptor local: TLS/SNI,
  VAPID ES256, payload genérico cifrado y descifrado independiente con HMAC/AES-GCM.
  Las IP públicas se enrutan a loopback exclusivamente dentro del test; producción
  mantiene la validación de destinos y certificados.
- Migraciones desde cero y desde cada revisión 0001–0006 hasta 0007_recovery_push.
  Se conserva la suite previa de identidad, mensajería, votaciones y retención.

Reproducción desde la raíz, con el entorno local preparado:

```bash
cd python
../.venv/bin/python -m pytest -q -m 'not integration' -p no:cacheprovider
../.venv/bin/ruff check .
../.venv/bin/mypy chat chat_client
cd ..
LD_LIBRARY_PATH="$PWD/artifacts/n7/runtime/usr/lib/x86_64-linux-gnu" \
CHAT_TEST_PG_BIN="$PWD/artifacts/n7/runtime/usr/lib/postgresql/18/bin" \
CHAT_TEST_REDIS_SERVER="$PWD/artifacts/n7/runtime/usr/bin/redis-server" \
CHAT_TEST_PYTHON="$PWD/.venv/bin/python" sh scripts/test_local_services.sh
git diff --check
```

Límites: no se dispone de suscripción de navegador/proveedor Push real ni de
proveedor SMTP real para homologación. Los tests HTTPS prueban protocolo y cifrado,
no recepción en un navegador de producción. UI final, versiones Compose exactas,
HTTPS/WSS de despliegue, staging y carga siguen pendientes. No se repite auditoría
de dependencias ni se certifica producción. N8/N9 son los siguientes niveles.

## Continuación N6 · 2026-09-23

Resultado: **93 pruebas unitarias y 71 de integración correctas (164 total)**.
Ruff de Python y de la migración nueva, `git diff --check` y mypy estricto de
42 módulos correctos. No hay nuevas dependencias. Se mantienen los avisos de
deprecación ya existentes de Starlette/httpx/AnyIO y Uvicorn/websockets.

El entorno temporal de N5 desapareció al reiniciar; se reconstruyó con los locks
del repositorio y paquetes Ubuntu extraídos en `/tmp`, sin instalar servicios en
el sistema. Python 3.14.4, PostgreSQL 18.6 y Redis 8.0.5. PostgreSQL/Redis usan
sockets Unix privados; las pruebas WebSocket usan puertos loopback temporales.
La suite arranca también el worker real como subproceso y lo detiene al finalizar.
Evidencia final: `/tmp/chat-persistence-test.g1gFTp` (temporal, no se versiona).

Comprobaciones N6, documentadas por apartado en [N6_VOTACIONES.md](N6_VOTACIONES.md):

- REST de votos, DTO estricto, sesión revocada, ocultación de votos ajenos y
  pérdida de acceso tras leave/borrado. Un primer ballot tardío devuelve el
  **410 VOTE_EXPIRED** normativo; cambiar uno existente devuelve 409.
- Ocho ballots simultáneos con elecciones opuestas producen una sola elección.
  Una petición que espera un lock comprueba el plazo al adquirirlo; no se usa
  el inicio de transacción como reloj. Reintentos compatibles funcionan tras cierre.
- Mayoría absoluta y abstención=NO con censos sintéticos 2/3/5/10; no se habilitan
  grupos en la API. No se cierra antes del plazo aunque todos voten a favor.
- Tres resolutores concurrentes cierran una sola vez. Rollback revierte juntos
  el resultado y la eliminación de gracia. Un proceso worker cierra sin lecturas REST.
- Gracia stored aprobada conservada; rechazada oculta desde deadline y eliminada
  físicamente al resolver. Permanecen mensajes posteriores, eventos, entregas,
  fingerprints y contador; reintentar no resucita contenido eliminado.
- Ballots conservados al abandonar/eliminar el elector; censo y resultado no
  cambian. La nueva FK rechaza un ballot de alguien ajeno al censo.
- WS entre dos instancias: gracia real stored/ephemeral, accept, ballots,
  `vote.opened/updated` con `my_vote` propio, bloqueo send/offer y envío tras cierre.
  Upgrade no convierte el contenido efímero previo en stored.
- Fallo Pub/Sub inyectado: el ballot mantiene commit aunque REST devuelve 503;
  la resolución de otros votos continúa y GET permite recuperar el resultado.
- Migraciones desde cero y desde 0001/0002/0003/0004/**0005** hasta 0006.

Comandos usados (rutas temporales sustituibles por binarios equivalentes):

```bash
cd python
/tmp/chat-n6-venv/bin/python -m pytest -q -m 'not integration' -p no:cacheprovider
/tmp/chat-n6-venv/bin/ruff check . ../BD/postgresql/migrations/versions/0006_ballot_electorate.py
/tmp/chat-n6-venv/bin/mypy --cache-dir /tmp/chat-n6-mypy chat chat_client
cd ..
LD_LIBRARY_PATH=/tmp/chat-n6-runtime/usr/lib/x86_64-linux-gnu \
CHAT_TEST_PG_BIN=/tmp/chat-n6-runtime/usr/lib/postgresql/18/bin \
CHAT_TEST_REDIS_SERVER=/tmp/chat-n6-runtime/usr/bin/redis-server \
CHAT_TEST_PYTHON=/tmp/chat-n6-venv/bin/python sh scripts/test_local_services.sh
git diff --check
```

Las pruebas temporales requieren permiso para sockets locales. La primera pasada
detectó expectativas de migración antiguas y dos fixtures de prueba incorrectos;
se corrigieron antes de la ejecución final. El caso de locks limpia el snapshot
de estadísticas de PostgreSQL al observar la espera para no consultar una vista
congelada dentro de la transacción. La comparación con §23 corrigió el HTTP de
voto vencido a 410. No se alteraron los requisitos para hacer pasar pruebas.

Límites: Docker sigue inaccesible, sin homologación de versiones Compose/HTTPS/WSS,
staging, carga ni proveedores SMTP/Push. Pub/Sub no es durable ni ordena commits
concurrentes; los clientes recuperan estado por GET. La aplicación local del
resultado ephemeral corresponde a la interfaz cliente pendiente. N7–N9 continúan
pendientes; no se repite auditoría de dependencias ni se certifica producción.

## Continuación N5 · 2026-09-22

Resultado final: **89 pruebas unitarias y 50 de integración correctas (139 total)**.
Ruff y `git diff --check` correctos; mypy estricto sin errores en 39 módulos.
No hay nuevas dependencias. Persisten avisos de deprecación de Starlette/httpx/AnyIO
y del backend Uvicorn/websockets; no se presentan como fallos ni se ocultan.

El entorno temporal anterior desapareció tras reiniciar. Se reconstruyó con los
locks existentes y binarios oficiales extraídos en `/tmp`, sin instalar servicios
del sistema. Python 3.14.4, PostgreSQL 18.6 y Redis 8.0.5. PostgreSQL/Redis usan
sockets Unix privados; las pruebas WebSocket levantan Uvicorn en puertos loopback
asignados por el sistema. Evidencia final: `/tmp/chat-persistence-test.2WyFw7`.
Todos los servicios temporales se detienen al terminar.

Comprobaciones nuevas:

- Tickets concurrentes de un solo uso, expiración, Origin, sesión y hash compatible
  con la emisión N2. Se corrigió el hash del handshake inicial: debe usar bytes
  del token, no su representación Base64.
- Sockets reales en dos instancias ASGI, envío stored, cifrado/descifrado del cliente
  de referencia, estado REST, historial, ACK repetido y fingerprint incompatible.
  Las fechas de envío, ACK, cierre y upgrade se verifican en UTC incluso cuando
  PostgreSQL devuelve su zona local.
- Offer no crea evento ni contiene ciphertext; ready vinculado a conexión;
  entrega ephemeral, ACK desde conexión ajena rechazado, ACK tardío, desconexión
  y borrado del payload. PostgreSQL no recibe el contenido efímero.
- ACK de una entrega previa sigue funcionando tras accept/upgrade/close; una
  oferta pendiente cancelada no se convierte en stored por un send tardío.
- Stored offline y reintento tras reconexión; receptor que conecta recupera offers
  vigentes. No se atribuye esta prueba a un proveedor Push.
- Pérdida total de Redis mediante FLUSHDB exclusivamente en la instancia sintética,
  cierre de sockets, nueva autenticación y reconciliación de entregas huérfanas.
- Revocación publicada y revocación sin aviso Pub/Sub; draining cierra 1001 y
  rechaza tickets nuevos en la misma instancia.
- Frames binarios 1003, máximo 1009, cuota persistente 4429, rechazo de accesos
  horizontales y bloqueo por voto/cierre. Offer+send consume una sola cuota lógica.
- Heartbeat nativo con cliente que recibe ping y deliberadamente no responde:
  el servidor cierra al vencer el timeout, sin mensajes JSON de heartbeat.
- Migraciones desde cero y desde 0001/0002/0003/**0004** hasta 0005_delivery_mode.

La repetición final detectó una prueba N2 dependiente del cambio de segundo:
calculaba exp y el helper calculaba iat en instantes distintos, convirtiendo a
veces el TTL inválido de 301 s en uno válido de 300 s. Se fijan ambos extremos
en la prueba; la validación de producción no cambia. La suite unitaria posterior pasa.

Límites: no hay prueba de Docker/HTTPS/WSS/staging ni carga o caída forzada de un
proceso de producción. El estado huérfano se reproduce de forma controlada y
Redis perdido se prueba con datos temporales. Pub/Sub no es una cola durable;
el cliente debe consultar estado/historial tras cortes. Ballots/resolución N6,
transferencia/Push N7, interfaz cliente y operación/homologación N8–N9 pendientes.
No se ha repetido una auditoría de dependencias ni se certifica producción.

## Continuación N4 · 2026-09-21

Resultado: **79 pruebas unitarias y 37 de integración correctas (116 total)**.
Ruff sin errores y mypy estricto sin errores en 33 módulos de servidor/cliente.
Se mantienen los dos avisos de deprecación del cliente Starlette/httpx/AnyIO.
No hay dependencias nuevas. No se repite la auditoría histórica de dependencias
ni se presenta como una comprobación de vulnerabilidades actual.

Servicios temporales PostgreSQL 18.6 y Redis 8.0.5, Python 3.14.4; sin puertos TCP
ni acceso a los datos del proyecto. Evidencia: `/tmp/chat-persistence-test.JVljSA`.
El runner aplica migraciones y detiene los servicios al terminar. Se comprueba:

- Layout exacto de 30 bytes + HMAC de 32, uint32 máximo, 83 caracteres,
  MAC alterado en cada byte, truncamientos, versión/modo inválidos y fuzz determinista.
- Creación concurrente de una única identidad, códigos estables, regeneraciones
  serializadas, revocación de ambas variantes y rollback al agotar generation.
- Canje frente a regeneración y dos usuarios canjeándose mutuamente, sin deadlocks.
- Autenticación, verificación, cuotas por usuario/IP, autocanje, pública inexistente,
  sesión revocada, campos extra y códigos no utilizables.
- Flujo REST real hasta pending/accept/upgrade/close/leave, sin fixtures de conversación;
  aislamiento de terceros y peer=null incluso tras cerrar sin aceptar.
- Accept concurrente crea un solo voto; el plazo es exactamente 30 s y leave no
  modifica el censo. Borrar al invitado antes de aceptar impide una aceptación parcial.
- Upgrade/send y close/send se serializan. El contenido previo efímero no aparece
  en messages tras upgrade; un reintento de mensaje persistido tras close no duplica.
- Migración desde cero y desde 0001, 0002 y **0003** hasta 0004_vote_electorate,
  conservando los hashes y datos anteriores.

La primera ejecución detectó un deadlock real entre el lock de usuario de upgrade
y la FK recipient_user_id de una entrega. Se corrigió con FOR NO KEY UPDATE en
el usuario para las transiciones, manteniendo FOR UPDATE en la conversación;
la suite completa posterior pasa. No se oculta el fallo inicial como éxito.

Límites: se adelanta solo la apertura del voto y su censo. Ballots, resolución y
notificaciones siguen pendientes de N6; el estado open puede persistir tras el plazo.
No hay servidor WS, eventos/cancelación de offers ni interfaz cliente. Siguen
pendientes SMTP real, versiones exactas de Compose, HTTPS/WSS, cobertura/CI,
staging y pruebas de operación. N4 no certifica producción.

## Continuación N2 · 2026-09-17

Resultado: **50 pruebas unitarias y 24 de integración correctas (74 total)**.
Ruff sin errores, mypy estricto sin errores en 19 módulos, compilación Python y
`git diff --check` correctos. `pip check` no detecta dependencias incompatibles y
`pip-audit -r python/requirements.lock --no-deps --disable-pip` no encuentra
vulnerabilidades conocidas en esta consulta. Continúan dos avisos de deprecación
del cliente de pruebas Starlette/httpx/AnyIO, sin fallos.

PostgreSQL 18.6 y Redis 8.0.5 se ejecutaron temporalmente con el runner local,
sin puertos TCP, como en N1. Se recrearon los binarios/entorno de `/tmp` tras el
reinicio del equipo. Evidencia final local: `/tmp/chat-persistence-test.ZTvk0j`.
Los procesos se detienen al terminar. Se probaron:

- BIP-39 inglés de 24 palabras, entropía de 256 bits, NFKD y parámetros exactos
  Argon2id con salts distintos; hashes excluidos de respuestas y frase solo en registro.
- JWT EdDSA, claims, tiempos, issuer/audience, sustitución de algoritmo, kid
  desconocido/ruta inválida y retirada inmediata de una pública.
- Registro, perfil propio, email de un uso, reenvío, vencimiento, cambio de
  email y borrado de cuenta; restricciones case-insensitive de nick/email.
- Refresh concurrente: un ganador, detección de reuse del otro y revocación
  durable del reemplazo, comprobada también con otra instancia del servicio.
- Recuperación y bootstrap concurrente de un uso; sustitución de sesión, logout,
  sesión expirada y publicación real de su revocación por Redis Pub/Sub.
- Si falla la publicación de la revocación, se conserva el commit PostgreSQL.
- Emisión de tickets solo con email verificado, hash en Redis y TTL ≤30 s.
- Privacidad de perfiles en pending y closed sin aceptación previa.
- Rate limiting concurrente real (sliding window/token bucket), 429 HTTP, 503 al
  faltar Redis/PostgreSQL, y rollback completo si falla el adaptador de correo.
- Cuerpo HTTP máximo, extras/JSON inválido, errores correlacionados y no-store;
  cabeceras de cliente ignoradas si el peer no es el proxy de confianza.
- Migraciones desde cero, desde 0001 y desde 0002 hasta 0003, conservando hashes
  email preexistentes al convertirlos de hexadecimal a 32 bytes binarios.

Límites de esta evidencia: ASGITransport prueba los endpoints sin ejecutar su
lifespan; la validación de secretos/arranque tiene pruebas separadas. El correo
se captura en un buzón de prueba y SMTP/STARTTLS se verifica con un doble: **no
hay prueba con un proveedor de correo real**. Tampoco hay todavía servidor WS
para consumir tickets/cerrar sockets revocados. Quedan pendientes versiones
exactas de Compose, HTTPS/WSS real, cobertura porcentual/CI y puertas N0/N8/N9.
La auditoría de dependencias no equivale a una auditoría de imagen o de aplicación.

## Continuación N1 · 2026-09-16

Se ejecutan **32 pruebas unitarias y 14 de integración**, todas correctas, con
Python 3.14.4 del entorno existente. Ruff y mypy estricto pasan (10 módulos de
`chat`); sintaxis del script local correcta. Persisten los dos avisos de
deprecación del cliente Starlette/httpx/AnyIO, sin fallos.

Las pruebas de integración usan **PostgreSQL 18.6 y Redis 8.0.5 reales**, mediante
binarios oficiales extraídos en `/tmp`, sin instalar servicios del sistema. Cada
ejecución de `scripts/test_local_services.sh` crea un clúster nuevo y un Redis sin
persistencia, usa sockets Unix privados, aplica migraciones y detiene los procesos
al salir. No se han modificado los datos ni los secretos de la aplicación.

Comprobado con esos servicios:

- Alembic desde base vacía y actualización 0001→0002 conservando la conversación
  preexistente y rellenando updated_at desde created_at.
- Commit/rollback de evento, contenido, entregas y gracia; duplicado compatible
  recupera estado delivered incluso tras close; fingerprint incompatible falla.
- Ocho reintentos simultáneos crean un único mensaje; ocho envíos adicionales
  respetan el máximo global de cinco mensajes de gracia.
- UUID concurrente desde conversaciones distintas produce un único ganador;
  creación concurrente de invitación activa respeta el índice único.
- Envío espera el lock de cierre; PostgreSQL muestra la espera en pg_locks y el
  envío se rechaza tras commit del cierre.
- Ephemeral no crea contenido PostgreSQL y un retry tras upgrade tampoco lo crea.
- Historial excluye caducados antes de la purga, aplica pertenencia incluso con
  cursor y pagina correctamente mensajes con el mismo sent_at.
- Constraints reales de crypto_meta, host único, uint32 de generation y nick único.
- Purga separada de contenido/metadatos, cascada de deliveries y conservación del
  historial refresh hasta el vencimiento de toda la familia más 30 días.

**Límite:** Docker vuelve a devolver `permission denied` también con permisos
ampliados. No se acreditan N0, el runtime Python 3.13.12/PostgreSQL 17.9/Redis 8.6.6
de Compose, ni el cierre de N1. Faltan repositorios específicos de flujos futuros y
las puertas de aceptación del despliegue. No hay endpoints nuevos de producto.
No se han repetido auditoría de dependencias, Caddy ni escaneo de imágenes: no
se modificaron dependencias o imágenes en esta continuación.

## Evidencia histórica de infraestructura

Los resultados y limitaciones siguientes corresponden a la entrega anterior;
la ejecución de PostgreSQL/Redis locales descrita arriba amplía esa evidencia.

Diagnóstico ya aportado por el usuario: Caddy se reinicia con ExitCode=255 y
`exec /usr/bin/caddy: operation not permitted`; no alcanza a ejecutar healthchecks.
Se corrige el conflicto entre las file capabilities de la imagen oficial y
`cap_drop: ALL` mediante `caddy/Dockerfile`, retirando la capacidad en el build.
El build comprueba que `getcap` quede vacío y ejecuta `caddy version`. Estas dos
comprobaciones se ejecutarán en el host al construir: Docker sigue inaccesible
desde este entorno, por lo que todavía no se afirma que el contenedor esté sano.
La reproducción anterior usaba el binario descargado sin file capabilities y,
por tanto, no reproducía esta restricción de la imagen oficial.

Diagnóstico posterior de `chat-caddy-1 is unhealthy`: el healthcheck original
respondió correctamente en una reproducción con Caddy 2.11.4. Se verificó en el
registro que la imagen exacta incluye curl. El nuevo script se probó contra ese
Caddy real en puertos temporales: devuelve éxito con proxies inválidos, falla
ante HTTP erróneo y falla cuando el proceso se detiene, conservando diagnóstico.
Compose producción/local, sintaxis sh y las tres pruebas de Compose pasan.
En ese momento faltaban los logs y State del usuario; el diagnóstico y la corrección
posteriores se recogen al principio de este documento. El Docker de ese host sigue
inaccesible desde este entorno.

Corrección posterior al conflicto de puerto: se confirmó un listener en 8080 y
se configuró 127.0.0.1:18080 como publicación local. Las tres variantes de Compose
son válidas y las 24 pruebas vuelven a pasar. Comprobados también un puerto alternativo,
la sincronización CORS en desarrollo, el rechazo de puertos inválidos y que esa
opción no cambie los orígenes de producción. El acceso al socket Docker continúa
denegado en este entorno; el nuevo arranque debe ejecutarse en el host del usuario.

Corrección posterior al error `invalid mount path: 'mode=1777'`: se comprobó
que YAML separaba las opciones de `tmpfs` en dos elementos. Los montajes de
Python/migrate/worker y Caddy usan ahora listas de bloque. Verificadas las tres
combinaciones de Compose, las tres pruebas de Compose (con comprobación de rutas
absolutas añadida), Ruff del archivo modificado y los comandos `make start/stop`
mediante ejecución en seco. No se ha ejecutado un nuevo arranque de contenedores.

Fecha: 2026-09-16. Estos resultados verifican la **base de infraestructura**,
no acreditan las puertas funcionales del MVP.

| Comprobación | Resultado |
|---|---|
| Compose producción | `docker compose config --quiet` correcto |
| Compose local y combinación con test | Configuración combinada correcta |
| Pruebas de configuración, secretos, health, logs, espera y Compose | **24 passed**, 1 prueba de integración excluida |
| Ruff | Sin errores |
| Mypy estricto | Sin errores en 9 archivos de `chat` |
| Compilación Python y sintaxis de scripts sh | Correctas |
| Caddy 2.11.4 oficial | Configuraciones local/producción adaptadas y validadas correctamente |
| SQL PostgreSQL con pglast 8.4 | 35 sentencias en 0001 y 12 en 0002 analizadas correctamente |
| Dependencias runtime (`pip-audit`) | Ninguna vulnerabilidad conocida encontrada en la consulta |
| Imágenes oficiales | Todas las versiones resuelven en el registro; digest fijado |
| DOCX original | Copia íntegra con SHA-256 en `ESPECIFICACION.sha256` |

Las pruebas se ejecutaron con Python3.14.4 del host; el contenedor está fijado a
Python3.13.12 y falta su ejecución real. Dos avisos de deprecación pertenecen al
cliente de pruebas Starlette/httpx/AnyIO; no fueron fallos. El aislamiento inicial
bloqueó los sockets internos del TestClient; las pruebas completas pasaron fuera
de esa restricción. Los procesos de prueba bloqueados se terminaron.

## No comprobado en este entorno

El CLI Docker está instalado, pero el acceso a `/var/run/docker.sock` devolvió
`permission denied`, también al intentar ejecutarlo con permisos ampliados.
Por tanto **no se han construido ni arrancado los contenedores**. En la entrega
inicial tampoco se había ejecutado Alembic contra PostgreSQL real; esa parte se
ha comprobado ahora con los servicios temporales descritos arriba. Sigue pendiente
repetirlo con las versiones exactas de producción.

Quedan pendientes en un host con acceso Docker:

- `sh scripts/test_containers.sh` para migraciones y retención con las imágenes fijadas.
- Arranque limpio, persistencia PostgreSQL, reinicio volátil Redis, migración
  fallida que impida la API, reinicio sin Compose y recuperación de readiness.
- HTTPS/WSS real, certificados, proveedores SMTP/Push, escaneo de imágenes.
- Suite funcional/E2E/concurrencia/cobertura/carga, backups/restauración y alertas,
  conforme se implementen los niveles N2–N9.

Las comprobaciones de Caddy usaron una copia temporal con rutas de importación
ajustadas al host. No se solicitaron certificados ni se publicó ningún servicio.
La auditoría de dependencias es una observación temporal, no sustituye la CI
exigida antes de cada release.
