import { ChatApi, errorMessage } from './api.js';
import { stopLocalPush } from './push.js';

const api = new ChatApi();
const screen = document.querySelector('#screen');
const notice = document.querySelector('#notice');
const card = document.querySelector('#account-card');
const workspace = document.querySelector('#workspace');
const accessDialog = document.querySelector('#access-dialog');
let user = null;
let busy = false;
let disposeKeys = null;
let disposePush = null;
let disposeBootstrap = null;
let disposeLobby = null;
let openConversations = null;

// El historial solo contiene nombres de pantallas, nunca frases ni credenciales.
function routeTo(route, push = false) {
  const hash = `#${route}`;
  if (location.hash !== hash) history[push ? 'pushState' : 'replaceState'](null, '', hash);
  for (const [id, target] of [['nav-account', 'cuenta'], ['nav-chat', 'chat']]) {
    const link = document.getElementById(id);
    if (route === target) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
}
function navigate(route) {
  if (busy) return;
  routeTo(route, true);
  renderRoute();
}
function renderRoute() {
  if (busy) { routeTo(api.signedIn ? 'cuenta' : 'inicio'); return; }
  const route = location.hash.slice(1);
  if (!api.signedIn) {
    if (route === 'registro') showAccess('register');
    else if (['acceso', 'cuenta', 'chat'].includes(route)) showAccess('recover');
    else showWelcome();
    return;
  }
  if (route === 'chat') {
    document.querySelector('#nav-chat').click();
  } else {
    document.body.dataset.view = 'account'; routeTo('cuenta');
  }
}
window.addEventListener('hashchange', () => {
  // Los enlaces externos con fragmentos también pasan por las mismas comprobaciones.
  if (location.hash !== '#main') renderRoute();
});

function syncSession() {
  document.body.dataset.session = api.signedIn ? 'active' : 'inactive';
  document.querySelector('.guest-nav').hidden = api.signedIn;
  document.querySelector('.member-nav').hidden = !api.signedIn;
  document.querySelector('#landing').hidden = api.signedIn;
  document.querySelector('#session-status').textContent = api.signedIn
    ? (user ? `Sesión iniciada · ${user.nick}` : 'Sesión iniciada') : 'Sin sesión';
}
function message(text, error = false) {
  if (user && !api.signedIn) { user = null; showAccess('recover'); }
  notice.textContent = text;
  notice.className = text ? (error ? 'notice error' : 'notice success') : '';
  syncSession();
}
function show(markup) {
  disposeLobby?.(); disposeLobby = null; openConversations = null;
  disposeBootstrap?.(); disposeBootstrap = null;
  disposeKeys?.(); disposeKeys = null;
  disposePush?.(); disposePush = null;
  screen.innerHTML = markup; // Solo plantillas constantes; datos de usuario siempre vía textContent.
  if (api.signedIn) {
    accessDialog.close(); workspace.append(card); workspace.hidden = false;
    document.body.dataset.view = 'account';
  } else {
    workspace.hidden = true;
    document.querySelector('#access-content').append(card);
    if (!accessDialog.open) accessDialog.showModal();
    document.body.dataset.view = 'access';
  }
  syncSession();
  const heading = screen.querySelector('h2');
  accessDialog.classList.toggle('recovery-dialog', Boolean(screen.querySelector('.recovery-words')));
  if (heading) { heading.id = 'access-title'; heading.focus(); }
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
  routeTo(recovering ? 'acceso' : 'registro');
  show(`
    <h2 tabindex="-1">${recovering ? 'Bienvenido de nuevo' : 'Crear tu cuenta'}</h2>
    <p class="muted">${recovering ? 'Introduce el correo y tus 24 palabras de recuperación en el orden correcto.' : 'Solo necesitamos dos datos para empezar.'}</p>
    <form id="access-form">
      ${!recovering ? '<label for="nick">NickName</label><input id="nick" name="nick" aria-label="Nombre de usuario" placeholder="Tu apodo" autocomplete="username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_]{3,32}" aria-describedby="nick-help"><small id="nick-help">De 3 a 32 letras, números o guiones bajos.</small>' : ''}
      <label for="email">Email</label><input id="email" name="email" aria-label="Correo electrónico" type="email" placeholder="nombre@correo.com" autocomplete="email" maxlength="254" required>
      ${recovering ? `<fieldset class="recovery-words"><legend class="sr-only">Frase de recuperación</legend>${Array.from({ length: 24 }, (_, i) => `<label class="word-input"><span>${String(i + 1).padStart(2, '0')}</span><input type="password" name="word-${i}" aria-label="Palabra ${i + 1}" placeholder="Palabra ${i + 1}" maxlength="2048" autocomplete="off" autocapitalize="none" spellcheck="false"></label>`).join('')}</fieldset><p class="field-help">Puedes pegar las 24 palabras en el primer campo. Recuperar el acceso cerrará tus otras sesiones.</p>` : ''}
      <button class="primary" type="submit">${recovering ? 'Acceder a mi cuenta' : 'Crear mi cuenta'} <img class="icon" src="/assets/icons/arrow-right.svg" alt=""></button>
    </form>
    <p class="access-switch">${recovering ? '¿Nuevo en Nexo?' : '¿Ya tienes cuenta?'} <button class="nav-link" type="button" id="${recovering ? 'register-tab' : 'recover-tab'}">${recovering ? 'Crear una cuenta' : 'Iniciar sesión'}</button></p>
    <details class="alternative-access"><summary>Otras formas de acceso</summary><button class="secondary" type="button" id="bootstrap-access">Acceder desde la aplicación principal</button></details>
    <p class="footnote">La sesión se conserva mientras esta página permanezca abierta. Si la recargas o cierras, tendrás que volver a acceder.</p>
  `);
  accessDialog.classList.toggle('recovery-dialog', recovering);
  const switchButton = screen.querySelector(recovering ? '#register-tab' : '#recover-tab');
  switchButton.onclick = () => { if (!busy) { message(''); navigate(recovering ? 'registro' : 'acceso'); } };
  screen.querySelector('#bootstrap-access').onclick = () => { if (!busy) { message(''); showBootstrap(); } };
  const wordInputs = [...screen.querySelectorAll('.word-input input')];
  function distribute(text, index) {
    const words = text.trim().normalize('NFKD').split(/\s+/);
    if (words.length < 2) return false;
    if (words.length + index > wordInputs.length) { message('La frase debe contener 24 palabras.', true); return true; }
    words.forEach((word, offset) => { wordInputs[index + offset].value = word; });
    wordInputs[Math.min(index + words.length, wordInputs.length - 1)].focus(); return true;
  }
  wordInputs.forEach((input, index) => {
    input.onpaste = event => { if (distribute(event.clipboardData.getData('text'), index)) event.preventDefault(); };
    input.oninput = () => { if (/\s/.test(input.value)) distribute(input.value, index); };
  });
  bindForm('#access-form', async (data) => {
    if (recovering) {
      const words = wordInputs.map(input => input.value.trim().normalize('NFKD'));
      if (words.some(word => !word || /\s/.test(word))) { message('Introduce las 24 palabras de recuperación.', true); return; }
      const phrase = words.join(' ');
      await api.recover(data.get('email').trim(), phrase);
      // Borrar el campo antes de cualquier lectura adicional de la red.
      wordInputs.forEach(input => { input.value = ''; });
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
  screen.querySelector('#back-access').onclick = () => { if (!busy) navigate('acceso'); };
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
  routeTo('cuenta');
  show(`<section class="profile-heading"><h2 tabindex="-1">Ajustes de usuario</h2><p class="muted">Tu identidad y tus conexiones, bajo tu control.</p></section>
    <section class="profile-card"><span class="avatar profile-avatar" id="user-avatar" aria-hidden="true"></span>
      <div class="profile-identity"><h3 id="user-name"></h3><p id="user-email" class="muted"></p>
      <p class="account-state ${user.email_verified ? 'verified' : ''}">${user.email_verified ? 'Correo verificado' : 'Verifica tu correo para completar el acceso'}</p></div>
      <button id="edit-profile" class="secondary" type="button">Editar perfil</button>
    </section>
    <section id="profile-editor" class="settings-section" hidden><h3>Edita tu identidad</h3>
      <form id="profile-form"><label for="profile-nick">Nombre de usuario</label><input id="profile-nick" name="nick" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_]{3,32}" autocomplete="username">
      <label for="profile-email">Correo electrónico</label><input id="profile-email" name="email" type="email" required autocomplete="email">
      <p class="muted">Si cambias el correo, tendrás que verificar la nueva dirección.</p><button class="primary" type="submit">Guardar cambios</button></form></section>
    ${!user.email_verified ? `<p class="muted">Hemos enviado un código a tu correo. Pégalo aquí para verificar tu cuenta.</p>
      <form id="verify-form"><label for="code">Código de verificación</label><input id="code" name="code" autocomplete="one-time-code" autocapitalize="none" spellcheck="false" maxlength="128" required>
      <button class="primary" type="submit">Verificar correo</button></form>
      <button class="secondary" id="resend" type="button">Enviar otro código</button>` :
      `<section id="invitations-panel" aria-label="Invitar a conversar">
        <div class="section-heading"><div><h3>Códigos de invitación</h3><p class="muted">Comparte uno de estos códigos con alguien para conectar.</p></div>
        <button class="secondary" id="invitation-codes" type="button">Obtener código para invitar</button></div>
        <div id="codes"></div></section>
        <section class="settings-section keys-intro"><h3>Claves de conversación</h3><p class="muted">Crea tus claves o abre su copia para escribir y leer mensajes.</p><button class="secondary" id="open-keys" type="button">Gestionar claves de conversación</button></section><section id="conversation-home" aria-label="Tus conversaciones" hidden></section><section id="keys-panel" aria-label="Claves de conversación"></section><section class="settings-section"><button class="secondary" id="open-push" type="button">Gestionar notificaciones</button><section id="push-panel" aria-label="Notificaciones"></section></section>`}
    <button class="text-button" id="logout" type="button">Cerrar sesión</button>`);
  screen.querySelector('#user-name').textContent = user.nick;
  screen.querySelector('#user-email').textContent = user.email;
  screen.querySelector('#user-avatar').textContent = user.nick.slice(0, 1).toUpperCase();
  screen.querySelector('#profile-nick').value = user.nick;
  screen.querySelector('#profile-email').value = user.email;
  screen.querySelector('#edit-profile').onclick = () => {
    const editor = screen.querySelector('#profile-editor'); editor.hidden = !editor.hidden;
    if (!editor.hidden) screen.querySelector('#profile-nick').focus();
  };
  bindForm('#profile-form', async data => {
    user = await api.updateProfile(data.get('nick').trim(), data.get('email').trim());
    showAccount(); message('Los cambios de tu perfil se han guardado.');
  });
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
    let pendingConversation = null;
    const owner = user, invitationArea = screen.querySelector('#codes');
    const alive = () => owner === user && api.signedIn && invitationArea.isConnected;
    async function showInvitations(regenerate = false) {
      const remote = await api.ownKey(owner.id);
      if (!alive()) return;
      if (!remote) {
        await openKeys();
        message('Primero crea y comprueba tu copia de claves. Después pulsa «Obtener código para invitar».', true);
        return;
      }
      const codes = await (regenerate ? api.regenerate() : api.invitations());
      if (!alive()) return;
      invitationArea.replaceChildren();
      for (const [index, [label, value]] of [['Con historial cifrado', codes.stored_code], ['Efímera (requiere conexión simultánea)', codes.ephemeral_code]].entries()) {
        const tile = document.createElement('section'); tile.className = 'invitation-card';
        const eyebrow = document.createElement('p'); eyebrow.className = 'eyebrow'; eyebrow.textContent = `INVITACIÓN ${index + 1}`;
        const field = document.createElement('textarea'); field.readOnly = true; field.value = value;
        field.rows = 2; field.setAttribute('aria-label', label); field.spellcheck = false;
        const title = document.createElement('p'); title.textContent = label; title.className = 'muted';
        const actions = document.createElement('div'); actions.className = 'invitation-actions';
        const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'primary';
        copy.textContent = 'Copiar'; copy.setAttribute('aria-label', index === 0 ? 'Copiar invitación con historial' : 'Copiar invitación efímera');
        copy.onclick = () => {
          // Mantener el gesto del usuario para el permiso de clipboard.
          if (!alive() || !field.isConnected) return;
          const fallback = () => { if (alive() && field.isConnected) { field.focus(); field.select(); message('Código seleccionado. Cópialo con Ctrl+C o con la opción de copiar del dispositivo.'); } };
          if (!navigator.clipboard?.writeText) { fallback(); return; }
          navigator.clipboard.writeText(value).then(() => { if (alive() && field.isConnected) message('Código de invitación copiado. Ya puedes compartirlo.'); }).catch(fallback);
        };
        const renew = document.createElement('button'); renew.type = 'button'; renew.className = 'secondary';
        const renewIcon = document.createElement('img'); renewIcon.className = 'icon'; renewIcon.src = '/assets/icons/refresh-cw.svg'; renewIcon.alt = '';
        renew.append(renewIcon, document.createTextNode(' Regenerar')); renew.setAttribute('aria-label', index === 0 ? 'Regenerar invitación con historial' : 'Regenerar invitación efímera');
        renew.onclick = () => perform(async () => {
          if (alive() && confirm('Se renovarán ambas invitaciones. Los dos códigos anteriores dejarán de funcionar. ¿Continuar?')) await showInvitations(true);
        });
        actions.append(copy, renew); tile.append(eyebrow, title, field, actions); invitationArea.append(tile);
      }
    }
    screen.querySelector('#invitation-codes').onclick = () => perform(() => showInvitations());
    screen.querySelector('#open-push').onclick = () => perform(async () => {
      const container = screen.querySelector('#push-panel'), owner = user;
      const { mountPush } = await import('./push-ui.js');
      if (!container.isConnected || user !== owner || !api.signedIn) return;
      disposePush?.(); disposePush = null;
      const dispose = await mountPush({ api, container, run: perform, notify: message });
      if (!container.isConnected || user !== owner || !api.signedIn) dispose();
      else disposePush = dispose;
    });
    async function openKeys() {
      const container = screen.querySelector('#keys-panel');
      const owner = user;
      const { mountKeys } = await import('./keys-ui.js');
      if (!container.isConnected || user !== owner || !api.signedIn) return;
      disposeKeys?.(); disposeKeys = null;
      const dispose = await mountKeys({ api, user: owner, container, run: perform, notify: message,
        initialConversation: () => pendingConversation,
        onChatReady: () => { if (api.signedIn && user === owner) { disposeLobby?.(); disposeLobby = null; document.body.dataset.view = 'chat'; routeTo('chat'); } },
        finishHandoff: () => { api.clear(); user = null; showAccess('recover'); message('Transferencia cerrada en este dispositivo.'); } });
      if (!container.isConnected || user !== owner || !api.signedIn) dispose();
      else disposeKeys = dispose;
    }
    screen.querySelector('#open-keys').onclick = () => perform(openKeys);
    openConversations = async () => {
      document.body.dataset.view = 'conversations'; routeTo('chat');
      const home = screen.querySelector('#conversation-home'); home.hidden = false;
      if (!disposeLobby) {
        const { mountConversationLobby } = await import('./chat-ui.js');
        if (!alive()) return;
        const dispose = await mountConversationLobby({ api, container: home, run: perform, notify: message,
          isAlive: alive, prepare: async id => {
            pendingConversation = id;
            if (!screen.querySelector('#keys-panel').children.length) await openKeys();
            const panel = screen.querySelector('#keys-panel'); panel.scrollIntoView({ block: 'start', behavior: 'smooth' });
            panel.querySelector('h3')?.focus();
          } });
        if (!alive()) dispose(); else disposeLobby = dispose;
      }
      home.querySelector('h2')?.focus();
    };
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
function showWelcome() {
  if (busy || api.signedIn) return;
  routeTo('inicio');
  accessDialog.close(); screen.replaceChildren(); notice.textContent = ''; notice.className = '';
  workspace.append(card); workspace.hidden = true; document.body.dataset.view = 'welcome'; syncSession();
}
document.querySelectorAll('.guest-nav [data-route], .welcome-copy [data-route]').forEach(link => {
  link.addEventListener('click', event => {
    event.preventDefault();
    navigate(link.dataset.route);
  });
});
document.querySelector('#close-access').onclick = () => navigate('inicio');
accessDialog.addEventListener('cancel', event => { event.preventDefault(); navigate('inicio'); });
document.querySelector('.brand').onclick = event => {
  event.preventDefault(); if (busy) return;
  navigate(api.signedIn ? 'cuenta' : 'inicio');
};
document.querySelector('#nav-account').onclick = event => { event.preventDefault(); if (!busy && user) { routeTo('cuenta', true); document.body.dataset.view = 'account'; } };
document.querySelector('#nav-chat').onclick = event => {
  event.preventDefault();
  if (busy || !user) return;
  if (!user.email_verified) { routeTo('cuenta'); message('Verifica tu correo antes de abrir tus conversaciones.', true); return; }
  if (screen.querySelector('#chat-panel')?.children.length) { routeTo('chat', true); document.body.dataset.view = 'chat'; }
  else perform(openConversations);
};
function showInfo(title, paragraphs) {
  const dialog = document.querySelector('#info-dialog'); document.querySelector('#info-title').textContent = title;
  const content = document.querySelector('#info-copy'); content.replaceChildren();
  for (const text of paragraphs) { const p = document.createElement('p'); p.textContent = text; content.append(p); }
  dialog.showModal(); document.querySelector('#info-title').focus();
}
document.querySelector('#privacy-link').onclick = () => showInfo('Tu privacidad, bajo tu control', [
  'Los mensajes se cifran y descifran en tu dispositivo. La clave privada de conversación permanece en esta página o en la copia cifrada que decides guardar.',
  'Tus palabras de recuperación permiten volver a tu cuenta. No se conservan en el navegador. Compártelas únicamente contigo: para conectar con alguien, utiliza los códigos de invitación.',
  'Puedes elegir conversaciones efímeras o con historial cifrado. Las notificaciones, si las activas, nunca incluyen el contenido del mensaje.',
]);
document.querySelector('#how-link').onclick = () => showInfo('Cómo funciona Nexo', [
  '01 · Crea tu identidad. Elige un apodo, verifica tu correo y guarda tus 24 palabras de recuperación.',
  '02 · Prepara tus claves y comparte una invitación. Guarda la copia cifrada para poder abrir tus conversaciones en otra sesión.',
  '03 · Empieza a hablar. Usa una invitación, acepta una conversación y elige qué mensajes conservar.',
]);
document.querySelector('#close-info').onclick = () => document.querySelector('#info-dialog').close();
document.querySelector('#mobile-privacy').onclick = document.querySelector('#privacy-link').onclick;
document.querySelector('#mobile-how').onclick = document.querySelector('#how-link').onclick;
document.querySelector('#info-dialog').addEventListener('close', () => {
  const menu = document.querySelector('#guest-more');
  if (menu.open) { menu.open = false; menu.querySelector('summary').focus(); }
});
// Una página recuperada desde la caché de navegación no debe restaurar secretos visibles.
window.addEventListener('pagehide', () => { disposeLobby?.(); disposeLobby = null; disposeBootstrap?.(); disposeBootstrap = null; disposeKeys?.(); disposeKeys = null; disposePush?.(); disposePush = null; api.clear(); user = null; screen.replaceChildren(); message(''); });
window.addEventListener('pageshow', event => { if (event.persisted) showWelcome(); });
if (location.hash === '#main-app') {
  // Marcador público de intención, sin credenciales; no se conserva en el historial.
  history.replaceState(null, '', location.pathname + location.search);
  showAutomaticBootstrap();
} else renderRoute();
