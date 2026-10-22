import { LocalKey, KeyError } from './keys.js';
import { parseRequest, parseSecretCode, requestCode, transferId } from './transfers.js';
import { drawQR, mountScanner, scannerMarkup } from './qr.js';
import { errorMessage, ApiError } from './api.js';

export function mountSender({ key, uploader, container, run, notify, isAlive, finish }) {
  let stopped = false, sealed = null, scanner = null, timer;
  const alive = () => !stopped && isAlive() && container.isConnected;
  container.innerHTML = `<h3 tabindex="-1">Traslada tus claves al nuevo dispositivo</h3>
    <p class="muted">El chat está pausado en este dispositivo. En el nuevo, accede desde la aplicación principal a la misma cuenta y elige «Recibir claves de otro dispositivo». Después trae aquí su solicitud.</p>
    <p id="upload-expiry" class="muted"></p>
    <form id="send-keys"><label for="transfer-request">Solicitud del nuevo dispositivo</label>
      <textarea id="transfer-request" rows="3" maxlength="1024" autocapitalize="none" spellcheck="false" required></textarea>
      ${scannerMarkup('request', 'de solicitud')}
      <button class="primary" type="submit" id="upload-keys">Cifrar y enviar claves</button></form>
    <section id="secret-qr" hidden aria-label="Código privado de transferencia">
      <p id="upload-state"></p><p class="muted">Lee este QR únicamente desde tu nuevo dispositivo. Quien lo tenga podrá descifrar la copia cifrada. No lo envíes por el chat.</p>
      <canvas id="transfer-secret-qr" class="qr-code" role="img" aria-label="QR privado para descifrar las claves"></canvas>
      <label for="transfer-secret-text">Código privado para copiar manualmente</label>
      <textarea id="transfer-secret-text" rows="4" readonly autocomplete="off" spellcheck="false"></textarea></section>
    <button class="secondary" type="button" id="finish-source">Finalizar y borrar códigos de este dispositivo</button>`;
  const $ = selector => container.querySelector(selector);
  const input = $('#transfer-request'), qrCanvas = $('#transfer-secret-qr');
  $('#upload-expiry').textContent = 'Permiso de envío hasta ' + new Date(uploader.expiresAt).toLocaleString() + '. Mantén esta página abierta.';
  function dispose() {
    if (stopped) return;
    stopped = true; clearTimeout(timer); scanner?.(); scanner = null; sealed = null;
    uploader.dispose(); key.lock();
    input.value = ''; $('#transfer-secret-text').value = ''; qrCanvas.width = qrCanvas.height = 0;
  }
  scanner = mountScanner({ container, prefix: 'request', input, run, notify, isAlive: alive });
  timer = setTimeout(() => {
    if (!alive()) return;
    dispose(); $('#send-keys').hidden = true; $('#secret-qr').hidden = true;
    notify(errorMessage(new ApiError('TRANSFER_ACCESS_EXPIRED')), true);
  }, Math.max(0, uploader.expiresAt - Date.now()));
  $('#finish-source').onclick = () => run(async () => { dispose(); finish(); });
  $('#send-keys').onsubmit = e => {
    e.preventDefault(); const request = input.value;
    run(async () => {
      if (!alive()) return;
      const id = sealed?.id || parseRequest(request);
      if (!sealed) sealed = { id, ...key.sealTransfer(id) };
      scanner?.(); scanner = null; input.value = ''; input.disabled = true;
      let uploaded = false;
      try { await uploader.upload(id, sealed.encrypted_blob); uploaded = true; }
      catch (error) {
        if (!alive()) return;
        // Si se perdió la respuesta, compartir el mismo secreto permite verificar en el destino antes de repetir PUT.
        notify(errorMessage(error), true);
      }
      if (!alive()) return;
      $('#secret-qr').hidden = false;
      $('#upload-state').textContent = uploaded ? 'Copia cifrada enviada. Comprueba la recepción en el nuevo dispositivo.'
        : 'Envío sin confirmar. Comprueba en el nuevo dispositivo antes de repetirlo.';
      $('#transfer-secret-text').value = sealed.qr_payload;
      await drawQR(qrCanvas, sealed.qr_payload);
      if (!alive()) { qrCanvas.width = qrCanvas.height = 0; return; }
      $('#upload-keys').disabled = uploaded;
      $('#upload-keys').textContent = 'Reintentar el mismo envío cifrado';
    });
  };
  return dispose;
}

