import { LocalKey, KeyError } from './keys.js';

// Solo copias cifradas descargadas explícitamente; ninguna escritura en storage del navegador.
export async function mountKeys({ api, user, container, run, notify }) {
  let active = null, candidate = null, stopped = false, disposeChat = null;
  const urls = new Set();
  const alive = () => !stopped && container.isConnected && api.signedIn;
  const check = () => { if (!alive()) throw new KeyError('KEY_LOCKED'); };
  function dispose() {
    stopped = true; disposeChat?.(); active?.lock(); candidate?.lock(); active = candidate = null;
    urls.forEach(url => URL.revokeObjectURL(url)); urls.clear();
  }
  function render(markup) {
    check(); container.innerHTML = markup;
    container.querySelector('h3')?.focus();
  }
  function form(id, action) {
    container.querySelector(id).onsubmit = event => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      run(async () => { check(); await action(data); });
    };
  }
  function importMarkup() {
    return `<form id="restore-key"><label for="key-file">Copia cifrada de claves</label>
      <input id="key-file" name="file" type="file" accept=".chatkey,application/json" required>
      <label for="key-password">Contraseña de la copia</label>
      <input id="key-password" name="password" type="password" minlength="12" maxlength="256" autocomplete="off" required>
      <button class="primary" type="submit">Abrir copia y comprobar clave</button></form>`;
  }
  function bindImport(expected = null) {
    form('#restore-key', async data => {
      const file = data.get('file');
      container.querySelector('#key-password').value = '';
      if (!file || file.size > 4096 || file.size === 0) throw new KeyError('KEY_FILE_INVALID');
      const restored = await LocalKey.restore(await file.text(), data.get('password'), user.id);
      try {
        check();
        if (expected && restored.publicKey !== expected) throw new KeyError('KEY_MISMATCH');
        let remote = await api.ownKey(user.id); check();
        if (!remote) {
          // El usuario ha abierto su copia: puede publicar únicamente si aún no existe clave.
          try { await api.createKey(restored.publicKey); }
          catch (error) { if (error.code !== 'KEY_ALREADY_EXISTS') throw error; }
          remote = await api.ownKey(user.id); check();
        }
        if (remote?.public_key !== restored.publicKey || remote.protocol_version !== 1) throw new KeyError('KEY_MISMATCH');
        active?.lock(); candidate?.lock(); candidate = null; active = restored;
        render(`<h3 tabindex="-1">Clave preparada en esta sesión</h3>
          <p class="muted">La clave de tu copia coincide con la publicada. Conserva el archivo y su contraseña: volverás a necesitarlos al cerrar o recargar.</p>
          <section id="chat-panel" aria-label="Chat"></section>
          <button class="secondary" id="lock-key" type="button">Bloquear clave</button>`);
        container.querySelector('#lock-key').onclick = () => run(async () => {
          disposeChat?.(); disposeChat = null; active?.lock(); active = null; await start(); notify('Clave bloqueada en esta página.');
        });
        notify('Copia comprobada. La clave privada permanece en esta página.');
        try {
        const { mountChat } = await import('./chat-ui.js'); check();
        disposeChat?.();
        disposeChat = await mountChat({ api, key: active, container: container.querySelector('#chat-panel'), run, notify });
        if (!alive()) { disposeChat(); disposeChat = null; }
        } catch (error) { notify('La clave está abierta, pero no se pudo cargar el chat. Bloquéala y vuelve a abrirla para reintentar.', true); }

      } catch (error) { restored.lock(); throw error; }
    });
  }
  async function start() {
    const remote = await api.ownKey(user.id); check();
    if (remote) {
      render(`<h3 tabindex="-1">Abre tu copia de claves</h3><p class="muted">Tu cuenta ya tiene una clave publicada. Abre su copia cifrada para usarla en esta sesión. Las 24 palabras recuperan tu cuenta, pero no descifran esta copia.</p>${importMarkup()}`);
      bindImport(); return;
    }
    render(`<h3 tabindex="-1">Prepara tus claves de conversación</h3>
      <p class="muted">Protege tu clave con una contraseña distinta de las 24 palabras. Descarga la copia y vuelve a abrirla antes de publicar tu clave pública.</p>
      <form id="create-key"><label for="new-key-password">Contraseña para la copia</label>
      <input id="new-key-password" name="password" type="password" minlength="12" maxlength="256" autocomplete="new-password" required aria-describedby="key-help">
      <small id="key-help">Al menos 12 caracteres. Guarda el archivo y la contraseña por separado; no podemos recuperar esta contraseña.</small>
      <label for="confirm-key-password">Repite la contraseña</label><input id="confirm-key-password" name="confirm" type="password" minlength="12" maxlength="256" autocomplete="new-password" required>
      <button class="primary" type="submit">Crear y descargar copia cifrada</button></form>
      <details><summary>Ya tengo una copia de esta cuenta</summary>${importMarkup()}</details>`);
    bindImport();
    form('#create-key', async data => {
      if (data.get('password') !== data.get('confirm')) { notify('Las contraseñas no coinciden.', true); return; }
      candidate?.lock(); candidate = LocalKey.generate(user.id);
      const current = candidate;
      try {
        const sealed = await current.backup(data.get('password')); check();
        const publicKey = current.publicKey;
        const url = URL.createObjectURL(new Blob([sealed], { type: 'application/json' })); urls.add(url);
        const link = document.createElement('a'); link.href = url; link.download = 'chat-claves.chatkey';
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => { URL.revokeObjectURL(url); urls.delete(url); }, 1000);
        render(`<h3 tabindex="-1">Comprueba la copia que has guardado</h3>
          <p class="muted">Selecciona el archivo descargado e introduce su contraseña. Solo después se publicará la clave pública. La copia y la contraseña se procesan aquí y no se envían al servidor.</p>${importMarkup()}`);
        bindImport(publicKey);
      } catch (error) { current.lock(); if (candidate === current) candidate = null; throw error; }
    });
  }
  try { await start(); return dispose; }
  catch (error) { dispose(); throw error; }
}
