import { LocalKey, KeyError } from './keys.js';

// Solo copias cifradas descargadas explícitamente; ninguna escritura en storage del navegador.
export async function mountKeys({ api, user, container, run, notify, finishHandoff = () => api.clear() }) {
  let active = null, candidate = null, stopped = false, disposeChat = null, disposeTransfer = null, pendingTransfer = null;
  const urls = new Set();
  const alive = () => !stopped && container.isConnected && api.signedIn;
  const check = () => { if (!alive()) throw new KeyError('KEY_LOCKED'); };
  function dispose() {
    stopped = true; disposeChat?.(); disposeTransfer?.(); active?.lock(); candidate?.lock(); active = candidate = null;
    pendingTransfer = null;
    urls.forEach(url => URL.revokeObjectURL(url)); urls.clear();
  }
  function render(markup) {
    check(); disposeTransfer?.(); disposeTransfer = null; container.innerHTML = markup;
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
        if (pendingTransfer) {
          if (pendingTransfer.publicKey !== restored.publicKey) throw new KeyError('KEY_MISMATCH');
          try { await api.transfer(pendingTransfer.id, 'DELETE'); }
          catch (error) { if (error.code !== 'KEY_TRANSFER_EXPIRED') throw error; }
          check(); pendingTransfer = null;
        }
        active?.lock(); candidate?.lock(); candidate = null; active = restored;
        render(`<h3 tabindex="-1">Clave preparada en esta sesión</h3>
          <p class="muted">La clave de tu copia coincide con la publicada. Conserva el archivo y su contraseña: volverás a necesitarlos al cerrar o recargar.</p>
          <section id="chat-panel" aria-label="Chat"></section>
          <p class="muted">Preparar un traslado borra los mensajes efímeros de esta página. La transferencia incluye las claves; puedes pedir al interlocutor una copia del historial que conserve.</p>
          <button class="secondary" id="send-device-keys" type="button">Enviar claves a otro dispositivo</button>
          <button class="secondary" id="lock-key" type="button">Bloquear clave</button>`);
        container.querySelector('#lock-key').onclick = () => run(async () => {
          disposeChat?.(); disposeChat = null; active?.lock(); active = null; await start(); notify('Clave bloqueada en esta página.');
        });
        container.querySelector('#send-device-keys').onclick = () => run(async () => {
          const { mountSender } = await import('./transfers-ui.js'); check();
          const uploader = await api.prepareUploader();
          try {
            check(); disposeChat?.(); disposeChat = null;
            render('<section id="transfer-panel" aria-label="Transferir claves"></section>');
            disposeTransfer = mountSender({ key: active, uploader, container: container.querySelector('#transfer-panel'),
              run, notify, isAlive: alive, finish: finishHandoff });
          } catch (error) { uploader.dispose(); throw error; }
        });
        notify('Copia comprobada. La clave privada permanece en esta página.');
        async function openChat() {
          check();
          const panel = container.querySelector('#chat-panel');
          try {
            const { mountChat } = await import('./chat-ui.js'); check();
            disposeChat?.(); disposeChat = null;
            const dispose = await mountChat({ api, key: active, container: panel, run, notify, isAlive: alive });
            if (!alive()) dispose(); else disposeChat = dispose;
          } catch (error) {
            if (!alive()) return;
            notify('La clave está abierta, pero no se pudo cargar el chat. Puedes volver a intentarlo.', true);
            panel.replaceChildren();
            const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'secondary';
            retry.textContent = 'Reintentar cargar el chat'; retry.onclick = () => run(openChat); panel.append(retry);
          }
        }
        await openChat();

      } catch (error) { restored.lock(); throw error; }
    });
  }
  function bindReceive() {
    if (!api.canReceiveHandoff) return;
    const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary';
    button.textContent = 'Recibir claves de otro dispositivo'; container.append(button);
    button.onclick = () => run(async () => {
      const { mountReceiver } = await import('./transfers-ui.js'); check();
      render('<section id="transfer-panel" aria-label="Recibir claves"></section>');
      disposeTransfer = mountReceiver({ api, user, container: container.querySelector('#transfer-panel'), run, notify, isAlive: alive,
        onBackup: result => {
          pendingTransfer = result;
          render(`<h3 tabindex="-1">Comprueba la copia de tus claves recibidas</h3>
            <p class="muted">Abre el archivo descargado. Al comprobarlo se borrará la transferencia cifrada del servidor.</p>${importMarkup()}`);
          bindImport(result.publicKey);
        },
        cancel: async () => { await start(); notify('Recepción cancelada.'); },
      });
    });
  }
  async function start() {
    const remote = await api.ownKey(user.id); check();
    if (remote) {
      render(`<h3 tabindex="-1">Abre tu copia de claves</h3><p class="muted">Tu cuenta ya tiene una clave publicada. Abre su copia cifrada para usarla en esta sesión. Las 24 palabras recuperan tu cuenta, pero no descifran esta copia.</p>${importMarkup()}`);
      bindImport(); bindReceive(); return;
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
    bindReceive();
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
