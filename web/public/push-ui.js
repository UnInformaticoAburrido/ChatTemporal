import { WebPush } from './push.js';
import { errorMessage } from './api.js';

export async function mountPush({ api, container, run, notify }) {
  const push = new WebPush(api);
  let stopped = false, ready = false;
  const alive = () => !stopped && container.isConnected && api.signedIn;
  container.innerHTML = `<h3 tabindex="-1">Notificaciones de este navegador</h3>
    <p class="muted">Recibirás un aviso genérico de actividad, sin nombres ni texto de mensajes. Pueden llegar con la página cerrada mientras la sesión siga vigente. Al abrir el aviso tendrás que acceder y abrir tus claves si la página se cerró.</p>
    <p id="push-state" role="status" aria-live="polite">Consultando notificaciones…</p>
    <button class="primary" id="enable-push" type="button" disabled>Activar notificaciones</button>
    <button class="secondary" id="disable-push" type="button" disabled>Desactivar en este navegador</button>
    <button class="secondary" id="retry-push" type="button" hidden>Consultar de nuevo</button>
    <p class="footnote">Desactivar elimina la suscripción de este navegador. El permiso del sistema se cambia en sus ajustes. Cerrar sesión detiene los avisos; cerrar solo la pestaña puede mantenerlos. <a href="#preguntas/notificaciones">Ayuda con las notificaciones</a></p>`;
  const state = container.querySelector('#push-state');
  const enable = container.querySelector('#enable-push'), disable = container.querySelector('#disable-push'), retry = container.querySelector('#retry-push');
  function update() {
    if (!alive()) return;
    enable.disabled = !ready || push.enabled || push.permission === 'denied' || push.removalPending;
    disable.disabled = !push.registration || !push.canDisable;
    state.textContent = push.removalPending ? 'Desactivación pendiente de confirmar. Vuelve a pulsar Desactivar.' : push.permission === 'denied' ? 'Notificaciones bloqueadas en los ajustes del navegador.' : push.enabled ? 'Notificaciones activas para esta sesión.' : push.canDisable ? 'Suscripción del navegador disponible; activa para vincularla a esta sesión.' : 'Notificaciones desactivadas.';
  }
  async function init() {
    try { await push.init(); if (!alive()) return; ready = true; retry.hidden = true; update(); }
    catch (error) { if (!alive()) return; ready = false; update(); state.textContent = 'No se pudo consultar la configuración de notificaciones.'; retry.hidden = !push.supported; throw error; }
  }
  enable.onclick = () => run(async () => {
    try { await push.enable(); if (alive()) notify('Notificaciones activadas.'); }
    finally { update(); }
  });
  disable.onclick = () => run(async () => {
    try { await push.disable(); if (alive()) notify('Notificaciones desactivadas en este navegador.'); }
    finally { update(); }
  });
  retry.onclick = () => run(init);
  try { await init(); }
  catch (error) { if (alive()) notify(errorMessage(error), true); }
  return () => { stopped = true; push.dispose(); };
}
