import { LocalKey, KeyError } from './keys.js';

// Solo copias cifradas descargadas explícitamente; ninguna escritura en storage del navegador.
export async function mountKeys({ api, user, container, run, notify, finishHandoff = () => api.clear(), onChatReady = () => {}, initialConversation = () => null }) {
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
  function showPublicKey(value) {
    const area = document.createElement('section'); area.className = 'public-key';
    const label = document.createElement('label'); label.htmlFor = 'own-public-key'; label.textContent = 'Tu clave pública';
    const field = document.createElement('textarea'); field.id = 'own-public-key'; field.readOnly = true; field.rows = 2; field.value = value;
    const help = document.createElement('p'); help.className = 'muted';
    help.textContent = 'Esta cadena es la parte pública de tu identidad de cifrado. La aplicación la utiliza para que otros puedan enviarte mensajes que solo tú puedes abrir. No es una invitación y no se pega en «Nueva conversación». Para conectar con alguien, comparte uno de los códigos de Mi cuenta.';
    const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'secondary'; copy.textContent = 'Copiar mi clave pública';
    copy.onclick = () => {
      if (!alive() || !field.isConnected) return;
      const fallback = () => { if (alive() && field.isConnected) { field.focus(); field.select(); notify('Clave pública seleccionada. Cópiala con Ctrl+C o con la opción de copiar del dispositivo.'); } };
      if (!navigator.clipboard?.writeText) { fallback(); return; }
      navigator.clipboard.writeText(value).then(() => { if (alive() && field.isConnected) notify('Clave pública copiada.'); }).catch(fallback);
    };
    area.append(label, field, copy, help);
    container.append(area);
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
      <p class="field-help">Selecciona el archivo .chatkey que descargaste para esta cuenta. No pegues aquí un código de invitación ni tus 24 palabras.</p>
      <label for="key-password">Contraseña de la copia</label>
      <input id="key-password" name="password" type="password" minlength="12" maxlength="256" autocomplete="off" required>
      <p class="field-help">Es la contraseña que elegiste al crear el archivo. Se usa en tu dispositivo para abrir la copia y no se envía al servidor.</p>
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
          <p class="muted">Tu copia pertenece a esta cuenta y ya está abierta: puedes leer y escribir mensajes. Cambiar de apartado no la bloquea. Si recargas o cierras la página, tendrás que seleccionar de nuevo el archivo .chatkey e introducir su contraseña.</p>
          <section id="chat-panel" aria-label="Chat"></section>
          <p class="muted">«Enviar claves a otro dispositivo» sirve para preparar el acceso a tus mensajes desde otro equipo. Este recorrido retira el chat actual y sus mensajes efímeros de la página; la transferencia incluye las claves, no el historial de conversaciones. Si necesitas mensajes antiguos, puedes pedir al interlocutor una copia del historial que todavía conserve.</p>
          <button class="secondary" id="send-device-keys" type="button">Enviar claves a otro dispositivo</button>
          <button class="secondary" id="lock-key" type="button">Bloquear clave</button>`);
        showPublicKey(restored.publicKey);
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
            const dispose = await mountChat({ api, key: active, container: panel, run, notify, isAlive: alive, initialConversation });
            if (!alive()) dispose(); else { disposeChat = dispose; onChatReady(); }
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
      render(`<h3 tabindex="-1">Abre tu copia de claves</h3>
        <p class="muted">Ya has entrado en tu cuenta, pero todavía falta desbloquear los mensajes. Tu navegador necesita la clave privada que guardaste en un archivo .chatkey. Ese archivo está protegido con la contraseña que elegiste al crearlo; las 24 palabras recuperan la cuenta, pero no abren esta copia.</p>
        <ol class="key-instructions"><li>Busca el archivo <strong>chat-claves.chatkey</strong> que descargaste. Puede estar en Descargas o en la carpeta donde lo guardaste.</li><li>Selecciónalo abajo e introduce la contraseña que elegiste para esa copia.</li><li>Pulsa «Abrir copia y comprobar clave». Si pertenece a esta cuenta y la contraseña es correcta, se abrirán tus conversaciones.</li></ol>
        <a class="help-link" href="#preguntas/claves">No sé qué archivo necesito o cómo abrirlo</a>${importMarkup()}`);
      showPublicKey(remote.public_key);
      bindImport(); bindReceive(); return;
    }
    render(`<h3 tabindex="-1">Prepara tus claves de conversación</h3>
      <p class="muted">Este es el primer paso para poder leer y enviar mensajes cifrados. La aplicación creará una clave privada en este dispositivo y la guardará en un archivo protegido con la contraseña que elijas. La parte pública se compartirá con la aplicación para que otras personas puedan escribirte.</p>
      <ol class="key-instructions"><li>Elige una contraseña de al menos 12 caracteres, distinta de tus 24 palabras de recuperación.</li><li>Pulsa «Crear y descargar copia cifrada» y guarda el archivo .chatkey en un lugar que puedas encontrar después.</li><li>En el siguiente paso, vuelve a abrir ese archivo con la misma contraseña. Así comprobamos que la copia que conservarás funciona antes de activar las claves.</li></ol>
      <a class="help-link" href="#preguntas/primera-vez">Ver los pasos completos para empezar a conversar</a>
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
          <p class="muted">La descarga ya está preparada. Ahora selecciona el archivo .chatkey que acabas de guardar y escribe la misma contraseña que elegiste en el paso anterior. Esto comprueba que podrás volver a abrirlo en futuras sesiones. Solo después se publicará tu clave pública y se activará el chat. El archivo y su contraseña no se envían al servidor.</p><a class="help-link" href="#preguntas/claves">Cómo seleccionar y abrir el archivo de claves</a>${importMarkup()}`);
        showPublicKey(publicKey);
        bindImport(publicKey);
      } catch (error) { current.lock(); if (candidate === current) candidate = null; throw error; }
    });
  }
  try { await start(); return dispose; }
  catch (error) { dispose(); throw error; }
}
