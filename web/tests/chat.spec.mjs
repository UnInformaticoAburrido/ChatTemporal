import { test, expect } from '@playwright/test';
import { LocalKey } from '../public/keys.js';

const user = { id: '12345678-1234-4234-8234-123456789abc', nick: 'ana', email: 'ana@example.com', email_verified: true };
const roomId = '12345678-1234-4234-8234-123456789def';
const password = 'synthetic backup password';
const phrase = Array(23).fill('abandon').concat('art').join(' ');
const frame = (type, payload, conversation_id = roomId) => ({ type, request_id: crypto.randomUUID(),
  conversation_id, timestamp: new Date().toISOString(), payload });
async function setup(page, { mode = 'stored', role = 'host', status = 'active', initialVote = null } = {}) {
  const key = LocalKey.generate(user.id), peer = LocalKey.generate(user.id), sent = [], sockets = [], requests = [], states = new Map();
  const backup = await key.backup(password);
  const room = { id: roomId, mode, role, status, accepted_at: status === 'pending' ? null : '2026-01-01T00:00:00Z',
    grace_message_limit: 5, grace_messages_used: 0, peer: status === 'pending' ? null : { nick: '<b>bob</b>' } };
  const data = { vote: initialVote, history: [], failChat: false };
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    requests.push({ path, method: request.method(), body: request.postData() });
    const reply = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
    if (path.endsWith('/recover')) return reply({ access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 });
    if (path.endsWith('/users/me')) return reply(user);
    if (path.endsWith('/logout')) return route.fulfill({ status: 204 });
    if (path.endsWith('/users/' + user.id + '/keys')) return reply({ public_key: key.publicKey, protocol_version: 1 });
    if (path.endsWith('/ws-ticket')) return reply({ ticket: 'synthetic-ticket-' + (sockets.length + 1) });
    if (path.endsWith('/conversations')) return data.failChat ? reply({ error: { code: 'TEMPORARY_UNAVAILABLE' } }, 503) : reply({ items: [room], next_cursor: null });
    if (path.endsWith('/conversations/' + roomId)) {
      if (request.method() === 'DELETE') { room.status = 'closed'; return reply(room); }
      return reply(room);
    }
    if (path.endsWith('/key')) return reply({ public_key: peer.publicKey, protocol_version: 1 });
    if (path.endsWith('/vote')) return reply(data.vote);
    if (path.endsWith('/messages')) return reply({ items: data.history, next_cursor: null });
    if (path.endsWith('/status')) {
      const identifier = path.split('/').at(-2);
      return states.has(identifier) ? reply({ status: states.get(identifier) }) : reply({ error: { code: 'MESSAGE_NOT_FOUND' } }, 404);
    }
    if (path.endsWith('/accept')) {
      room.status = 'active'; room.accepted_at = new Date().toISOString(); room.peer = { nick: '<b>bob</b>' };
      data.vote = { id: crypto.randomUUID(), status: 'open', expires_at: new Date(Date.now() + 30000).toISOString(), my_vote: null };
      return reply({ conversation: room, vote: data.vote });
    }
    if (path.endsWith('/ballots')) { data.vote.my_vote = request.postDataJSON().choice; return reply(data.vote); }
    if (path.endsWith('/upgrade')) { room.mode = 'stored'; return reply(room); }
    if (path.endsWith('/invitations/me')) return reply({ ephemeral_code: 'ephemeral-synthetic-code', stored_code: 'stored-synthetic-code' });
    if (path.endsWith('/redeem')) return reply({ ...room, host_public_key: peer.publicKey });
    throw new Error('Unexpected synthetic API endpoint: ' + path);
  });
  await page.routeWebSocket('**/ws/v1?*', ws => {
    sockets.push(ws);
    ws.onMessage(value => {
      const event = JSON.parse(value); sent.push(event);
      if (event.type === 'message.send') states.set(event.payload.message_id, 'pending');
    });
    ws.send(JSON.stringify(frame('session.ready', {}, null)));
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).click();
  await page.getByLabel('Correo electrónico', { exact: true }).fill(user.email);
  await page.getByLabel('Frase de recuperación', { exact: true }).fill(phrase);
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).last().click();
  await page.getByRole('button', { name: 'Gestionar claves de conversación' }).click();
  await page.getByLabel('Copia cifrada de claves').setInputFiles({ name: 'test.chatkey', mimeType: 'application/json', buffer: Buffer.from(backup) });
  await page.getByLabel('Contraseña de la copia', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Abrir copia y comprobar clave' }).click();
  await expect(page.getByText('Conectado · cifrado de extremo a extremo', { exact: true })).toBeVisible();
  await page.getByRole('navigation', { name: 'Conversaciones' }).getByRole('button').click();
  await expect(page.getByLabel('Mensaje', { exact: true })).toBeEnabled();
  return { key, peer, room, data, sent, sockets, requests, states };
}
const sendEvent = (sockets, type, payload) => sockets.at(-1).send(JSON.stringify(frame(type, payload)));

