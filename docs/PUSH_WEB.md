# Notificaciones Web Push del cliente · 2026-10-05

Implementadas en la interfaz; recepción con proveedores y dispositivos reales
todavía pendiente. El backend N7 ya dispone de cola, VAPID y transporte cifrado.
Este incremento conecta la interfaz con ese contrato, sin nueva migración ni
dependencias npm/Python.

## Recorrido

1. Acceder y verificar el correo. Pulsar **Gestionar notificaciones**.
2. Se consultan la configuración pública y la relación de la suscripción nativa
   con esta sesión. No se solicita permiso ni se realiza alta automáticamente.
3. Pulsar **Activar notificaciones** y aceptar el permiso del navegador.
   Se crea/reutiliza una suscripción con `userVisibleOnly: true` y la pública VAPID.
   Se envían únicamente endpoint, p256dh y auth_secret al backend autenticado.
4. Un aviso muestra «Chat Temporal» y texto fijo sobre actividad. No muestra
   nombres, mensajes, ciphertext, claves ni datos arbitrarios del payload.
5. Al pulsarlo se enfoca la página de chat abierta, conservando las claves y los
   mensajes en RAM. Si no existe, se abre `/` y hace falta acceder/abrir la copia.
6. **Desactivar en este navegador** revoca el registro propio y cancela la
   suscripción nativa; intenta quitar avisos visibles. El permiso concedido se
   cambia por separado en los ajustes del navegador.

Requiere un contexto seguro y soporte de Service Worker, PushManager y
Notification. La solicitud de permiso se hace al pulsar Activar, antes de esperar
a la red. Se sigue el contrato de
[PushManager.subscribe](https://developer.mozilla.org/en-US/docs/Web/API/PushManager/subscribe),
[requestPermission](https://developer.mozilla.org/en-US/docs/Web/API/Notification/requestPermission_static)
y [notificationclick](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerGlobalScope/notificationclick_event).
El localhost de pruebas permite verificar el registro; no demuestra compatibilidad
con todos los navegadores/SO ni recepción desde sus proveedores.

## Sesión y custodia

La suscripción y sus claves de transporte Web Push son gestionadas por el navegador
y permanecen tras cerrar la pestaña. Son distintas de las claves X25519 de chat.
La aplicación no guarda tokens, privadas de chat ni IDs de sesión en storage o
en el worker. El worker no intercepta peticiones, no crea caches, no renueva la
sesión ni solicita datos a la API.

Cerrar pestaña borra los secretos de la página, pero conserva avisos mientras la
sesión backend siga vigente. Cerrar sesión revoca esa sesión e intenta dar de baja
la suscripción local incluso sin haber abierto el panel; la espera local está
acotada a 5 s y se informa de un fallo. Si falla el logout remoto, no se afirma
revocación; la UI indica recuperar el acceso para revocar la sesión anterior.
Exchange/recover excluyen las sesiones anteriores de futuras entregas. Un aviso
ya enviado al proveedor puede llegar tarde: no se promete retirarlo remotamente.

Después de un nuevo acceso, una suscripción nativa existente no se vuelve a
vincular automáticamente. Se exige Activar. Si pertenece a otra cuenta, el
servidor rechaza el alta; Desactivar cancela la suscripción local sin modificar
el registro ajeno. Una clave VAPID distinta exige desactivar y crear otra.

## Respuestas perdidas

GET `/api/v1/push/config` entrega solo `{vapid_public_key}` a una sesión autenticada.
GET `/api/v1/push/subscriptions` devuelve `{items:[{id,endpoint}]}` exclusivamente de
registros activos de esa sesión. No devuelve secretos de transporte ni endpoints
de otros usuarios/sesiones. Ambas respuestas siguen las cabeceras no-store de API.

Un POST incierto conserva la suscripción nativa en RAM, sin afirmar activación.
Otro clic Activar usa el upsert existente; Desactivar puede consultar los registros
para encontrar el ID antes de DELETE, sin emitir otro POST. Si DELETE falla, se
intenta cortar igualmente la recepción nativa, se mantiene el ID para repetir la
revocación y se bloquea la activación hasta confirmarla. Un DELETE ya aplicado es
idempotente; un ID propio ya desaparecido también se trata como revocado.
Abandonar la página durante un alta nueva termina su suscripción nativa al resolver
la operación. La confirmación pendiente solo se conserva en RAM: recargar elimina
ese estado, y el próximo acceso revoca la sesión anterior.

## Worker, duplicados y comprobaciones

`/service-worker.js` tiene scope `/`, no-store y permiso CSP `worker-src 'self'`.
Solo procesa push y clics de avisos, más la instalación para actualizar su código.
El tag usa event_type/message_id válidos; payload desconocido recibe un tag fijo.
Avisos repetidos con el mismo tag reemplazan los todavía visibles sin renotify.
Después de descartar un aviso, una nueva entrega puede mostrarlo otra vez: no hay
historial durable de deduplicación ni garantía de exactamente una entrega.

Las pruebas Node cubren permisos, formato, respuestas perdidas, cambio VAPID,
sesión nueva, limpieza tardía y privacidad/URL del worker. Las pruebas Playwright
registran/activan el worker real en Chromium escritorio y viewport móvil, pero
simulan PushManager y REST: no contactan FCM/Firefox/Apple. La integración backend
usa PostgreSQL/Redis reales y un receptor HTTPS local que verifica VAPID y descifra
el payload. El smoke Caddy HTTPS incluye los nuevos assets/cabeceras en CI.

Pendiente de homologación: navegador/SO final, permisos y recepción con proveedor
real, cambios de suscripción gestionados por el navegador, sustitución de sesión,
TTL/offers, cortes y cierre de página en staging. El worker no hace alta automática
en `pushsubscriptionchange`, porque carece de credenciales: volver al panel y
activar de nuevo permite renovar la relación autenticada.
