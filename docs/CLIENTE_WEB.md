# Cliente web Nexo

Actualizado 2026-10-15. Identidad, claves, chat y Push del cliente final; no completa N9.
Se adopta una web adaptable como opción inicial de implementación, pendiente de
una preferencia explícita de plataforma. No se atribuye esa elección al usuario.

## Disponible

La apariencia sigue los PDF de Nexo aportados por el usuario: bienvenida azul
noche, formularios de registro/acceso en modal, perfil con tarjetas de invitación
y chat con lista/buscador y conversación. El acceso mantiene email y 24 palabras,
con campos numerados y pegado de la frase completa. El perfil permite editar
nick/email; los avatares siguen usando iniciales. Navegar entre cuenta y chat no
bloquea ni sustituye una clave abierta. Enter envía, Shift+Enter añade una línea;
los mensajes entrantes no desplazan al usuario que está leyendo arriba.
Detalles y compatibilidad en [NEXO_UI.md](NEXO_UI.md), con revisión en
[design-qa.md](../design-qa.md). Para cargar rutas/cabeceras nuevas en el Docker
local: `sudo make start reload-web`.

Caddy sirve `web/public/` en `/`, con API en el mismo origen. HTML, CSS y módulos
JavaScript nativos; libsodium y QR se sirven como bundles locales versionados.
No hay CDN ni servicio Node en producción. Solo se exponen los archivos públicos
enumerados en las rutas; CI verifica ambos bundles contra el lock.
CSP sin scripts/estilos inline, no-store, no-referrer y protección contra iframes.
La configuración sigue el [patrón oficial de Caddy](https://caddyserver.com/docs/caddyfile/patterns).

- Registro con nick y correo; el backend real debe poder enviar la verificación.
- Presentación única de las 24 palabras, confirmación obligatoria de guardado y
  retirada del DOM al continuar. No se copian automáticamente al portapapeles.
- Verificación y reenvío de código, consulta del estado de la cuenta.
- Recuperación con correo y frase; avisa de la revocación de otras sesiones.
- Cierre de sesión con aviso si no se pudo confirmar la revocación remota.
- Renovación de tokens en una única petición compartida por llamadas simultáneas.
  Un refresh de resultado dudoso elimina la sesión local, sin reenvío automático.
- Errores traducidos por código; datos de usuario como texto, sin HTML interpolado.
- Cabecera visible en escritorio y móvil: «Sin sesión» o «Sesión iniciada · nick».
  Al acceder se retira la introducción de registro y la cuenta usa todo el espacio;
  al cerrar o perder la sesión se recupera la pantalla de acceso.
- Botón visible en la cuenta verificada «Obtener código para invitar», con copia
  de invitaciones stored/ephemeral y renovación explícita. No requiere desbloquear
  la privada si ya existe una pública publicada. Si falta la pública, lleva a crear
  y comprobar la copia antes de compartir una invitación que el otro pueda canjear.
- Conversaciones y canje de invitaciones después de abrir la copia de claves.
  Canje, renovación, aceptación del host, cierre, salida y upgrade irreversible.
  Pending conserva anonimato: se obtiene la clave del interlocutor sin UUID/nick.
- Envíos cifrados localmente, hasta 256 puntos de código. Stored consulta estado
  REST y reintenta el mismo UUID/nonce/ciphertext; ephemeral negocia offer/ready
  y exige un nuevo envío manual tras un fallo. ACK únicamente tras descifrar.
- Reconexión con tickets nuevos y backoff de 1–30 s; sincronización cada 5 s,
  historial paginado y recuperación de páginas recientes después de un corte.
- Votación de conservación de gracia: bloqueo de envío, voto definitivo y
  resolución REST autoritativa. El rechazo elimina la gracia también en salas
  no seleccionadas; avisos atrasados no reabren resultados terminales.
- Replay de una copia local cifrada a un interlocutor conectado, con aplicación
  al recibir end y conteo exacto.
- Entrada manual por código bootstrap de la aplicación principal, sin tokens en URL.
- Adaptador de apertura automática desde main-app: emisor Ed25519 reutilizable,
  intercambio entre ventanas con origen/source/nonce comprobados y una sola
  entrega de token en memoria. Apagado hasta configurar `bootstrap_web_origins`;
  conectar el controlador autenticado y el mapeo de UUID en la aplicación real.
  Ver [integración y contrato](INTEGRACION_MAIN_APP.md).
- Transferencia cifrada de claves entre dispositivos: preparación del origen,
  solicitud/QR privado, lectura por imagen/cámara/texto y coincidencia de claves.
  Exige descargar y abrir la copia recibida antes de borrar el blob del servidor.
  Ver [recorrido y límites](TRANSFERENCIAS_WEB.md).
- Notificaciones Web Push voluntarias: permiso por gesto, alta vinculada a la
  sesión y baja tanto en servidor como navegador. Avisos con texto fijo, sin
  nombres ni contenido; enfocar la página abierta conserva la clave en RAM.
  Alta/baja dudosas requieren confirmación, sin afirmar éxito ni renovar otra
  cuenta. Registro real del worker comprobado en Chromium con proveedor simulado.
  Ver [custodia y límites](PUSH_WEB.md).

Tokens únicamente en memoria de esta página, sin cookies/localStorage/sessionStorage.
La suscripción Push nativa y su worker permanecen en el navegador al cerrar la
pestaña, sin tokens de acceso ni privadas de chat. Logout intenta eliminarla y
revoca la sesión en servidor; exchange/recover dejan la antigua sin entrega.
Cerrar o recargar requiere acceder otra vez. Esta limitación se muestra en la UI.
`pagehide` limpia credenciales y pantalla; volver desde bfcache no restaura secretos.
La frase BIP-39 recupera identidad, **no sustituye las claves de descifrado**.
La web permite generar una clave X25519 inicial, descargar su copia cifrada y
volver a abrirla antes de publicar la pública. Si ya existe una clave, exige una
copia coincidente y no la sustituye. Detalles en [CLAVES_CLIENTE.md](CLAVES_CLIENTE.md).
La gestión de claves muestra «Tu clave pública» y permite copiarla incluso si
la copia privada está bloqueada. El código de invitación es lo que debe compartir
el usuario para iniciar una conversación. Nunca se muestran privadas sin cifrar.
Mensajes y ciphertext pendientes también viven solo en RAM; bloquear la clave,
cerrar sesión o abandonar la página borra la vista y detiene sockets/temporizadores.
Cada conversación conserva como máximo 500 mensajes consultados, indicado al
alcanzar el límite. El replay recuperado se etiqueta como copia compartida: no
pretende reconstruir el autor original. No hay cola durable del cliente.
Los mensajes stored con fecha de caducidad REST se retiran en la sincronización;
una pérdida de acceso borra la conversación local. Una salida de resultado incierto
se reconcilia antes de quitar su contenido.

## Abrir y probar

Tras `make up` (o `make start` si las imágenes ya existen), abrir
`http://localhost:18080/`, o el puerto elegido con CHAT_HTTP_PORT.
El override local incluye el buzón Mailpit en `http://127.0.0.1:18025/` para los
códigos de verificación, cuando el secreto SMTP conserva el marcador inicial.
Con un SMTP real se utiliza ese proveedor; un fallo de correo provoca 503 y
rollback del registro. Producción necesita un proveedor externo configurado.

Pruebas de desarrollo (Node 22+; CI fija 24.21.0, Playwright 1.63.0):

```sh
npm ci --ignore-scripts --prefix web
.venv/bin/python -m pip install -r python/requirements-client.lock
npm run check:crypto --prefix web
npm run check:qr --prefix web
CHAT_TEST_PYTHON="$PWD/.venv/bin/python" npm test --prefix web
cd web
npx --no-install playwright install chromium
npm run test:browser
```

Las unitarias usan el [runner nativo de Node](https://nodejs.org/api/test.html).
Pruebas de contratos/sesión, copias cifradas e interoperabilidad real con Python;
recorridos de identidad, claves, chat y transferencias en escritorio y viewport móvil. Los escenarios de navegador interceptan REST y WebSocket con
datos sintéticos: prueban UI, errores, manejo de secretos y estados; **no se
presentan como E2E con SMTP real**. CI mantiene la integración del backend con
PostgreSQL/Redis y añade comprobaciones de assets/cabeceras/404 por Caddy HTTPS.
No se publican traces, vídeos ni capturas con frases/tokens. Se audita el lock npm.

Una suite separada valida la UI con API/WS, PostgreSQL y Redis reales en un
entorno nuevo: `python scripts/test_web_containers.py` (job `web_e2e`). Cubre
stored/ephemeral, votos, reconexión, TTL y transferencia entre dos contextos con
bootstrap firmado. El buzón de correo y la sesión del emisor son de prueba;
proveedores y staging siguen pendientes. Comandos y límites en [E2E_WEB.md](E2E_WEB.md).

## Trabajo siguiente y aceptación

| Orden | Trabajo | Terminado cuando |
|---|---|---|
| 1 | Implementado: claves en memoria con copia cifrada portable y publicación inicial condicional | Interoperabilidad Python, restauración y rechazo de sustituciones comprobados; falta homologar dispositivos finales |
| 2 | Implementado: listado, invitaciones, canje y aceptación | Recorridos UI con backend real; homologación SMTP/dispositivos pendiente |
| 3 | Implementado: mensajería stored/ephemeral y reconexión | Cifrado, ACK, historial y reconexión UI con backend real; errores/reintentos también con API interceptada; falta staging |
| 4 | Implementado: gracia, votación, cierre y upgrade | Bloqueo y resultado local ephemeral comprobados; falta homologación de cortes reales |
| 5 | Implementado: transferencia QR, replay y Push web | Transferencia y worker comprobados en navegador con API/proveedor interceptados; faltan cámara óptica y recepción Push real |
| 6 | Entrada bootstrap manual, adaptador automático y emisor implementados; conexión real y sesiones durables pendientes | Controlador autenticado/mapeo de la aplicación principal, origen HTTPS y política de custodia homologados |
| 7 | E2E y staging | Criterios §22/§31 completos con proveedores reales y carga representativa |

La custodia inicial usa copias cifradas portables y privadas en memoria. Cualquier
persistencia automática futura y las sesiones durables requieren diseño separado. Grupos, adjuntos y
multi-dispositivo permanente continúan fuera del MVP v1.

## Conversaciones como apartado propio

La cabecera «Conversaciones» abre `/#chat`. Antes de desbloquear la clave privada
se muestra la lista de conversaciones activas y pendientes, con actualización
periódica y paginación. Seleccionar una guía a abrir la copia cifrada y después
carga esa conversación. «Abrir mis mensajes» permite preparar las claves sin
seleccionar todavía una conversación. Se mantienen las claves y mensajes en RAM
al alternar entre cuenta y chat durante la misma sesión.

«Nueva conversación» abre un modal para introducir el código de otra persona.
Este recorrido pide **la invitación efímera**, y envía `expected_mode: ephemeral`.
El backend valida el modo firmado antes de crear nada; si se pega una invitación
con historial devuelve `INVITATION_MODE_MISMATCH` sin consumir un uso. Los clientes
anteriores que omitan el nuevo campo conservan su contrato de canje.

Después del canje se selecciona la conversación. El destinatario la ve al
actualizar su lista o recibir la actualización periódica. Se mantienen la
aceptación por el anfitrión y las reglas existentes de mensajes de gracia/voto.
Con las claves abiertas, seleccionar una conversación presenta exclusivamente
sus mensajes; el compositor envía por botón o Enter, con Shift+Enter para saltos.

En modo efímero ambos usuarios necesitan estar conectados. No se escribe el
payload en la tabla `messages` ni se consulta historial remoto. El servidor actúa
como relevo temporal de contenido cifrado y conserva metadatos de entrega; no es
una conexión directa entre navegadores. Los textos descifrados y el historial
local permanecen en memoria y se pierden al cerrar, recargar o cerrar sesión.
Las conversaciones previas con historial conservan su modo y se identifican en
la cabecera. Su existencia no cambia la política de las nuevas invitaciones.

### Aplicar esta actualización local

Los fuentes Python y web están montados desde el proyecto. Como cambia también
la validación del API, hay que recargar el proceso Python antes de usar el nuevo
formulario; no hace falta migración ni reconstruir imágenes:

```bash
sudo docker compose -f docker-compose.yml -f docker-compose.local.yml restart python worker
sudo make reload-web
```

Después recarga `http://localhost:18080/` y vuelve a acceder. No reiniciar ni
borrar PostgreSQL para aplicar esta función.

### Pruebas

`web/tests/conversations.spec.mjs` cubre listado antes de desbloquear, selección
guiada, rechazo de invitaciones con historial, intercambio cifrado entre dos
páginas y aislamiento al cambiar de conversación, tanto en escritorio como en
móvil. El relevo de ese ensayo es sintético. Las pruebas de integración existentes
con PostgreSQL/Redis verifican por separado la ausencia de ciphertext durable
efímero; requieren acceso a Docker y no se sustituyen por el ensayo sintético.