test('stored cifra, descifra sin HTML, confirma y reintenta el mismo envío tras un corte', async ({ page }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const { key, peer, sent, sockets, requests, states } = await setup(page);
  await page.getByLabel('Mensaje', { exact: true }).fill('Mensaje privado 👋');
  await page.getByRole('button', { name: 'Enviar mensaje', exact: true }).click();
  await expect.poll(() => sent.filter(event => event.type === 'message.send').length).toBe(1);
  const first = sent.find(event => event.type === 'message.send');
  expect(peer.decrypt(first.payload)).toBe('Mensaje privado 👋');
  expect(JSON.stringify(first)).not.toContain('Mensaje privado');
  await expect(page.getByLabel('Mensajes', { exact: true })).toContainText('sin confirmar');
  const incoming = { message_id: crypto.randomUUID(), sender_role: 'guest', sent_at: new Date().toISOString(), ...peer.encrypt('<script>texto</script>', key.publicKey) };
  sendEvent(sockets, 'message.new', incoming);
  await expect(page.getByLabel('Mensajes', { exact: true })).toContainText('<script>texto</script>');
  await expect.poll(() => sent.some(event => event.type === 'message.ack' && event.payload.message_id === incoming.message_id)).toBe(true);
  expect(await page.locator('#messages script, #room-title b').count()).toBe(0);
  sockets.at(-1).close({ code: 1001 });
  await expect.poll(() => sockets.length).toBe(2);
  await expect(page.getByText('Conectado · cifrado de extremo a extremo', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Comprobar y reintentar' }).click();
  await expect.poll(() => sent.filter(event => event.type === 'message.send').length).toBe(2);
  expect(sent.filter(event => event.type === 'message.send')[1]).toEqual(first);
  states.set(first.payload.message_id, 'delivered'); sendEvent(sockets, 'message.delivered', { message_id: first.payload.message_id });
  await expect(page.getByLabel('Mensajes', { exact: true })).toContainText('entregado');
  expect(requests.filter(request => request.path.endsWith('/ws-ticket'))).toHaveLength(2);
  expect(requests.every(request => !request.body?.includes('Mensaje privado'))).toBe(true);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await page.getByRole('button', { name: 'Bloquear clave', exact: true }).click();
  await expect(page.getByLabel('Mensajes', { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]); key.lock(); peer.lock();
});

test('ephemeral negocia ready y elimina gracia al resolver la votación, incluso con aviso atrasado', async ({ page }) => {
  const { key, peer, sent, sockets, room, data } = await setup(page, { mode: 'ephemeral', status: 'pending' });
  await expect(page.getByRole('heading', { name: 'Conversación anónima', exact: true })).toBeVisible();
  await page.getByLabel('Mensaje', { exact: true }).fill('Gracia efímera');
  await page.getByRole('button', { name: 'Enviar mensaje', exact: true }).click();
  await expect.poll(() => sent.filter(event => event.type === 'message.offer').length).toBe(1);
  const offer = sent.find(event => event.type === 'message.offer');
  expect(sent.some(event => event.type === 'message.send')).toBe(false);
  sendEvent(sockets, 'message.ready', offer.payload);
  await expect.poll(() => sent.filter(event => event.type === 'message.send').length).toBe(1);
  sendEvent(sockets, 'message.delivered', offer.payload);
  await page.getByRole('button', { name: 'Aceptar conversación', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Enviar mensaje', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Eliminar', exact: true }).click();
  await expect(page.getByText('Tu voto: eliminar', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Enviar mensaje', exact: true })).toBeDisabled();
  data.vote.status = 'rejected';
  sendEvent(sockets, 'vote.updated', data.vote);
  await expect(page.getByLabel('Mensajes', { exact: true })).not.toContainText('Gracia efímera');
  await expect(page.getByRole('button', { name: 'Enviar mensaje', exact: true })).toBeEnabled();
  sendEvent(sockets, 'vote.opened', { ...data.vote, status: 'open' });
  await expect(page.getByText('Votación: mensajes anteriores eliminados.', { exact: true })).toBeVisible();
  page.on('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Guardar mensajes futuros', exact: true }).click();
  expect(room.mode).toBe('stored');
  await expect(page.locator('#room-state')).toContainText('Historial cifrado en el servidor');
  key.lock(); peer.lock();
});

test('rol guest, invitaciones y mensajes corruptos respetan permisos y no generan ACK', async ({ page }) => {
  const { key, peer, sockets, sent } = await setup(page, { role: 'guest', status: 'pending' });
  await expect(page.getByRole('button', { name: 'Aceptar conversación', exact: true })).toHaveCount(0);
  await page.getByText('Invitar o usar una invitación', { exact: true }).click();
  await page.getByRole('button', { name: 'Mostrar mis invitaciones', exact: true }).click();
  await expect(page.getByLabel('Con historial cifrado', { exact: true })).toHaveValue('stored-synthetic-code');
  await page.getByLabel('Código de invitación', { exact: true }).fill('synthetic-invitation');
  await page.getByRole('button', { name: 'Iniciar conversación', exact: true }).click();
  const message_id = crypto.randomUUID();
  sendEvent(sockets, 'message.new', { message_id, sender_role: 'host', sent_at: new Date().toISOString(),
    ...peer.encrypt('Corrupto', key.publicKey), ciphertext: 'a'.repeat(32) });
  await expect(page.locator('#notice')).toContainText('No se ha podido autenticar');
  expect(sent.some(event => event.type === 'message.ack' && event.payload.message_id === message_id)).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  key.lock(); peer.lock();
});

test('revocación del socket limpia textos y vuelve al acceso', async ({ page }) => {
  const { key, peer, sockets } = await setup(page);
  sendEvent(sockets, 'message.new', { message_id: crypto.randomUUID(), sender_role: 'guest', sent_at: new Date().toISOString(),
    ...peer.encrypt('Texto para limpiar', key.publicKey) });
  await expect(page.getByLabel('Mensajes', { exact: true })).toContainText('Texto para limpiar');
  sockets.at(-1).close({ code: 4401 });
  await expect(page.getByRole('heading', { name: 'Vuelve a tu cuenta' })).toBeVisible();
  await expect(page.getByLabel('Mensajes', { exact: true })).toHaveCount(0);
  expect(await page.locator('body').innerText()).not.toContain('Texto para limpiar');
  key.lock(); peer.lock();
});
