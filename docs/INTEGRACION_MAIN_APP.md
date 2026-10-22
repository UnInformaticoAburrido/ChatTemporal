# Integración de acceso desde la aplicación principal · 2026-10-05

El receptor web, el launcher y la ayuda de emisión están implementados y probados.
La conexión con una aplicación principal real sigue pendiente de su repositorio,
controlador de sesión, mapeo de cuentas y origen HTTPS. No se configura un origen
de ejemplo como emisor de confianza ni se modifica otro proyecto.

## Piezas y contrato

- `python/chat_client/bootstrap.py`: `BootstrapIssuer`, para el servidor main-app.
  Usa PyJWT/cryptography ya fijados en los locks del proyecto. La API de chat no lo
  instancia; no se añade un endpoint público que firme tokens ni una privada al
  navegador o al contenedor Chat API.
- `integrations/main-app/launch-chat.mjs`: módulo autónomo que main-app puede
  copiar/servir desde su origen. `launchChat` abre el chat por gesto, espera el
  receptor, pide un token mediante el callback autenticado y entrega uno solo.
- `web/public/bootstrap.js`: receptor de ventana con lista explícita de orígenes,
  comprobaciones de ventana/nonce/protocolo y plazo de 60 s.
- `GET /api/v1/auth/bootstrap-config`: metadata pública `{origins,protocol_version:1}`
  con `Cache-Control: no-store`. No publica secretos ni autoriza intercambio.

El token conserva el contrato §24.4: JWT Ed25519/EdDSA, `typ=JWT`, `kid` y claims
exactos `sub,iss,aud,iat,nbf,exp,jti`. `iss=main-app`, `aud=chat-api`, TTL de 1–300 s
(y no mayor que el máximo configurado en Chat API), UUID canónico de una cuenta de
chat existente y jti nuevo. Redis consume el jti de forma atómica. Exchange revoca
las sesiones previas y concede al predecesor únicamente el permiso de PUT para
trasladar claves al nuevo dispositivo; no mantiene dos sesiones normales.

## Conectar el servidor principal

La aplicación principal debe resolver el UUID de chat a partir del usuario de su
sesión autenticada. No aceptar `sub`/UUID libre del body, query o JavaScript. Este
adaptador no registra ni vincula cuentas automáticamente. Un usuario sin mapeo
requiere completar ese flujo de negocio en main-app antes de emitir.

En su controlador autenticado, después de verificar sesión, origen/CSRF y cuota:

```python
from pathlib import Path
from chat_client.bootstrap import BootstrapIssuer

issuer = BootstrapIssuer(
    Path("/run/secrets/main_bootstrap_private"),
    kid="main-2026-10",
    ttl_seconds=60,
)
# mapped_chat_user_id es UUID obtenido del usuario autenticado por el servidor.
bootstrap_token = issuer.issue(mapped_chat_user_id)
```

Responder `{bootstrap_token}` con `Cache-Control: no-store`. No imprimir token,
privada ni excepciones de firma; no registrar bodies de este controlador. La
validación de sesión/CSRF y las cuotas pertenecen a main-app y no pueden suplirse
con el launcher. Mantener su privada Ed25519 independiente de la del Chat API.
Instalar únicamente la pública como `<kid>.pem` en `BOOTSTRAP_PUBLIC_KEYS_DIR`.
Rotación/retirada de kid sigue el contrato existente. Las claves locales generadas
por tests son sintéticas y no configuran una integración de producción.

En los settings TOML del Chat API, declarar el origen real canónico, sin ruta,
slash final, credenciales, query, fragmento ni puerto por defecto explícito:

```toml
bootstrap_web_origins = ["https://app.example.com"]
```

La lista vacía apaga el receptor automático. No reutilizar `allowed_origins`: CORS
y permiso para entregar un bootstrap son decisiones diferentes. HTTP solo se
acepta para loopback en desarrollo/test; producción exige HTTPS. Reiniciar la API
tras cambiar su configuración. Esta metadata no debilita la verificación JWT:
incluso una ventana del origen permitido necesita un token firmado válido.

## Conectar el botón de main-app

Copiar el módulo `launch-chat.mjs` al frontend de main-app y llamar desde un clic:

