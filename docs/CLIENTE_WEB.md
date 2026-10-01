# Cliente web · identidad y próximos bloques

Actualizado 2026-10-01. Identidad y claves del cliente final; no completa N9.
Se adopta una web adaptable como opción inicial de implementación, pendiente de
una preferencia explícita de plataforma. No se atribuye esa elección al usuario.

## Disponible

Caddy sirve `web/public/` en `/`, con API en el mismo origen. HTML, CSS y módulos
JavaScript nativos; libsodium se sirve como bundle local versionado para claves.
No hay CDN ni servicio Node en producción. Solo se exponen los archivos públicos
enumerados en las rutas; CI verifica el bundle contra el lock.
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

Tokens únicamente en memoria de esta página, sin cookies/localStorage/sessionStorage.
Cerrar o recargar requiere acceder otra vez. Esta limitación se muestra en la UI.
`pagehide` limpia credenciales y pantalla; volver desde bfcache no restaura secretos.
La frase BIP-39 recupera identidad, **no sustituye las claves de descifrado**.
La web permite generar una clave X25519 inicial, descargar su copia cifrada y
volver a abrirla antes de publicar la pública. Si ya existe una clave, exige una
copia coincidente y no la sustituye. Detalles en [CLAVES_CLIENTE.md](CLAVES_CLIENTE.md).

## Abrir y probar

Tras `make up` (o `make start` si las imágenes ya existen), abrir
`http://localhost:18080/`, o el puerto elegido con CHAT_HTTP_PORT.
El frontend no evita el requisito SMTP: con el proveedor local sin configurar,
registro falla con 503 y el backend revierte la creación; la UI informa del fallo.

Pruebas de desarrollo (Node 22+; CI fija 24.21.0, Playwright 1.63.0):

```sh
npm ci --ignore-scripts --prefix web
.venv/bin/python -m pip install -r python/requirements-client.lock
npm run check:crypto --prefix web
CHAT_TEST_PYTHON="$PWD/.venv/bin/python" npm test --prefix web
cd web
npx --no-install playwright install chromium
npm run test:browser
```

Las unitarias usan el [runner nativo de Node](https://nodejs.org/api/test.html).
Pruebas de contratos/sesión, copias cifradas e interoperabilidad real con Python;
recorridos de identidad y claves en escritorio y viewport móvil. Los escenarios de navegador interceptan la API con
datos sintéticos: prueban UI, errores, manejo de secretos y estados; **no se
presentan como E2E con SMTP real**. CI mantiene la integración del backend con
PostgreSQL/Redis y añade comprobaciones de assets/cabeceras/404 por Caddy HTTPS.
No se publican traces, vídeos ni capturas con frases/tokens. Se audita el lock npm.

## Trabajo siguiente y aceptación

| Orden | Trabajo | Terminado cuando |
|---|---|---|
| 1 | Implementado: claves en memoria con copia cifrada portable y publicación inicial condicional | Interoperabilidad Python, restauración y rechazo de sustituciones comprobados; falta homologar dispositivos finales |
| 2 | Listado, invitaciones, canje y aceptación | Dos usuarios pueden iniciar una conversación respetando anonimato pending y roles |
| 3 | Mensajería stored/ephemeral y reconexión | Cifrado local, ACK tras descifrar, historial, estados dudosos y reintentos correctos desde la UI |
| 4 | Gracia, votación, cierre y upgrade | Bloqueo de 30 s y resultado aplicado también al historial local ephemeral |
| 5 | Transferencia QR, replay y Push | Cambio de dispositivo y recepción genérica comprobados en navegador real |
| 6 | Entrada bootstrap y sesiones durables según plataforma | Integración con app principal y política de custodia aprobada/ensayada |
| 7 | E2E y staging | Criterios §22/§31 completos con proveedores reales y carga representativa |

La custodia inicial usa copias cifradas portables y privadas en memoria. Cualquier
persistencia automática futura y las sesiones durables requieren diseño separado. Grupos, adjuntos y
multi-dispositivo permanente continúan fuera del MVP v1.
