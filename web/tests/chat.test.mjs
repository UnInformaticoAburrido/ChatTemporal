import test from 'node:test';
import assert from 'node:assert/strict';
import { Chat, envelope } from '../public/chat.js';
import { ApiError } from '../public/api.js';
import { LocalKey } from '../public/keys.js';

const user = '12345678-1234-4234-8234-123456789abc';
const id = '12345678-1234-4234-8234-123456789def';
const now = new Date().toISOString();
function fixture(t, overrides = {}, mode = 'stored') {
  const key = LocalKey.generate(user), peer = LocalKey.generate(user), sent = [], errors = [];
  const room = { id, role: 'host', mode, status: 'active', accepted_at: '2026-01-01T00:00:00Z',
    grace_messages_used: 0, grace_message_limit: 5, peer: { nick: 'bob' } };
  const api = { signedIn: true, conversations: async () => ({ items: [room], next_cursor: null }),
    conversation: async () => room, currentVote: async () => null, peerKey: async () => ({ public_key: peer.publicKey }),
    history: async () => ({ items: [], next_cursor: null }), status: async () => { throw new ApiError('MESSAGE_NOT_FOUND'); },
    ...overrides };
  const ws = { readyState: 1, send: value => sent.push(JSON.parse(value)), close() {} };
  const chat = new Chat(api, key, { origin: 'https://example.test', error: e => errors.push(e), socket: () => ws });
  chat.room(room); chat.ready = true; chat.ws = ws;
  t.after(() => { chat.dispose(); key.lock(); peer.lock(); });
  return { chat, api, key, peer, sent, room, ws, errors };
}
function received(peer, key, fields = {}) {
  return { message_id: crypto.randomUUID(), sender_role: 'guest', sent_at: now, ...peer.encrypt('Hola 👋', key.publicKey), ...fields };
}

test('stored cifra localmente; un reintento conserva el UUID y payload exactos', async t => {
  const { chat, api, peer, sent } = fixture(t);
  await chat.send(id, 'Hola privado');
  const first = structuredClone(sent[0]);
  assert.equal(first.type, 'message.send');
  assert.equal(peer.decrypt(first.payload), 'Hola privado');
  assert.ok(!JSON.stringify(first).includes('Hola privado'));
  assert.equal(chat.outbox.get(first.payload.message_id).status, 'unknown');
  await chat.retry(first.payload.message_id);
  assert.deepEqual(sent[1], first);
  api.status = async () => ({ status: 'delivered' });
  await chat.retry(first.payload.message_id);
  assert.equal(sent.length, 2);
  assert.equal(chat.outbox.get(first.payload.message_id).status, 'delivered');
});

test('ephemeral exige ready, no reenvía el intento y no consulta ofertas como mensajes durables', async t => {
  let checks = 0;
  const { chat, sent } = fixture(t, { status: async () => { checks++; } }, 'ephemeral');
  await chat.send(id, 'Efímero');
  const message = sent[0].payload.message_id;
  assert.equal(sent[0].type, 'message.offer');
  await chat.checkStatus(message);
  assert.equal(checks, 0);
  await chat.handle(envelope('message.ready', id, { message_id: message }));
  assert.equal(sent[1].type, 'message.send');
  await chat.handle(envelope('message.ready', id, { message_id: message }));
  await chat.retry(message);
  assert.equal(sent.length, 2);
  await chat.handle(envelope('message.delivered', id, { message_id: message }));
  await chat.handle(envelope('message.failed', id, { message_id: message }));
  assert.equal(chat.outbox.get(message).status, 'delivered');
});

test('un estado REST atrasado y errores antiguos no degradan una entrega confirmada', async t => {
  let finish, started;
  const reading = new Promise(resolve => { started = resolve; });
  const { chat, api, sent } = fixture(t);
  await chat.send(id, 'Entregado');
  const message = sent[0].payload.message_id;
  api.status = () => new Promise(resolve => { finish = resolve; started(); });
  const retry = chat.retry(message); await reading;
  await chat.handle(envelope('message.delivered', id, { message_id: message }));
  finish({ status: 'pending' }); await retry;
  const error = envelope('system.error', id, { code: 'VOTE_OPEN' }); error.request_id = sent[0].request_id;
  await assert.rejects(chat.handle(error), { code: 'VOTE_OPEN' });
  assert.equal(chat.outbox.get(message).status, 'delivered');
  assert.equal(sent.length, 1);
});

