import { Chat } from './chat.js';
import { errorMessage } from './api.js';

function invitationDialog(container, { api, run, notify, onRedeemed, isAlive, reconcile = async () => {} }) {
  const dialog = document.createElement('dialog');
  dialog.className = 'info-dialog invitation-dialog';
  dialog.setAttribute('aria-labelledby', 'invitation-title');
  dialog.innerHTML = `<button class="icon-button dialog-close" type="button" aria-label="Cerrar nueva conversación"><img class="icon" src="/assets/icons/x.svg" alt=""></button>
    <h2 id="invitation-title">Nueva conversación</h2>
    <p class="muted">Pide a la otra persona su código de invitación efímera, disponible en Mi cuenta. Pégalo completo en el campo de abajo y pulsa «Iniciar conversación». No uses su clave pública ni sus palabras de recuperación.</p>
    <p class="muted">Se creará una conversación pendiente de que esa persona la acepte. Para intercambiar mensajes, ambos debéis abrir vuestras claves y mantener el chat conectado. En este modo no se guarda historial de mensajes en el servidor.</p>
    <form id="redeem-form"><label for="invitation">Código de invitación</label><textarea id="invitation" name="code" rows="3" maxlength="4096" required spellcheck="false" autocomplete="off" autocapitalize="none"></textarea>
    <p class="invitation-error notice error" role="alert" hidden></p><button class="primary" type="submit">Iniciar conversación</button></form>`;
  container.append(dialog);
  const field = dialog.querySelector('textarea'), error = dialog.querySelector('[role=alert]');
  let submitting = false;
  const close = () => { if (!submitting) dialog.close(); };
  dialog.querySelector('[aria-label="Cerrar nueva conversación"]').onclick = close;
  dialog.addEventListener('cancel', event => { if (submitting) event.preventDefault(); });
  dialog.addEventListener('close', () => { field.value = ''; error.hidden = true; });
  dialog.querySelector('form').onsubmit = event => {
    event.preventDefault();
    if (submitting || !field.value.trim()) return;
    const code = field.value.trim();
    run(async () => {
      submitting = true; error.hidden = true; dialog.querySelector('button[type=submit]').disabled = true;
      try {
        const room = await api.redeem(code, { ephemeralOnly: true });
        if (!isAlive()) return;
        dialog.close(); await onRedeemed(room);
      } catch (failure) {
        if (isAlive()) { error.textContent = errorMessage(failure); error.hidden = false; notify(errorMessage(failure), true); }
        // Una respuesta perdida puede haber creado el chat: consultar antes de repetir el canje.
        if (isAlive()) await reconcile().catch(() => {});
      } finally {
        submitting = false; dialog.querySelector('button[type=submit]').disabled = false;
      }
    });
  };
  return () => { error.hidden = true; dialog.showModal(); field.focus(); };
}

