import { test, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { LocalKey } from '../public/keys.js';

const evidence = new URL('../../artifacts/conversations/', import.meta.url);
await mkdir(evidence, { recursive: true });
const capture = async (page, project, name) => {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: new URL(`${project}-${name}.png`, evidence).pathname });
};

const phrase = Array(23).fill('abandon').concat('art').join(' ');
const password = 'synthetic backup password';
const roomId = '22345678-1234-4234-8234-123456789abc';
const frame = (type, payload, conversation_id = roomId) => ({ type, payload, conversation_id,
  request_id: crypto.randomUUID(), timestamp: new Date().toISOString() });

async function fixture(pages, { existing = false, secondRoom = false } = {}) {
  const users = ['Ana', 'Luis'].map((nick, i) => ({ id: `12345678-1234-4234-8234-123456789ab${i}`, nick, email: `${nick}@example.com`, email_verified: true }));
  const keys = users.map(user => LocalKey.generate(user.id));
  const backups = await Promise.all(keys.map(key => key.backup(password)));
  const secondId = '32345678-1234-4234-8234-123456789abc';
  const sockets = [], wire = [], requests = [], owners = new Map(), errors = [];
  let created = existing;
  const room = i => ({ id: roomId, role: i ? 'guest' : 'host', mode: 'ephemeral', status: 'active',
    accepted_at: '2026-01-01T00:00:00Z', peer: { nick: users[1-i].nick }, grace_messages_used: 0, grace_message_limit: 5 });
  for (const [i, page] of pages.entries()) {
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/**', route => {
      const req = route.request(), path = new URL(req.url()).pathname;
      requests.push({ path, body: req.postData() });
      const reply = (json, status = 200) => route.fulfill({ json, status });
      if (path.endsWith('/recover')) return reply({ access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 });
      if (path.endsWith('/users/me')) return reply(users[i]);
      if (path.endsWith('/keys')) return reply({ public_key: keys[i].publicKey, protocol_version: 1 });
      if (path.endsWith('/ws-ticket')) return reply({ ticket: 'synthetic-ticket' });
      if (path.endsWith('/conversations')) return reply({ items: created ? [room(i), ...(secondRoom ? [{ ...room(i), id: secondId, peer: { nick: 'Marta' } }] : [])] : [], next_cursor: null });
      if (path.endsWith('/invitations/redeem')) {
        const body = req.postDataJSON();
        expect(body.expected_mode).toBe('ephemeral');
        if (body.code === 'stored-code') return reply({ error: { code: 'INVITATION_MODE_MISMATCH' } }, 400);
        created = true; return reply({ ...room(i), host_public_key: keys[1-i].publicKey }, 201);
      }
      if (path.endsWith('/conversations/' + secondId)) return reply({ ...room(i), id: secondId, peer: { nick: 'Marta' } });
      if (path.endsWith('/conversations/' + roomId)) return reply(room(i));
      if (path.endsWith('/key')) return reply({ public_key: keys[1-i].publicKey, protocol_version: 1 });
      if (path.endsWith('/vote')) return reply(null);
      throw new Error('Unexpected API request: ' + path);
    });
    await page.routeWebSocket('**/ws/v1?*', ws => {
      sockets[i] = ws;
      ws.onMessage(raw => {
        const event = JSON.parse(raw); wire.push(event);
        const id = event.payload.message_id;
        if (event.type === 'message.offer') { owners.set(id, i); sockets[1-i].send(JSON.stringify(frame('message.offer', { message_id: id }))); }
        if (event.type === 'message.ready') sockets[owners.get(id)].send(JSON.stringify(frame('message.ready', { message_id: id })));
        if (event.type === 'message.send') sockets[1-i].send(JSON.stringify(frame('message.new', { ...event.payload,
          sender_role: i ? 'guest' : 'host', sent_at: event.timestamp })));
        if (event.type === 'message.ack' && owners.has(id)) sockets[owners.get(id)].send(JSON.stringify(frame('message.delivered', { message_id: id })));
      });
      ws.send(JSON.stringify(frame('session.ready', {}, null)));
    });
    await page.goto('/#acceso');
    await page.getByLabel('Correo electrónico', { exact: true }).fill(users[i].email);
    await page.getByLabel('Palabra 1', { exact: true }).fill(phrase);
    await page.getByRole('button', { name: 'Acceder a mi cuenta', exact: true }).click();
    await page.getByRole('button', { name: 'Conversaciones', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Conversaciones', exact: true })).toBeVisible();
  }
  async function unlock(i, prepare = true) {
    const page = pages[i];
    if (prepare) await page.getByRole('button', { name: 'Abrir mis mensajes', exact: true }).click();
    await page.getByLabel('Copia cifrada de claves', { exact: true }).setInputFiles({ name: 'test.chatkey', mimeType: 'application/json', buffer: Buffer.from(backups[i]) });
    await page.getByLabel('Contraseña de la copia', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Abrir copia y comprobar clave', exact: true }).click();
    await expect(page.locator('#connection')).toContainText('Conectado');
  }
  return { requests, wire, errors, unlock, keys, sockets, secondId };
}

test('lista accesible antes de abrir claves y selección guiada hacia sus mensajes', async ({ page }, testInfo) => {
  const { errors, keys, unlock } = await fixture([page], { existing: true });
  await expect(page.getByRole('navigation', { name: 'Conversaciones activas' }).getByRole('button')).toContainText('Luis');
  await capture(page, testInfo.project.name, 'list');
  await page.getByRole('navigation', { name: 'Conversaciones activas' }).getByRole('button').click();
  await expect(page.getByRole('heading', { name: 'Abre tu copia de claves' })).toBeVisible();
  await expect(page).toHaveURL(/#chat$/);
  await page.getByRole('button', { name: 'Mi cuenta', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Ajustes de usuario' })).toBeVisible();
  await page.getByRole('button', { name: 'Conversaciones', exact: true }).click();
  await page.getByRole('button', { name: 'Nueva conversación', exact: true }).click();
  await capture(page, testInfo.project.name, 'new-conversation');
  await page.getByLabel('Código de invitación', { exact: true }).fill('stored-code');
  await page.getByRole('button', { name: 'Iniciar conversación', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('invitación efímera');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Cerrar nueva conversación' }).click();
  await unlock(0, false);
  await expect(page.getByRole('heading', { name: 'Luis', exact: true })).toBeVisible();
  expect(errors).toEqual([]); keys.forEach(key => key.lock());
});

test('dos usuarios canjean un código, seleccionan y envían mensajes cifrados en ambos sentidos sin historial remoto', async ({ page, context }, testInfo) => {
  const other = await context.newPage();
  const { requests, wire, errors, unlock, keys } = await fixture([page, other]);
  await unlock(0); await unlock(1);
  await other.getByRole('button', { name: 'Nueva conversación', exact: true }).click();
  await other.getByLabel('Código de invitación', { exact: true }).fill('ephemeral-code');
  await other.getByRole('button', { name: 'Iniciar conversación', exact: true }).click();
  await expect(other.getByRole('heading', { name: 'Ana', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Actualizar conversaciones', exact: true }).click();
  await page.getByRole('navigation', { name: 'Conversaciones', exact: true }).getByRole('button').click();
  await expect(page.getByRole('heading', { name: 'Luis', exact: true })).toBeVisible();
  for (const [sender, recipient, text] of [[other, page, 'Hola Ana, mensaje privado'], [page, other, 'Hola Luis, recibido en privado']]) {
    await sender.getByLabel('Mensaje', { exact: true }).fill(text);
    await sender.getByLabel('Mensaje', { exact: true }).press('Enter');
    await expect(recipient.getByLabel('Mensajes', { exact: true })).toContainText(text);
    await expect(sender.getByLabel('Mensajes', { exact: true })).toContainText('entregado');
  }
  await capture(page, testInfo.project.name, 'messages');
  expect(requests.some(request => request.path.endsWith('/messages'))).toBe(false);
  expect(JSON.stringify(wire)).not.toContain('Hola Ana');
  expect(JSON.stringify(wire)).not.toContain('Hola Luis');
  expect(wire.filter(event => event.type === 'message.send')).toHaveLength(2);
  for (const client of [page, other]) {
    expect(await client.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
    expect(await client.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Bienvenido de nuevo' })).toBeVisible();
  await expect(page.locator('body')).not.toContainText('Hola Ana, mensaje privado');
  expect(errors).toEqual([]); keys.forEach(key => key.lock()); await other.close();
});


test('seleccionar otra conversación muestra únicamente los mensajes correspondientes', async ({ page }) => {
  const { errors, unlock, keys, sockets, secondId } = await fixture([page], { existing: true, secondRoom: true });
  await unlock(0);
  for (const [id, text] of [[roomId, 'Mensaje de Luis'], [secondId, 'Mensaje de Marta']]) {
    sockets[0].send(JSON.stringify(frame('message.new', { message_id: crypto.randomUUID(), sender_role: 'guest',
      sent_at: new Date().toISOString(), ...keys[1].encrypt(text, keys[0].publicKey) }, id)));
  }
  const select = async name => {
    if (await page.getByRole('button', { name: 'Volver a conversaciones', exact: true }).isVisible()) {
      await page.getByRole('button', { name: 'Volver a conversaciones', exact: true }).click();
    }
    await page.getByRole('navigation', { name: 'Conversaciones', exact: true }).getByRole('button', { name: name + ' · activa' }).click();
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  };
  await select('Luis');
  await expect(page.getByLabel('Mensajes', { exact: true })).toContainText('Mensaje de Luis');
  await expect(page.getByLabel('Mensajes', { exact: true })).not.toContainText('Mensaje de Marta');
  await select('Marta');
  await expect(page.getByLabel('Mensajes', { exact: true })).toContainText('Mensaje de Marta');
  await expect(page.getByLabel('Mensajes', { exact: true })).not.toContainText('Mensaje de Luis');
  await select('Luis');
  await expect(page.getByLabel('Mensajes', { exact: true })).toContainText('Mensaje de Luis');
  expect(errors).toEqual([]); keys.forEach(key => key.lock());
});

test('la barra superior se mantiene entre cuenta, chat y ayuda sin perder claves ni mensajes', async ({ page }, testInfo) => {
  const { errors, unlock, keys, sockets, requests } = await fixture([page], { existing: true });
  await page.evaluate(() => { window.originalHeader = document.querySelector('.topbar'); });
  const header = page.locator('.topbar');
  const original = await header.boundingBox();
  await unlock(0);
  await page.getByRole('navigation', { name: 'Conversaciones', exact: true }).getByRole('button').click();
  sockets[0].send(JSON.stringify(frame('message.new', { message_id: crypto.randomUUID(), sender_role: 'guest',
    sent_at: new Date().toISOString(), ...keys[1].encrypt('Mensaje que conservamos al consultar ayuda', keys[0].publicKey) })));
  await expect(page.getByLabel('Mensajes', { exact: true })).toContainText('Mensaje que conservamos');
  await page.getByLabel('Mensaje', { exact: true }).click();
  await page.getByLabel('Mensaje', { exact: true }).fill('Borrador pendiente');
  await expect(page.getByLabel('Mensaje', { exact: true })).toHaveValue('Borrador pendiente');
  for (const id of ['nav-chat', 'nav-account', 'nav-faq']) await expect(page.locator('#' + id)).toBeVisible();
  await capture(page, testInfo.project.name, 'persistent-header');
  await page.getByRole('link', { name: 'Preguntas frecuentes', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Preguntas frecuentes', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Abrir mis claves', exact: true }).click();
  await expect(page.locator('#faq-claves')).toHaveAttribute('open', '');
  await capture(page, testInfo.project.name, 'faq-keys');
  await page.getByRole('button', { name: 'Mi cuenta', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Ajustes de usuario' })).toBeVisible();
  await page.getByRole('button', { name: 'Conversaciones', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Luis', exact: true })).toBeVisible();
  await expect(page.getByLabel('Mensajes', { exact: true })).toContainText('Mensaje que conservamos');
  await expect(page.getByLabel('Mensaje', { exact: true })).toHaveValue('Borrador pendiente');
  expect(await page.evaluate(() => window.originalHeader === document.querySelector('.topbar'))).toBe(true);
  const current = await header.boundingBox();
  expect(current.height).toBe(original.height); expect(current.width).toBe(original.width);
  if (testInfo.project.name === 'mobile') {
    await page.setViewportSize({ width: 320, height: 740 });
    const narrowHeader = await header.boundingBox();
    for (const id of ['nav-chat', 'nav-account', 'nav-faq']) {
      const bounds = await page.locator('#' + id).boundingBox();
      expect(bounds.y).toBeGreaterThanOrEqual(narrowHeader.y);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(narrowHeader.y + narrowHeader.height);
    }
  }

  expect(requests.filter(request => request.path.endsWith('/recover'))).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]); keys.forEach(key => key.lock());
});

test('la ayuda contextual permite volver al archivo y contraseña que se estaban abriendo', async ({ page }) => {
  const { errors, keys } = await fixture([page], { existing: true });
  await page.getByRole('button', { name: 'Abrir mis mensajes', exact: true }).click();
  await page.getByLabel('Contraseña de la copia', { exact: true }).fill('synthetic in-progress password');
  await page.getByRole('link', { name: 'No sé qué archivo necesito o cómo abrirlo', exact: true }).click();
  await expect(page.locator('#faq-claves')).toHaveAttribute('open', '');
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Abre tu copia de claves', exact: true })).toBeVisible();
  await expect(page.getByLabel('Contraseña de la copia', { exact: true })).toHaveValue('synthetic in-progress password');
  expect(errors).toEqual([]); keys.forEach(key => key.lock());
});
