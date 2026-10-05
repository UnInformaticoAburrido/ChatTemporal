import { ChatApi, errorMessage } from './api.js';
import { stopLocalPush } from './push.js';

const api = new ChatApi();
const screen = document.querySelector('#screen');
const notice = document.querySelector('#notice');
let user = null;
let busy = false;
let disposeKeys = null;
let disposePush = null;
let disposeBootstrap = null;

function message(text, error = false) {
  if (user && !api.signedIn) { user = null; showAccess('recover'); }
  notice.textContent = text;
  notice.className = text ? (error ? 'notice error' : 'notice success') : '';
}
function show(markup) {
  disposeBootstrap?.(); disposeBootstrap = null;
  disposeKeys?.(); disposeKeys = null;
  disposePush?.(); disposePush = null;
  screen.innerHTML = markup; // Solo plantillas constantes; datos de usuario siempre vía textContent.
  screen.querySelector('h2')?.focus();
}
async function perform(operation) {
  if (busy) return;
  busy = true;
  message('');
  screen.setAttribute('aria-busy', 'true');
  // Bloquear interacciones sin sobrescribir restricciones que el chat actualiza en segundo plano.
  screen.inert = true;
  try { await operation(); }
  catch (error) {
    if (!api.signedIn && user) { user = null; showAccess('recover'); }
    message(errorMessage(error), true);
  } finally {
    busy = false;
    screen.removeAttribute('aria-busy');
    screen.inert = false;
  }
}
function bindForm(id, action) {
  screen.querySelector(id).addEventListener('submit', event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    perform(() => action(data));
  });
}
function showAccess(mode = 'register') {
  const recovering = mode === 'recover';
  show(`
    <nav class="switch" aria-label="Tipo de acceso">
      <button type="button" id="register-tab" ${!recovering ? 'aria-current="page"' : ''}>Crear cuenta</button>
      <button type="button" id="recover-tab" ${recovering ? 'aria-current="page"' : ''}>Recuperar acceso</button>
    </nav>
    <h2 tabindex="-1">${recovering ? 'Vuelve a tu cuenta' : 'Tu primera conversación está por venir'}</h2>
    <p class="muted">${recovering ? 'Usa el correo de tu cuenta y las 24 palabras que guardaste. Al recuperar el acceso, se cerrarán tus otras sesiones.' : 'Elige cómo te conocerán y dónde recibir tu código de verificación.'}</p>
    <form id="access-form">
      ${!recovering ? '<label for="nick">Nombre de usuario</label><input id="nick" name="nick" autocomplete="username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_]{3,32}" aria-describedby="nick-help"><small id="nick-help">De 3 a 32 letras sin acentos, números o guiones bajos.</small>' : ''}
      <label for="email">Correo electrónico</label><input id="email" name="email" type="email" autocomplete="email" maxlength="254" required>
      ${recovering ? '<label for="phrase">Frase de recuperación</label><textarea id="phrase" name="phrase" rows="4" maxlength="2048" autocomplete="off" autocapitalize="none" spellcheck="false" required aria-describedby="phrase-help"></textarea><small id="phrase-help">Introduce las 24 palabras en su orden original.</small>' : ''}
      <button class="primary" type="submit">${recovering ? 'Recuperar acceso' : 'Crear mi cuenta'} <span aria-hidden="true">→</span></button>
    </form>
    <button class="secondary" type="button" id="bootstrap-access">Acceder desde la aplicación principal</button>
    <p class="footnote">La sesión se conserva mientras esta página permanezca abierta. Si la recargas o cierras, tendrás que volver a acceder.</p>
  `);
  screen.querySelector('#register-tab').onclick = () => { if (!busy) { message(''); showAccess(); } };
  screen.querySelector('#recover-tab').onclick = () => { if (!busy) { message(''); showAccess('recover'); } };
  screen.querySelector('#bootstrap-access').onclick = () => { if (!busy) { message(''); showBootstrap(); } };
  bindForm('#access-form', async (data) => {
    if (recovering) {
      const phrase = data.get('phrase').trim().normalize('NFKD').split(/\s+/).join(' ');
      if (phrase.split(' ').length !== 24) { message('Introduce las 24 palabras de recuperación.', true); return; }
      await api.recover(data.get('email').trim(), phrase);
      // Borrar el campo antes de cualquier lectura adicional de la red.
      screen.querySelector('#phrase').value = '';
      showProfileRetry();
      await loadAccount();
    } else {
      const result = await api.register(data.get('nick').trim(), data.get('email').trim());
      user = result.user;
      showPhrase(result.recovery_mnemonic);
    }
  });
}
function showBootstrap() {
  show(`<h2 tabindex="-1">Accede desde la aplicación principal</h2>
    <p class="muted">Introduce su código de acceso. Para trasladar claves, prepara primero el dispositivo anterior: este acceso cierra su sesión de chat y conserva únicamente el permiso temporal de envío de claves.</p>
    <form id="bootstrap-form"><label for="bootstrap-code">Código de acceso de la aplicación principal</label>
      <textarea id="bootstrap-code" name="bootstrap" rows="4" maxlength="8192" autocomplete="off" autocapitalize="none" spellcheck="false" required></textarea>
      <button class="primary" type="submit">Entrar con el código</button></form>
    <button class="secondary" type="button" id="back-access">Volver al acceso</button>`);
  screen.querySelector('#back-access').onclick = () => { if (!busy) showAccess('recover'); };
  bindForm('#bootstrap-form', async data => {
    const token = data.get('bootstrap').trim();
    screen.querySelector('#bootstrap-code').value = '';
    await api.exchange(token);
    showProfileRetry(); await loadAccount();
  });
}
async function showAutomaticBootstrap() {
  let stopped = false;
  show(`<h2 tabindex="-1">Abriendo el acceso de la aplicación principal</h2>
    <p class="muted">Estamos esperando su código firmado. Este acceso sustituye la sesión anterior de chat. Para trasladar claves, prepara primero el dispositivo anterior.</p>
    <button class="secondary" id="cancel-bootstrap" type="button">Cancelar acceso automático</button>`);
  const container = screen.querySelector('#cancel-bootstrap');
  // También invalida una consulta de configuración que termine después de cancelar.
  disposeBootstrap = () => { stopped = true; try { window.opener = null; } catch { /* Origen protegido. */ } };
  container.onclick = () => { if (!busy) { showBootstrap(); message('Acceso automático cancelado.'); } };
  try {
    const config = await api.bootstrapConfig();
    if (stopped || !container.isConnected || api.signedIn) return;
    if (config.protocol_version !== 1) throw new Error('unsupported');
    const { receiveBootstrap } = await import('./bootstrap.js');
    if (stopped || !container.isConnected || api.signedIn) return;
    const dispose = receiveBootstrap({ origins: config.origins,
      onError: error => { if (!stopped && container.isConnected) { showBootstrap(); message(errorMessage(error), true); } },
      onToken: async token => {
        if (stopped || !container.isConnected || busy || api.signedIn) throw new Error('cancelled');
        let accepted = false;
        await perform(async () => {
          try { await api.exchange(token); accepted = true; }
          catch (error) { showBootstrap(); throw error; }
          showProfileRetry(); await loadAccount();
        });
        if (!accepted) throw new Error('exchange-failed');
      },
    });
    disposeBootstrap = () => { stopped = true; dispose(); };
  } catch (error) {
    if (stopped || !container.isConnected) return;
    showBootstrap(); message(errorMessage(error.code ? error : { code: 'BOOTSTRAP_UNAVAILABLE' }), true);
  }
}
async function loadAccount() {
  try { user = await api.me(); showAccount(); }
  catch (error) {
    if (!api.signedIn) showAccess('recover');
    throw error;
  }
}
function showProfileRetry() {
  show(`<h2 tabindex="-1">Acceso recuperado</h2><p class="muted">Estamos consultando el estado de tu cuenta.</p>
    <button class="primary" id="load-account" type="button">Consultar mi cuenta</button>
    <p class="footnote">Si falla la conexión, puedes volver a consultar sin introducir tu frase.</p>`);
  screen.querySelector('#load-account').onclick = () => perform(loadAccount);
}
function showPhrase(phrase) {
  show(`<p class="eyebrow">PASO 2 · GUARDA TU ACCESO</p><h2 tabindex="-1">Estas palabras son solo tuyas.</h2>
    <p class="muted">Guárdalas en un lugar seguro y en este orden. Solo se muestran al crear tu cuenta. Cualquiera que las tenga junto a tu correo puede recuperar tu acceso.</p>
    <ol id="words" class="words" aria-label="Frase de recuperación"></ol>
    <form id="saved-form"><label class="check"><input type="checkbox" name="saved" required> He guardado mis 24 palabras en un lugar seguro.</label>
    <button class="primary" type="submit">Continuar a la verificación <span aria-hidden="true">→</span></button></form>`);
  const words = screen.querySelector('#words');
  phrase.split(' ').forEach(word => { const item = document.createElement('li'); item.textContent = word; words.append(item); });
  bindForm('#saved-form', async () => { showAccount(); });
}
function showAccount() {
  show(`<p class="eyebrow">TU CUENTA</p><h2 tabindex="-1">Hola, <span id="user-name"></span>.</h2>
    <p id="user-email" class="muted"></p>
    <div class="account-state ${user.email_verified ? 'verified' : ''}"><span aria-hidden="true">${user.email_verified ? '✓' : '○'}</span> ${user.email_verified ? 'Correo verificado' : 'Verifica tu correo para completar el acceso'}</div>
    ${!user.email_verified ? `<p class="muted">Hemos enviado un código a tu correo. Pégalo aquí para verificar tu cuenta.</p>
      <form id="verify-form"><label for="code">Código de verificación</label><input id="code" name="code" autocomplete="one-time-code" autocapitalize="none" spellcheck="false" maxlength="128" required>
      <button class="primary" type="submit">Verificar correo</button></form>
      <button class="secondary" id="resend" type="button">Enviar otro código</button>` :
      '<p class="muted">Tu cuenta está preparada. Configura las claves que protegerán tus conversaciones.</p><button class="primary" id="open-keys" type="button">Gestionar claves de conversación</button><section id="keys-panel" aria-label="Claves de conversación"></section><button class="secondary" id="open-push" type="button">Gestionar notificaciones</button><section id="push-panel" aria-label="Notificaciones"></section>'}
    <button class="text-button" id="logout" type="button">Cerrar sesión</button>`);
  screen.querySelector('#user-name').textContent = user.nick;
  screen.querySelector('#user-email').textContent = user.email;
  if (!user.email_verified) {
    bindForm('#verify-form', async data => {
      await api.verify(data.get('code').trim());
      user = await api.me();
      showAccount();
      message('Tu correo se ha verificado correctamente.');
    });
    screen.querySelector('#resend').onclick = () => perform(async () => {
      await api.resend(); message('Hemos enviado otro código a tu correo.');
    });
  }
  if (user.email_verified) {
    screen.querySelector('#open-push').onclick = () => perform(async () => {
      const container = screen.querySelector('#push-panel'), owner = user;
      const { mountPush } = await import('./push-ui.js');
      if (!container.isConnected || user !== owner || !api.signedIn) return;
      disposePush?.(); disposePush = null;
      const dispose = await mountPush({ api, container, run: perform, notify: message });
      if (!container.isConnected || user !== owner || !api.signedIn) dispose();
      else disposePush = dispose;
    });
    screen.querySelector('#open-keys').onclick = () => perform(async () => {
      const container = screen.querySelector('#keys-panel');
      const owner = user;
      const { mountKeys } = await import('./keys-ui.js');
      if (!container.isConnected || user !== owner || !api.signedIn) return;
      disposeKeys?.(); disposeKeys = null;
      const dispose = await mountKeys({ api, user: owner, container, run: perform, notify: message,
        finishHandoff: () => { api.clear(); user = null; showAccess('recover'); message('Transferencia cerrada en este dispositivo.'); } });
      if (!container.isConnected || user !== owner || !api.signedIn) dispose();
      else disposeKeys = dispose;
    });
  }
  screen.querySelector('#logout').onclick = () => perform(async () => {
    disposeKeys?.(); disposeKeys = null;
    disposePush?.(); disposePush = null;
    let localPushFailed = false, timer;
    const cleanup = Promise.race([stopLocalPush(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('timeout')), 5000);
    })]).catch(() => { localPushFailed = true; }).finally(() => clearTimeout(timer));
    try { await api.logout(); }
    catch { await cleanup; user = null; showAccess('recover'); message('Has salido de esta página. No hemos podido confirmar el cierre en el servidor; recuperar el acceso revocará la sesión anterior.', true); return; }
    await cleanup;
    user = null; showAccess('recover'); message(localPushFailed ? 'Sesión cerrada y entrega de avisos revocada. No se pudo confirmar la baja del navegador; comprueba sus ajustes de notificaciones.' : 'Sesión cerrada.', localPushFailed);
  });
}
// Una página recuperada desde la caché de navegación no debe restaurar secretos visibles.
window.addEventListener('pagehide', () => { disposeBootstrap?.(); disposeBootstrap = null; disposeKeys?.(); disposeKeys = null; disposePush?.(); disposePush = null; api.clear(); user = null; screen.replaceChildren(); message(''); });
window.addEventListener('pageshow', event => { if (event.persisted) showAccess('recover'); });
if (location.hash === '#main-app') {
  // Marcador público de intención, sin credenciales; no se conserva en el historial.
  history.replaceState(null, '', location.pathname + location.search);
  showAutomaticBootstrap();
} else showAccess();
