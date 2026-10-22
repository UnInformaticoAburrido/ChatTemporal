// Sin fetch handler, caches, credenciales ni claves de chat. Solo avisos genéricos.
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('push', event => {
  let payload = null;
  try { payload = event.data?.json(); } catch { /* Payload desconocido: mismo aviso genérico. */ }
  const valid = ['message.new', 'message.offer'].includes(payload?.event_type)
    && typeof payload?.message_id === 'string' && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(payload.message_id);
  // El tag reemplaza duplicados todavía visibles; no persiste historial de avisos.
  const tag = valid ? `chat-${payload.event_type}-${payload.message_id}` : 'chat-activity';
  event.waitUntil(self.registration.showNotification('Chat Temporal', {
    body: 'Hay actividad en tus conversaciones. Abre Chat Temporal para consultarla.',
    tag, renotify: false,
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of windows) {
      const url = new URL(client.url);
      // Enfocar conserva las claves/mensajes en RAM de una página abierta.
      if (url.origin === self.location.origin && ['/', '/index.html'].includes(url.pathname)) return client.focus();
    }
    return self.clients.openWindow('/'); // No usa URLs, secretos ni IDs del payload.
  })());
});