// La lista se puede consultar antes de abrir la clave privada de esta sesión.
export async function mountConversationLobby({ api, container, run, notify, prepare, isAlive }) {
  let disposed = false, cursor = null, loading = false;
  const rooms = new Map();
  const alive = () => !disposed && container.isConnected && isAlive();
  container.innerHTML = `<h2 tabindex="-1">Conversaciones</h2><p class="muted">Conecta con un código y habla en privado.</p>
    <div class="actions"><button class="primary" id="new-lobby-conversation" type="button">Nueva conversación</button><button class="secondary" id="prepare-chat" type="button">Abrir mis mensajes</button><button class="secondary" id="refresh-lobby" type="button">Actualizar conversaciones</button></div>
    <p class="muted">Puedes ver tus conversaciones sin abrir las claves. Para leer o escribir, selecciona una conversación o pulsa «Abrir mis mensajes»: te pediremos el archivo .chatkey y la contraseña que elegiste al crearlo. Si aún no tienes claves, ese mismo apartado te guiará para crearlas.</p>
    <p class="muted">«Nueva conversación» sirve para pegar la invitación efímera de otra persona. Para compartir tu propio código, ve a Mi cuenta. Los mensajes efímeros de esta página se pierden al cerrarla o recargarla; cambiar de apartado no los elimina.</p><a class="help-link" href="#preguntas/claves">Qué son las claves y cómo abrir mi copia</a>
    <nav id="lobby-rooms" aria-label="Conversaciones activas"></nav><p id="lobby-state" role="status"></p><button class="secondary" id="more-lobby-rooms" type="button" hidden>Más conversaciones</button>`;
  const $ = selector => container.querySelector(selector);
  function render() {
    const list = $('#lobby-rooms'); list.replaceChildren();
    for (const room of rooms.values()) {
      if (room.status === 'closed') continue;
      const item = document.createElement('button'); item.type = 'button'; item.className = 'room-item';
      const avatar = document.createElement('span'); avatar.className = 'avatar'; avatar.textContent = (room.peer?.nick || '?').slice(0, 1).toUpperCase();
      const text = document.createElement('span'); text.className = 'room-preview';
      const name = document.createElement('strong'); name.textContent = room.peer?.nick || 'Conversación anónima';
      const state = document.createElement('span'); state.textContent = room.status === 'pending' ? 'Pendiente de aceptación' : 'Activa · abre tus claves para leer';
      text.append(name, state); item.append(avatar, text); item.onclick = () => run(() => prepare(room.id)); list.append(item);
    }
    $('#lobby-state').textContent = list.children.length ? '' : 'Todavía no tienes conversaciones activas. Pulsa «Nueva conversación» para usar un código.';
    $('#more-lobby-rooms').hidden = !cursor;
  }
  async function load(more = false) {
    if (loading || !alive()) return;
    loading = true; $('#lobby-state').textContent = 'Cargando conversaciones…';
    try {
      const page = await api.conversations(more ? cursor : null);
      if (!alive()) return;
      if (!more) rooms.clear();
      page.items.forEach(room => rooms.set(room.id, room)); cursor = page.next_cursor; render();
    } catch (error) {
      if (alive()) { $('#lobby-state').textContent = 'No se pudo cargar la lista. Pulsa «Actualizar conversaciones» para reintentar.'; notify(errorMessage(error), true); }
    } finally { loading = false; }
  }
  const open = invitationDialog(container, { api, run, notify, isAlive: alive, reconcile: () => load(), onRedeemed: async room => {
    rooms.set(room.id, room); render(); await prepare(room.id);
  } });
  $('#new-lobby-conversation').onclick = open;
  $('#prepare-chat').onclick = () => run(() => prepare(null));
  $('#refresh-lobby').onclick = () => run(() => load());
  $('#more-lobby-rooms').onclick = () => run(() => load(true));
  await load();
  const timer = setInterval(() => { if (alive()) load(); }, 5000);
  return () => { disposed = true; clearInterval(timer); container.querySelector('dialog')?.close(); container.replaceChildren(); };
}

