import { launchChat } from './launch-chat.mjs';
const chatOrigin = 'http://127.0.0.1:4173';
window.__issues = 0; window.__ready = null; window.__launch = null;
if (location.search === '?attack=1') {
  // Otra ventana del origen permitido: no es la ventana que abrió el chat.
  const ready = window.opener.__ready;
  window.opener.__launch.popup.postMessage({ type: 'chat.bootstrap.token', protocol_version: 1,
    request_id: ready.request_id, bootstrap_token: 'attacker-bootstrap' }, chatOrigin);
  document.querySelector('#result').textContent = 'Mensaje ajeno enviado';
} else {
  window.addEventListener('message', event => {
    if (event.origin === chatOrigin && event.source === window.__launch?.popup && event.data?.type === 'chat.bootstrap.ready') window.__ready = event.data;
  });
  document.querySelector('#launch').onclick = () => {
    window.__launch = launchChat({ chatOrigin, createBootstrapToken: async () => {
      window.__issues += 1;
      const response = await fetch('/bootstrap', { method: 'POST', cache: 'no-store', redirect: 'error', credentials: 'same-origin' });
      if (!response.ok) throw Error('synthetic provider failed');
      const result = await response.json(); return result.bootstrap_token;
    } });
    window.__launch.result.then(() => { document.querySelector('#result').textContent = 'Acceso confirmado'; },
      error => { document.querySelector('#result').textContent = error.code; });
  };
}