test('ACK solo después de autenticar y descifrar; historial propio y cerrado no se confirma', async t => {
  const { chat, key, peer, sent } = fixture(t);
  const room = chat.rooms.get(id), message = received(peer, key);
  await chat.receive(room, message, true);
  assert.equal(room.messages.get(message.message_id).text, 'Hola 👋');
  assert.equal(sent[0].type, 'message.ack');
  await assert.rejects(chat.receive(room, { ...message, ciphertext: 'a'.repeat(32) }, true), { code: 'MESSAGE_CORRUPTED' });
  assert.equal(sent.length, 1);
  room.publicKey = peer.publicKey;
  const own = { ...received(peer, key), ...key.encrypt('Propio', peer.publicKey), sender_role: 'host' };
  await chat.receive(room, own, false);
  assert.equal(room.messages.get(own.message_id).text, 'Propio');
  room.status = 'closed';
  await chat.receive(room, received(peer, key), false);
  assert.equal(sent.length, 1);
});

test('consulta tras reconexión recupera varias páginas y mantiene el cursor de mensajes antiguos', async t => {
  const { chat, api, peer, key } = fixture(t);
  const old = received(peer, key), recent = received(peer, key), middle = received(peer, key);
  const calls = [];
  api.history = async (_, cursor) => {
    calls.push(cursor);
    if (cursor === 'new-page') return { items: [middle, old], next_cursor: 'older-original' };
    if (cursor === 'older-original') return { items: [received(peer, key)], next_cursor: null };
    return calls.length === 1 ? { items: [old], next_cursor: 'older-original' }
      : { items: [recent], next_cursor: 'new-page' };
  };
  await chat.refresh(id);
  await chat.refresh(id);
  assert.deepEqual(calls, [null, null, 'new-page']);
  assert.equal(chat.rooms.get(id).cursor, 'older-original');
  assert.equal(chat.rooms.get(id).messages.size, 3);
  await chat.refresh(id, true);
  assert.equal(calls.at(-1), 'older-original');
  assert.equal(chat.rooms.get(id).cursor, null);
});

test('voto rechazado elimina gracia en salas no seleccionadas; un aviso atrasado consulta REST', async t => {
  const { chat, api, peer, key } = fixture(t, {}, 'ephemeral');
  const room = chat.rooms.get(id);
  const grace = received(peer, key, { sent_at: '2025-12-31T23:59:00Z' });
  await chat.receive(room, grace, true);
  const vote = { id: crypto.randomUUID(), status: 'rejected', my_vote: false };
  api.currentVote = async () => vote;
  await chat.sync();
  assert.equal(room.messages.size, 0);
  await chat.handle(envelope('vote.opened', id, { ...vote, status: 'open' }));
  assert.equal(room.vote.status, 'rejected');
  assert.equal(room.vote.my_vote, false);
  chat.applyVote(room, { ...vote, status: 'open' });
  assert.equal(room.vote.status, 'rejected');
  await chat.receive(room, grace, true);
  assert.equal(room.messages.size, 0);
});

test('salir con respuesta perdida conserva acceso hasta reconciliar; 404 borra texto y envíos', async t => {
  const { chat, api } = fixture(t, { change: async () => { throw new ApiError('NETWORK_ERROR'); } });
  await chat.send(id, 'Borrador privado');
  chat.selected = id;
  await assert.rejects(chat.change(id, 'leave'), { code: 'NETWORK_ERROR' });
  assert.equal(chat.rooms.size, 1); assert.equal(chat.outbox.size, 1);
  api.conversation = async () => { throw new ApiError('CONVERSATION_NOT_FOUND'); };
  await assert.rejects(chat.refresh(id), { code: 'CONVERSATION_NOT_FOUND' });
  assert.equal(chat.rooms.size, 0); assert.equal(chat.outbox.size, 0); assert.equal(chat.selected, null);
});