```javascript
import { launchChat } from './launch-chat.mjs';

button.addEventListener('click', () => {
  const launch = launchChat({
    chatOrigin: 'https://chat.example.com',
    createBootstrapToken: async () => {
      const response = await fetch('/chat/bootstrap', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        redirect: 'error', referrerPolicy: 'no-referrer',
        headers: { 'X-CSRF-Token': csrfToken },
      });
      if (!response.ok) throw new Error('Bootstrap unavailable');
      return (await response.json()).bootstrap_token;
    },
  });
  launch.result.then(showChatOpened, showChatAccessError);
});
```

`button`, `csrfToken` y los callbacks proceden de main-app; adaptar ruta y cabecera
CSRF a su autenticación real. Los orígenes del ejemplo no están habilitados por
defecto. `launch.result` resuelve sin devolver access/refresh/perfil. Fallos tienen
un código estable de `ChatLaunchError`; el consumidor lo traduce sin mostrar datos
del servidor. `launch.cancel()` retira el emisor local y no cierra un chat que ya
esté abierto. Evitar un segundo clic mientras haya una apertura pendiente.

La apertura usa `/#main-app`, un marcador público que se elimina inmediatamente
del historial. No poner el bootstrap en query, fragmento, window.name, storage,
cookies de chat ni HTML. La ventana del chat no acepta tokens del emisor mientras
ya tenga una sesión abierta; este adaptador abre una ventana nueva por intento.

## Protocolo entre ventanas

1. Chat consulta la lista de orígenes configurada y captura la ventana opener.
2. Genera un UUIDv4 aleatorio y envía `chat.bootstrap.ready`, versión 1 y
   `request_id`, a cada origen exacto configurado; solo puede entregarse al opener
   que en ese momento tenga ese origen. No se usa `*`.
3. Launcher verifica el origen del chat y que `event.source` sea su popup. Emite
   una vez y entrega `chat.bootstrap.token`, versión, request_id y bootstrap_token.
4. Chat exige origen autorizado, la misma ventana opener, nonce y campos exactos.
   Otra ventana del mismo origen tampoco puede entregar un token. Retira listener
   y separa opener antes de intercambiar; un duplicado no causa otro POST.
5. Main-app recibe únicamente `chat.bootstrap.result` con accepted/failed. Accepted
   confirma exchange; una consulta de perfil posterior puede fallar y reintentarse
   dentro del chat sin otra emisión. No implica claves abiertas ni sesión durable.

Se aplican las comprobaciones de origen/source y targetOrigin exacto documentadas
en [Window.postMessage](https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage).
Tokens siguen brevemente en RAM/structured clone; no se garantiza borrado físico
del navegador ni protección frente a XSS en un origen autorizado.

No usar `noopener`/`noreferrer` en esta apertura: impedirían el canal. Revisar las
cabeceras de main-app antes de activar; COOP puede separar las ventanas. No se
relajan cabeceras de una app ajena desde este proyecto. Si popup, COOP u origen
impiden la entrega, el chat muestra el formulario manual. Referencias:
[window.open](https://developer.mozilla.org/en-US/docs/Web/API/Window/open) y
[COOP](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cross-Origin-Opener-Policy).

## Fallos y validación pendiente

Cancelar en el chat, pagehide o configuración tardía retiran la recepción y
separan opener. El timeout muestra la alternativa manual. La cancelación/deadline
del launcher impiden transmitir un token cuya emisión termine después. Un
exchange de respuesta perdida no se reintenta y no confirma sesión; volver a
abrir desde main-app debe emitir otro jti, o usar un código nuevo manualmente.

Las pruebas Chromium usan dos orígenes locales y ventanas reales, con REST/token
interceptados. Comprueban consumo único, otra ventana del origen permitido,
cancelación, secretos fuera de URL/DOM/storage y perfil temporalmente caído. Las
integraciones separadas firman con el emisor real y verifican exchange/jti/revocación
contra PostgreSQL/Redis. No constituyen E2E con el controlador real de main-app.
Sus fixtures no se exponen por las rutas públicas de Caddy.

Quedan conectar el controlador autenticado/mapeo real, instalar pública y origen,
comprobar COOP/popups en el navegador final, homologar sustitución de sesión y
transferencia, y ejecutar UI/API/proveedores/carga en staging. Sesiones durables
y persistencia automática de privadas mantienen un diseño separado.
