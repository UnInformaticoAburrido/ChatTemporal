# Navegador con backend real

La suite `web/integration/client.spec.mjs` abre Chromium contra FastAPI por HTTP y
WebSocket reales, con PostgreSQL migrado y Redis vacíos. No intercepta REST ni WS.
Se ejecutan tres recorridos en escritorio y con viewport Pixel 7:

- Registro, verificación, copia `.chatkey`, publicación inicial, invitación stored,
  cifrado/descifrado, ACK, gracia conservada, reconexión con ticket nuevo,
  bloqueo/restauración de clave, historial y purga de contenido caducado.
- Invitación ephemeral, protocolo offer/ready, ACK sin ciphertext durable,
  gracia rechazada con abstención y upgrade irreversible a stored.
- Dos contextos de navegador, emisor Ed25519 y exchange reales, revocación del
  predecesor, PUT por permiso residual, recepción cifrada de la misma clave y
  borrado de Redis únicamente después de descargar y reabrir la copia.

El inspector comprueba estados de entrega, ausencia de marcadores de plaintext
en ciphertext/meta persistidos y una sola sesión normal. Las pruebas observan
que los mensajes y el secreto de transferencia no salen por los cuerpos de la
API de Chat; las credenciales de Chat no se guardan en local/sessionStorage.
Esto verifica esos canales concretos, no constituye una auditoría general.

## Comando con Docker

Requiere Python con las dependencias de desarrollo, Node 22+ y Docker Compose.
Desde la raíz:

```sh
.venv/bin/python -m pip install -r python/requirements-dev.lock
npm ci --ignore-scripts --prefix web
(cd web && npx --no-install playwright install chromium)
.venv/bin/python scripts/test_web_containers.py
```

También `make test-web-e2e` dentro del entorno Python activado. El runner crea un
proyecto Compose con UUID nuevo y únicamente PostgreSQL/Redis, usando las imágenes
fijadas en `docker-compose.yml`. Genera secretos propios en un directorio temporal
0700; no ejecuta `init_local.py`, no consulta `secrets/` ni monta volúmenes durables.
Publica las dependencias y ambos servidores solo en loopback con puertos libres.
Migra, ejecuta el navegador y solicita `down` de su propio proyecto al terminar,
incluso si falla la suite. Un cierre forzoso del proceso/daemon puede requerir
limpiar ese proyecto por su nombre; nunca ejecutar `down` del Chat instalado.

CI añade el job `web_e2e`, separado de `web` (contratos y UI interceptada), `tests`
(backend, arranque, Caddy HTTPS/WSS) y `security`. No se suben trazas, capturas,
vídeos ni snapshots de estas cuentas. La configuración Playwright exige el runner
local de prueba y no permite ejecutarla contra un dominio público.

## Alternativa sin Docker

El runner existente inicializa PostgreSQL/Redis nuevos mediante sockets Unix
privados. Hay que aportar binarios compatibles; no instala servicios del host:

```sh
CHAT_TEST_PG_BIN=/ruta/a/postgresql/bin \
CHAT_TEST_REDIS_SERVER=/ruta/a/redis-server \
CHAT_TEST_PYTHON="$PWD/.venv/bin/python" RUN_WEB_E2E=1 \
sh scripts/test_local_services.sh tests/test_web_client_integration.py -s --tb=short
```

Si esos binarios necesitan bibliotecas privadas, añadir su `LD_LIBRARY_PATH`.
Al terminar se detienen los servicios y el directorio de evidencia local queda
bajo `/tmp/chat-persistence-test.*`, con datos únicamente sintéticos. No debe
usarse una base existente: la fixture exige `APP_ENV=test`, opt-in explícito,
esquema actual y tabla de usuarios vacía.

## Alcance de la fixture

El buzón Mailbox sustituye únicamente el transporte SMTP; el registro y la
verificación pasan por la API real. El emisor representa una sesión autenticada
de main-app mediante una cookie sintética HttpOnly limitada a `/bootstrap`.
Esa cookie pertenece al emisor de prueba y nunca contiene credenciales de Chat.
No conecta la aplicación principal real ni prueba su CSRF o COOP definitivo.

Se ejecuta el mantenimiento real sin el bucle de envíos Push. Los controles de
prueba viven exclusivamente dentro de `python/tests/test_web_client_integration.py`,
requieren loopback y una capacidad aleatoria que solo conoce el runner Node;
no se añaden endpoints a producción. Se esperan los 30 segundos normativos de
votación, manteniendo las cuotas autenticadas de producción. Se adelanta únicamente
el TTL del mensaje sintético y se consulta de nuevo el historial tras la purga.
La política de producción no cambia; mantenimiento tiene intervalo de 2 s y la
cuota de registro por IP admite 100 cuentas solo en esta fixture (toda la suite
comparte loopback).

El servidor estático aplica la lista pública y CSP de Caddy, pero aquí no se
arranca Caddy ni se valida TLS. El lifespan/validación de secretos de producción
no se ejecuta: lo cubren las pruebas de arranque y el smoke de contenedores.
La transferencia usa la entrada manual del descriptor/QR. QR óptico, cámara
física, navegadores/dispositivos finales, SMTP/Push externos, integración real de
main-app, staging HTTPS/WSS y capacidad ≥2× del pico siguen pendientes de N9.