test('desconexión entre consulta de clave y envío conserva el borrador sin crear un intento', async t => {
  const { chat, api, peer, sent } = fixture(t);
  api.peerKey = async () => { chat.ready = false; return { public_key: peer.publicKey }; };
  await assert.rejects(chat.send(id, 'Pendiente'), { code: 'NOT_CONNECTED' });
  assert.equal(chat.outbox.size, 0); assert.equal(sent.length, 0);
});

test('polling limita consultas de envíos y reserva la descarga de salas no seleccionadas para reconexión', async t => {
  const { chat, api, room } = fixture(t);
  const other = crypto.randomUUID();
  chat.room({ ...room, id: other });
  for (const value of chat.rooms.values()) value.messages.set('local', { id: 'local', time: now });
  const checked = [], histories = [];
  api.currentVote = async () => ({ status: 'approved' });
  api.conversation = async identifier => ({ ...room, id: identifier });
  api.history = async identifier => { histories.push(identifier); return { items: [], next_cursor: null }; };
  api.status = async identifier => { checked.push(identifier); return { status: 'pending' }; };
  for (let i = 0; i < 6; i++) await chat.send(id, 'Pendiente ' + i);
  await chat.sync();
  assert.equal(histories.length, 0); assert.equal(checked.length, 4);
  await chat.sync(true);
  assert.deepEqual(histories, [id, other]);
  assert.equal(new Set(checked).size, 6);
});

test('dispose durante una lectura impide reintroducir mensajes y elimina temporizadores', async t => {
  let finish, started;
  const reading = new Promise(resolve => { started = resolve; });
  const { chat, api } = fixture(t, {}, 'ephemeral');
  await chat.send(id, 'Temporal');
  assert.equal(chat.timers.size, 1);
  api.conversation = () => new Promise(resolve => { finish = resolve; started(); });
  const loading = chat.refresh(id);
  await reading;
  chat.dispose(); finish({ id }); await loading;
  assert.equal(chat.rooms.size, 0); assert.equal(chat.outbox.size, 0); assert.equal(chat.timers.size, 0);
});

test('ticket nuevo por conexión y pérdida de sesión cierran el chat', async t => {
  let tickets = 0, cleared = false;
  const { chat, api, ws } = fixture(t, { ticket: async () => ({ ticket: 'one-use-' + ++tickets }) });
  api.clear = () => { api.signedIn = false; cleared = true; };
  chat.ready = false;
  const urls = []; chat.socketFactory = url => { urls.push(url); return ws; };
  await chat.connect();
  await chat.handle(envelope('session.ready', null, {}));
  ws.onclose({ code: 1001 });
  await chat.connect();
  assert.deepEqual(urls.map(url => new URL(url).searchParams.get('ticket')), ['one-use-1', 'one-use-2']);
  assert.ok(urls.every(url => url.startsWith('wss:') && !url.includes('access_token')));
  ws.onclose({ code: 4401 });
  assert.equal(cleared, true); assert.equal(chat.stopped, true);
});

test('replay es atómico y no resucita gracia rechazada; se limita memoria y caducidad', async t => {
  const { chat, peer, key } = fixture(t);
  const room = chat.rooms.get(id), replay_id = crypto.randomUUID();
  room.vote = { status: 'rejected' };
  chat.receiveReplay(room, envelope('recovery.replay.begin', id, { replay_id }));
  chat.receiveReplay(room, envelope('recovery.replay.item', id, { replay_id, sequence: 0,
    original_message_id: crypto.randomUUID(), original_timestamp: '2025-12-31T00:00:00Z', ...peer.encrypt('Gracia', key.publicKey) }));
  assert.equal(room.messages.size, 0);
  chat.receiveReplay(room, envelope('recovery.replay.end', id, { replay_id, item_count: 1 }));
  assert.equal(room.messages.size, 0);
  for (let i = 0; i < 505; i++) room.messages.set(String(i), { id: String(i), time: now });
  chat.trim(room); assert.equal(room.messages.size, 500);
  room.messages.set('expired', { expires: '2000-01-01T00:00:00Z' });
  chat.prune(room); assert.equal(room.messages.has('expired'), false);
});
