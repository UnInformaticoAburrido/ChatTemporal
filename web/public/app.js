import { ChatApi, errorMessage } from './api.js';

const api = new ChatApi();
const screen = document.querySelector('#screen');
const notice = document.querySelector('#notice');
let user = null;
let busy = false;
let disposeKeys = null;

function message(text, error = false) {
  notice.textContent = text;
  notice.className = text ? (error ? 'notice error' : 'notice success') : '';
}
function show(markup) {
  disposeKeys?.(); disposeKeys = null;
  screen.innerHTML = markup; // Solo plantillas constantes; datos de usuario siempre vía textContent.
  screen.querySelector('h2')?.focus();
}
async function perform(operation) {
  if (busy) return;
  busy = true;
  message('');
  screen.setAttribute('aria-busy', 'true');
  const controls = [...screen.querySelectorAll('button, input, textarea')];
  controls.forEach(control => { control.disabled = true; });
  try { await operation(); }
  catch (error) {
    if (!api.signedIn && user) { user = null; showAccess('recover'); }
    message(errorMessage(error), true);
  } finally {
    busy = false;
    screen.removeAttribute('aria-busy');
    controls.filter(control => control.isConnected).forEach(control => { control.disabled = false; });
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
    <p class="footnote">La sesión se conserva mientras esta página permanezca abierta. Si la recargas o cierras, tendrás que volver a acceder.</p>
  `);
  screen.querySelector('#register-tab').onclick = () => { if (!busy) { message(''); showAccess(); } };
  screen.querySelector('#recover-tab').onclick = () => { if (!busy) { message(''); showAccess('recover'); } };
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
      '<p class="muted">Tu cuenta está preparada. Configura las claves que protegerán tus conversaciones.</p><button class="primary" id="open-keys" type="button">Gestionar claves de conversación</button><section id="keys-panel" aria-label="Claves de conversación"></section>'}
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
    screen.querySelector('#open-keys').onclick = () => perform(async () => {
      const container = screen.querySelector('#keys-panel');
      const owner = user;
      const { mountKeys } = await import('./keys-ui.js');
      if (!container.isConnected || user !== owner || !api.signedIn) return;
      disposeKeys?.(); disposeKeys = null;
      const dispose = await mountKeys({ api, user: owner, container, run: perform, notify: message });
      if (!container.isConnected || user !== owner || !api.signedIn) dispose();
      else disposeKeys = dispose;
    });
  }
  screen.querySelector('#logout').onclick = () => perform(async () => {
    disposeKeys?.(); disposeKeys = null;
    try { await api.logout(); }
    catch { user = null; showAccess('recover'); message('Has salido de esta página. No hemos podido confirmar el cierre en el servidor; recuperar el acceso revocará la sesión anterior.', true); return; }
    user = null; showAccess('recover'); message('Sesión cerrada.');
  });
}
// Una página recuperada desde la caché de navegación no debe restaurar secretos visibles.
window.addEventListener('pagehide', () => { disposeKeys?.(); disposeKeys = null; api.clear(); user = null; screen.replaceChildren(); message(''); });
window.addEventListener('pageshow', event => { if (event.persisted) showAccess('recover'); });
showAccess();