export function mountReceiver({ api, user, container, run, notify, isAlive, onBackup, cancel }) {
  let stopped = false, created = null, qr = null, restored = null, scanner = null, timer;
  const alive = () => !stopped && isAlive() && container.isConnected;
  const check = () => { if (!alive()) throw new KeyError('KEY_LOCKED'); };
  container.innerHTML = `<h3 tabindex="-1">Recibe claves del dispositivo anterior</h3>
    <p class="muted">El dispositivo anterior debe haber preparado el envío antes de tu acceso desde la aplicación principal. Las 24 palabras no conceden ese permiso.</p>
    <button class="primary" type="button" id="create-transfer">Crear solicitud de claves</button>
    <section id="receive-transfer" hidden><p id="transfer-expiry" class="muted"></p>
      <canvas id="transfer-request-qr" class="qr-code" role="img" aria-label="QR de solicitud para el dispositivo anterior"></canvas>
      <label for="transfer-request-text">Solicitud para copiar al dispositivo anterior</label><textarea id="transfer-request-text" rows="3" readonly></textarea>
      <form id="receive-keys"><label for="transfer-secret">Código privado del dispositivo anterior</label>
        <textarea id="transfer-secret" rows="4" maxlength="1024" autocomplete="off" autocapitalize="none" spellcheck="false" required></textarea>
        ${scannerMarkup('secret', 'privado')}
        <button class="primary" type="submit">Comprobar y recibir claves</button></form></section>
    <section id="save-received" hidden><h3>Guarda una copia en este dispositivo</h3>
      <p class="muted">Las claves coinciden con la cuenta. Crea una copia cifrada y vuelve a abrirla antes de borrar la transferencia del servidor.</p>
      <form id="save-transfer"><label for="received-password">Contraseña para la nueva copia</label>
        <input id="received-password" name="password" type="password" minlength="12" maxlength="256" autocomplete="new-password" required>
        <label for="received-confirm">Repite la contraseña de la nueva copia</label>
        <input id="received-confirm" name="confirm" type="password" minlength="12" maxlength="256" autocomplete="new-password" required>
        <button class="primary" type="submit">Descargar copia de claves recibidas</button></form></section>
    <button class="secondary" type="button" id="cancel-transfer">Cancelar recepción</button>`;
  const $ = selector => container.querySelector(selector);
  const requestCanvas = $('#transfer-request-qr');
  function dispose() {
    if (stopped) return;
    stopped = true; clearTimeout(timer); scanner?.(); scanner = null; restored?.lock(); restored = null; qr = null;
    for (const input of container.querySelectorAll('input, textarea')) input.value = '';
    requestCanvas.width = requestCanvas.height = 0;
  }
  $('#create-transfer').onclick = () => run(async () => {
    const value = await api.createTransfer(); check();
    transferId(value.transfer_id);
    if (!Number.isFinite(Date.parse(value.expires_at)) || Date.parse(value.expires_at) <= Date.now()
      || !Number.isInteger(value.max_blob_bytes) || value.max_blob_bytes <= 40
      || value.max_blob_bytes > 65536) throw new ApiError('INVALID_RESPONSE');
    created = value; $('#create-transfer').hidden = true; $('#receive-transfer').hidden = false;
    $('#transfer-expiry').textContent = 'Solicitud válida hasta ' + new Date(created.expires_at).toLocaleString() + '.';
    const text = requestCode(created.transfer_id); $('#transfer-request-text').value = text;
    await drawQR(requestCanvas, text); check();
    scanner = mountScanner({ container, prefix: 'secret', input: $('#transfer-secret'), run, notify, isAlive: alive });
    timer = setTimeout(() => {
      if (!alive()) return;
      dispose(); $('#receive-transfer').hidden = true; $('#save-received').hidden = true;
      notify(errorMessage(new ApiError('KEY_TRANSFER_EXPIRED')), true);
    }, Math.max(0, Date.parse(created.expires_at) - Date.now()));
  });
  $('#receive-keys').onsubmit = e => {
    e.preventDefault(); const text = $('#transfer-secret').value;
    run(async () => {
      check();
      if (!created) throw new ApiError('KEY_TRANSFER_NOT_FOUND');
      if (text) { parseSecretCode(text, created.transfer_id); qr = text; }
      if (!qr) throw new ApiError('TRANSFER_INVALID');
      $('#transfer-secret').value = ''; $('#transfer-secret').required = false;
      const blob = await api.transfer(created.transfer_id); check();
      const key = LocalKey.fromTransfer(created.transfer_id, blob.encrypted_blob, qr, user.id);
      try {
        const remote = await api.ownKey(user.id); check();
        if (remote?.public_key !== key.publicKey || remote.protocol_version !== 1) throw new KeyError('KEY_MISMATCH');
        restored?.lock(); restored = key; qr = null; scanner?.(); scanner = null;
        $('#receive-transfer').hidden = true; $('#save-received').hidden = false;
        requestCanvas.width = requestCanvas.height = 0; $('#transfer-request-text').value = '';
        notify('Claves recibidas y comprobadas. Guarda tu copia antes de continuar.');
      } catch (error) { key.lock(); throw error; }
    });
  };
  $('#save-transfer').onsubmit = e => {
    e.preventDefault(); const form = new FormData(e.currentTarget);
    run(async () => {
      check(); if (!restored || !created) throw new KeyError('KEY_LOCKED');
      if (form.get('password') !== form.get('confirm')) { notify('Las contraseñas no coinciden.', true); return; }
      $('#received-password').value = ''; $('#received-confirm').value = '';
      const backup = await restored.backup(form.get('password')); check();
      const url = URL.createObjectURL(new Blob([backup], { type: 'application/json' }));
      const link = document.createElement('a'); link.href = url; link.download = 'chat-claves.chatkey';
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      const result = { id: created.transfer_id, publicKey: restored.publicKey };
      dispose(); onBackup(result);
    });
  };
  $('#cancel-transfer').onclick = () => run(async () => {
    if (created && api.signedIn) {
      try { await api.transfer(created.transfer_id, 'DELETE'); }
      catch (error) { if (error.code !== 'KEY_TRANSFER_EXPIRED') throw error; }
    }
    dispose(); await cancel();
  });
  return dispose;
}