export async function mountChat({ api, key, container, run, notify, isAlive = () => true, initialConversation = () => null }) {
  let disposed = false, shown = null, sendBlocked = true;
  container.innerHTML = `<aside class="chat-sidebar"><div class="chat-sidebar-heading"><h3 tabindex="-1">Mensajes</h3></div>
    <label class="chat-search"><img class="icon" src="/assets/icons/search.svg" alt=""><input id="chat-search" type="search" aria-label="Buscar chats" placeholder="Buscar chats..." autocomplete="off"></label>
    <nav id="rooms" aria-label="Conversaciones"></nav><button id="more-rooms" class="secondary" hidden>Más conversaciones</button>
    <button class="primary new-conversation" id="new-conversation" type="button">Nueva conversación</button>
    <button id="refresh-rooms" class="secondary">Actualizar conversaciones</button><a class="help-link" href="#preguntas/entrega">Ayuda con las invitaciones y el envío</a><p id="connection" role="status"></p></aside>
    <section id="empty-conversation" class="chat-empty"><img class="empty-icon" src="/assets/icons/message-circle.svg" alt=""><h3>Tu próxima conversación empieza aquí</h3><p class="muted">Selecciona una conversación o usa una invitación para conectar.</p></section>
    <section id="conversation" aria-label="Conversación seleccionada" hidden>
      <header class="conversation-header"><button id="back-to-rooms" class="icon-button" type="button" aria-label="Volver a conversaciones"><img class="icon" src="/assets/icons/arrow-left.svg" alt=""></button>
      <span id="room-avatar" class="avatar" aria-hidden="true"></span><div class="conversation-heading"><h3 id="room-title" tabindex="-1"></h3><p id="room-state" class="muted"></p></div>
      <details class="conversation-options" id="room-options"><summary aria-label="Opciones de conversación"><img class="icon" src="/assets/icons/more-horizontal.svg" alt=""></summary><div id="room-actions" class="actions"></div></details></header>
      <section id="vote" aria-label="Votación"></section>
      <button id="older" class="secondary" hidden>Cargar mensajes anteriores</button>
      <p id="history-limit" class="muted" hidden>Esta página conserva los 500 mensajes más recientes que ha consultado.</p>
      <ol id="messages" class="messages" aria-label="Mensajes"></ol>
      <form id="send-form"><label class="sr-only" for="message-text">Mensaje</label><textarea id="message-text" rows="1" required placeholder="Escribe un mensaje..." aria-describedby="message-limit"></textarea>
        <button class="primary" id="send-message" aria-label="Enviar mensaje"><img class="icon" src="/assets/icons/send.svg" alt=""></button><small class="sr-only" id="message-limit">Hasta 256 caracteres. Enter envía; Shift+Enter añade un salto de línea.</small></form>
    </section>`;
  const $ = selector => container.querySelector(selector);
  const chat = new Chat(api, key, { changed: render, error: e => notify(errorMessage(e), true) });
  function button(parent, text, action, className = 'secondary') {
    const element = document.createElement('button'); element.type = 'button'; element.className = className; element.textContent = text;
    element.onclick = () => run(async () => { if (parent.id === 'room-actions') $('#room-options').open = false; return action(); }); parent.append(element); return element;
  }
  function render() {
    if (disposed) return;
    if (!container.isConnected || !isAlive()) { dispose(); return; }
    $('#connection').textContent = chat.ready ? 'Conectado · cifrado de extremo a extremo' : 'Reconectando… Puedes consultar el historial disponible.';
    const rooms = $('#rooms'); rooms.replaceChildren();
    const search = $('#chat-search').value.trim().toLocaleLowerCase();
    [...chat.rooms.values()].filter(room => !search || (room.peer?.nick || 'Conversación anónima').toLocaleLowerCase().includes(search)).forEach(room => {
      const label = room.peer?.nick || 'Conversación anónima';
      const latest = [...room.messages.values()].sort((a, b) => a.time.localeCompare(b.time)).at(-1);
      const b = button(rooms, '', () => { container.dataset.mobile = 'conversation'; return chat.select(room.id); }, 'room-item');
      b.setAttribute('aria-label', label + ' · ' + ({ pending: 'pendiente', active: 'activa', closed: 'cerrada' }[room.status]));
      const avatar = document.createElement('span'); avatar.className = 'avatar'; avatar.textContent = label.slice(0, 1).toUpperCase(); avatar.setAttribute('aria-hidden', 'true');
      const body = document.createElement('span'); body.className = 'room-preview';
      const name = document.createElement('strong'); name.textContent = label;
      const preview = document.createElement('span'); preview.textContent = latest?.text || ({ pending: 'Pendiente de aceptación', active: 'Empieza a conversar', closed: 'Conversación cerrada' }[room.status]);
      const time = document.createElement('time'); time.textContent = latest ? new Date(latest.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
      body.append(name, preview); b.append(avatar, body, time);
      if (chat.selected === room.id) b.setAttribute('aria-current', 'page');
    });
    if (!chat.rooms.size) rooms.textContent = 'Todavía no tienes conversaciones. Comparte una invitación o introduce la de otra persona.';
    else if (!rooms.children.length) rooms.textContent = 'No hay conversaciones que coincidan con tu búsqueda.';
    $('#more-rooms').hidden = !chat.next;
    const room = chat.rooms.get(chat.selected); $('#conversation').hidden = !room;
    $('#empty-conversation').hidden = Boolean(room);
    if (!room) { $('.chat-sidebar').append($('#connection')); return; }
    $('.conversation-heading').append($('#connection'));
    const switched = shown !== room.id;
    if (switched) { $('#message-text').value = ''; shown = room.id; $('#room-options').open = room.status === 'pending'; }
    $('#room-avatar').textContent = room.peer?.nick?.slice(0, 1).toUpperCase() || '?';
    $('#room-title').textContent = room.peer?.nick || 'Conversación anónima';
    $('#room-state').textContent = `${room.mode === 'stored' ? 'Historial cifrado en el servidor' : 'Efímera: la otra persona debe estar conectada; historial solo en esta página'}. ${room.status === 'pending' ? `Pendiente de aceptación. Mensajes de gracia: ${room.grace_messages_used}/${room.grace_message_limit}.` : room.status === 'closed' ? 'Conversación cerrada.' : 'Conversación aceptada.'}`;
    const actions = $('#room-actions'); actions.replaceChildren();
    if (room.role === 'host' && room.status === 'pending') button(actions, 'Aceptar conversación', () => chat.change(room.id, 'accept'));
    if (room.role === 'host' && room.status === 'active' && room.mode === 'ephemeral') button(actions, 'Guardar mensajes futuros', async () => {
      if (confirm('Los mensajes futuros se guardarán cifrados en el servidor. Los mensajes efímeros anteriores no se subirán. ¿Continuar?')) await chat.change(room.id, 'upgrade');
    });
    if (room.status !== 'closed') button(actions, 'Cerrar conversación', async () => {
      if (confirm('¿Cerrar esta conversación para ambas personas?')) await chat.change(room.id, 'close');
    });
    button(actions, 'Abandonar conversación', async () => {
      if (confirm('Perderás el acceso a esta conversación y su historial. ¿Abandonarla?')) await chat.change(room.id, 'leave');
    });
    if (room.status === 'active') button(actions, 'Compartir copia del historial local', async () => {
      if (confirm('Enviarás a la otra persona una copia cifrada de los mensajes que conserva esta página. Ambos debéis estar conectados. ¿Continuar?')) {
        await chat.replay(room.id); notify('Copia transmitida. Pide a la otra persona que compruebe la recepción.');
      }
    });
    const vote = $('#vote'); vote.replaceChildren();
    if (room.vote) {
      const p = document.createElement('p');
      p.textContent = room.vote.status === 'open' ? `El anfitrión ya ha aceptado la conversación. Ahora se decide qué hacer con los mensajes enviados antes de aceptarla. «Conservar» vota por mantenerlos; «Eliminar» vota por descartarlos. El envío se pausa hasta que termine la votación, como máximo a las ${new Date(room.vote.expires_at).toLocaleTimeString()}. No votar cuenta como eliminar. Tu voto es definitivo y no cambia por sí solo el modo de historial del chat.` : `Votación terminada: ${{ approved: 'se han conservado los mensajes anteriores a la aceptación', rejected: 'se han eliminado los mensajes anteriores a la aceptación', cancelled: 'se ha cancelado' }[room.vote.status]}.`;
      vote.append(p);
      if (room.vote.status === 'open' && room.vote.my_vote === null) for (const [label, choice] of [['Conservar', true], ['Eliminar', false]]) {
        button(vote, label, async () => { try { await api.ballot(room.vote.id, choice); } finally { await chat.refresh(room.id); } });
      }
      if (room.vote.my_vote !== null) { const p = document.createElement('p'); p.textContent = 'Tu voto: ' + (room.vote.my_vote ? 'conservar' : 'eliminar'); vote.append(p); }
    }
    $('#older').hidden = !room.cursor || room.mode !== 'stored' || room.messages.size >= 500;
    $('#history-limit').hidden = room.messages.size < 500;
    const messages = $('#messages'), previousTop = messages.scrollTop;
    const atBottom = messages.scrollHeight - messages.clientHeight - previousTop < 48;
    messages.replaceChildren();
    let previousDay = null;
    for (const m of [...room.messages.values()].sort((a,b) => a.time.localeCompare(b.time) || a.id.localeCompare(b.id))) {
      const date = new Date(m.time), day = date.toLocaleDateString();
      if (day !== previousDay) {
        const divider = document.createElement('li'); divider.className = 'day-divider';
        divider.textContent = day === new Date().toLocaleDateString() ? 'HOY' : day;
        messages.append(divider); previousDay = day;
      }
      const li = document.createElement('li'); li.className = m.own ? 'message own' : 'message';
      const text = document.createElement('p'); text.textContent = m.text;
      const status = document.createElement('small'), item = chat.outbox.get(m.id);
      status.textContent = (m.recovered ? 'Copia compartida · ' : m.own ? 'Tú · ' : 'Interlocutor · ') + new Date(m.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + (item ? ' · ' + ({ unknown: 'sin confirmar', offered: 'esperando conexión', pending: 'guardado, pendiente de entrega', delivered: 'entregado', failed: 'fallido', expired: 'caducado' }[item.status]) : '');
      li.append(text, status);
      if (item && ['unknown','pending'].includes(item.status) && item.mode === 'stored') button(li, 'Comprobar y reintentar', () => chat.retry(m.id));
      messages.append(li);
    }
    messages.scrollTop = switched || atBottom ? messages.scrollHeight : previousTop;
    sendBlocked = !chat.ready || room.status === 'closed' || room.vote?.status === 'open' || (room.status === 'pending' && room.grace_messages_used >= room.grace_message_limit);
    $('#message-text').disabled = sendBlocked; updateComposer();
  }
  function updateComposer() { $('#send-message').disabled = sendBlocked || !$('#message-text').value.trim(); }
  $('#chat-search').oninput = render;
  $('#back-to-rooms').onclick = () => { container.dataset.mobile = 'list'; $('#chat-search').focus(); };
  $('#message-text').oninput = updateComposer;
  $('#message-text').onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault(); if (!$('#send-message').disabled) $('#send-form').requestSubmit();
    }
  };
  $('#new-conversation').onclick = invitationDialog(container, { api, run, notify,
    isAlive: () => !disposed && isAlive(), reconcile: () => chat.list(), onRedeemed: async room => {
      chat.room(room); container.dataset.mobile = 'conversation'; await chat.select(room.id);
    } });
  $('#refresh-rooms').onclick = () => run(() => chat.sync());
  $('#more-rooms').onclick = () => run(() => chat.list(true));
  $('#older').onclick = () => run(async () => {
    const list = $('#messages'), height = list.scrollHeight, top = list.scrollTop;
    await chat.refresh(chat.selected, true); if (!disposed) list.scrollTop = top + list.scrollHeight - height;
  });
  $('#send-form').onsubmit = e => { e.preventDefault(); const text = $('#message-text').value; if (sendBlocked || !text.trim()) return; run(async () => { await chat.send(chat.selected, text); if (!disposed) { $('#message-text').value = ''; updateComposer(); } }); };
  function dispose() { disposed = true; container.querySelector('dialog')?.close(); chat.dispose(); container.replaceChildren(); }
  try {
    await chat.start();
    const initial = initialConversation();
    if (initial && !disposed && isAlive()) { container.dataset.mobile = 'conversation'; await chat.select(initial); }
  } catch (error) { dispose(); throw error; }
  if (!isAlive() || !container.isConnected) dispose();
  return dispose;
}
