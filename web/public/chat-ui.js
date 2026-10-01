import { Chat } from './chat.js';
import { errorMessage } from './api.js';

export async function mountChat({ api, key, container, run, notify }) {
  let disposed = false, shown = null;
  container.innerHTML = `<h3 tabindex="-1">Tus conversaciones</h3><p id="connection" role="status"></p>
    <details><summary>Invitar o usar una invitación</summary>
      <button id="invitation-codes" class="secondary">Mostrar mis invitaciones</button><div id="codes"></div>
      <form id="redeem-form"><label for="invitation">Código de invitación</label><textarea id="invitation" name="code" rows="3" maxlength="4096" required spellcheck="false"></textarea>
        <button class="primary">Iniciar conversación</button></form></details>
    <button id="refresh-rooms" class="secondary">Actualizar conversaciones</button>
    <nav id="rooms" aria-label="Conversaciones"></nav><button id="more-rooms" class="secondary" hidden>Más conversaciones</button>
    <section id="conversation" aria-label="Conversación seleccionada" hidden>
      <h3 id="room-title" tabindex="-1"></h3><p id="room-state" class="muted"></p><div id="room-actions" class="actions"></div>
      <section id="vote" aria-label="Votación"></section>
      <button id="older" class="secondary" hidden>Cargar mensajes anteriores</button>
      <ol id="messages" class="messages" aria-label="Mensajes"></ol>
      <form id="send-form"><label for="message-text">Mensaje</label><textarea id="message-text" rows="3" required aria-describedby="message-limit"></textarea>
        <small id="message-limit">Hasta 256 caracteres. El contenido se cifra en este dispositivo.</small><button class="primary" id="send-message">Enviar mensaje</button></form>
    </section>`;
  const $ = selector => container.querySelector(selector);
  const chat = new Chat(api, key, { changed: render, error: e => notify(errorMessage(e), true) });
  function button(parent, text, action, className = 'secondary') {
    const element = document.createElement('button'); element.type = 'button'; element.className = className; element.textContent = text;
    element.onclick = () => run(action); parent.append(element); return element;
  }
  function render() {
    if (disposed || !container.isConnected) return;
    $('#connection').textContent = chat.ready ? 'Conectado · cifrado de extremo a extremo' : 'Reconectando… Puedes consultar el historial disponible.';
    const rooms = $('#rooms'); rooms.replaceChildren();
    [...chat.rooms.values()].forEach(room => {
      const b = button(rooms, (room.peer?.nick || 'Conversación anónima') + ' · ' + ({ pending: 'pendiente', active: 'activa', closed: 'cerrada' }[room.status]), () => chat.select(room.id));
      if (chat.selected === room.id) b.setAttribute('aria-current', 'page');
    });
    if (!chat.rooms.size) rooms.textContent = 'Todavía no tienes conversaciones. Comparte una invitación o introduce la de otra persona.';
    $('#more-rooms').hidden = !chat.next;
    const room = chat.rooms.get(chat.selected); $('#conversation').hidden = !room;
    if (!room) return;
    if (shown !== room.id) { $('#message-text').value = ''; shown = room.id; }
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
      p.textContent = room.vote.status === 'open' ? `¿Conservar los mensajes anteriores a la aceptación? El envío se pausa hasta ${new Date(room.vote.expires_at).toLocaleTimeString()}. Abstenerse cuenta como no. Tu voto es definitivo.` : `Votación: ${{ approved: 'mensajes anteriores conservados', rejected: 'mensajes anteriores eliminados', cancelled: 'cancelada' }[room.vote.status]}.`;
      vote.append(p);
      if (room.vote.status === 'open' && room.vote.my_vote === null) for (const [label, choice] of [['Conservar', true], ['Eliminar', false]]) {
        button(vote, label, async () => { try { await api.ballot(room.vote.id, choice); } finally { await chat.refresh(room.id); } });
      }
      if (room.vote.my_vote !== null) { const p = document.createElement('p'); p.textContent = 'Tu voto: ' + (room.vote.my_vote ? 'conservar' : 'eliminar'); vote.append(p); }
    }
    $('#older').hidden = !room.cursor || room.mode !== 'stored';
    const messages = $('#messages'); messages.replaceChildren();
    for (const m of [...room.messages.values()].sort((a,b) => a.time.localeCompare(b.time) || a.id.localeCompare(b.id))) {
      const li = document.createElement('li'); li.className = m.own ? 'message own' : 'message';
      const text = document.createElement('p'); text.textContent = m.text;
      const status = document.createElement('small'), item = chat.outbox.get(m.id);
      status.textContent = (m.recovered ? 'Copia compartida · ' : m.own ? 'Tú · ' : 'Interlocutor · ') + new Date(m.time).toLocaleTimeString() + (item ? ' · ' + ({ unknown: 'sin confirmar', offered: 'esperando conexión', pending: 'guardado, pendiente de entrega', delivered: 'entregado', failed: 'fallido', expired: 'caducado' }[item.status]) : '');
      li.append(text, status);
      if (item && ['unknown','pending'].includes(item.status) && item.mode === 'stored') button(li, 'Comprobar y reintentar', () => chat.retry(m.id));
      messages.append(li);
    }
    const blocked = !chat.ready || room.status === 'closed' || room.vote?.status === 'open' || (room.status === 'pending' && room.grace_messages_used >= room.grace_message_limit);
    $('#send-message').disabled = blocked; $('#message-text').disabled = blocked;
  }
  async function showCodes(regenerate = false) {
    const codes = await (regenerate ? api.regenerate() : api.invitations()); if (disposed) return;
    const area = $('#codes'); area.replaceChildren();
    for (const [label, value] of [['Efímera (requiere conexión simultánea)', codes.ephemeral_code], ['Con historial cifrado', codes.stored_code]]) {
      const p = document.createElement('p'); p.textContent = label;
      const field = document.createElement('textarea'); field.readOnly = true; field.value = value; field.setAttribute('aria-label', label); area.append(p, field);
    }
    button(area, 'Renovar invitaciones', async () => { if (confirm('Los códigos anteriores dejarán de funcionar. ¿Renovarlos?')) await showCodes(true); });
  }
  $('#invitation-codes').onclick = () => run(() => showCodes());
  $('#redeem-form').onsubmit = e => { e.preventDefault(); const code = $('#invitation').value.trim(); run(async () => {
    try { const room = await api.redeem(code); if (disposed) return; chat.room(room); $('#invitation').value = ''; await chat.select(room.id); }
    catch (error) { await chat.list(); throw error; } // Un canje dudoso puede haber creado la conversación: consultar antes de repetir.
  }); };
  $('#refresh-rooms').onclick = () => run(() => chat.sync());
  $('#more-rooms').onclick = () => run(() => chat.list(true));
  $('#older').onclick = () => run(() => chat.refresh(chat.selected, true));
  $('#send-form').onsubmit = e => { e.preventDefault(); const text = $('#message-text').value; run(async () => { await chat.send(chat.selected, text); if (!disposed) $('#message-text').value = ''; }); };
  try { await chat.start(); } catch (error) { chat.dispose(); throw error; }
  return () => { disposed = true; chat.dispose(); container.replaceChildren(); };
}
