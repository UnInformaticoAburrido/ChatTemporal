import { test, expect, devices } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { LocalKey } from '../public/keys.js';
const controlHeaders = { 'X-Chat-Test-Control': process.env.CHAT_E2E_CONTROL };
const mainOrigin = process.env.CHAT_E2E_MAIN_URL;
const password = 'synthetic e2e backup password';
const file = text => ({ name: 'e2e.chatkey', mimeType: 'application/json', buffer: Buffer.from(text) });
const pathOf = response => new URL(response.url()).pathname;
async function control(page, path, data) {
  const response = await page.context().request.post('/__test__/' + path, { headers: controlHeaders, data });
  expect(response.status()).toBe(path === 'disconnect' ? 204 : 200);
  return path === 'disconnect' ? null : response.json();
}
async function restore(page, text) {
  await page.getByLabel('Copia cifrada de claves', { exact: true }).setInputFiles(file(text));
  await page.getByLabel('Contraseña de la copia', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Abrir copia y comprobar clave', exact: true }).click();
  await expect(page.locator('#chat-panel')).toBeVisible();
  await expect(page.locator('#connection')).toContainText('Conectado');
}
const registeredAccounts = new WeakMap();
async function registerKey(page) {
  const nick = 'e2e_' + randomUUID().replaceAll('-', '').slice(0, 12), email = nick + '@example.com';
  await page.goto('/');
  await page.getByRole('button', { name: 'Crear cuenta', exact: true }).click();
  await page.getByLabel('Nombre de usuario', { exact: true }).fill(nick);
  await page.getByLabel('Correo electrónico', { exact: true }).fill(email);
  const registered = page.waitForResponse(response => pathOf(response).endsWith('/users/register'));
  await page.getByRole('button', { name: 'Crear mi cuenta' }).click();
  const result = await (await registered).json();
  await expect(page.getByRole('listitem')).toHaveCount(24);
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Continuar a la verificación' }).click();
  const mail = await control(page, 'mail', { email });
  await page.getByLabel('Código de verificación').fill(mail.token);
  await page.getByRole('button', { name: 'Verificar correo', exact: true }).click();
  await expect(page.locator('.account-state')).toContainText('Correo verificado');
  await page.getByRole('button', { name: 'Gestionar claves de conversación' }).click();
  await page.getByLabel('Contraseña para la copia', { exact: true }).fill(password);
  await page.getByLabel('Repite la contraseña', { exact: true }).fill(password);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Crear y descargar copia cifrada' }).click();
  const backup = await readFile(await (await download).path(), 'utf8');
  await restore(page, backup);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  const account = { id: result.user.id, access: result.tokens.access_token, backup, nick };
  registeredAccounts.set(page, account); return account;
}
async function pair(host, guest, mode) {
  await host.getByRole('button', { name: 'Mi cuenta', exact: true }).click();
  await host.getByRole('button', { name: 'Obtener código para invitar' }).click();
  const code = await host.getByLabel(mode === 'stored' ? 'Con historial cifrado' : 'Efímera (requiere conexión simultánea)', { exact: true }).inputValue();
  await host.getByRole('button', { name: 'Conversaciones', exact: true }).click();
  let room;
  if (mode === 'stored') {
    // Compatibilidad del modo histórico existente: el flujo nuevo de UI exige efímero.
    const response = await guest.request.post('/api/v1/invitations/redeem', {
      headers: { Authorization: `Bearer ${registeredAccounts.get(guest).access}` }, data: { code },
    });
    expect(response.status()).toBe(201); room = await response.json();
    await guest.getByRole('button', { name: 'Actualizar conversaciones' }).click();
    await guest.locator('#rooms button').first().click();
  } else {
    await guest.getByRole('button', { name: 'Nueva conversación', exact: true }).click();
    await guest.getByLabel('Código de invitación', { exact: true }).fill(code);
    const redeemed = guest.waitForResponse(response => pathOf(response).endsWith('/invitations/redeem'));
    await guest.getByRole('button', { name: 'Iniciar conversación' }).click();
    room = await (await redeemed).json();
  }
  await host.getByRole('button', { name: 'Actualizar conversaciones' }).click();
  await host.locator('#rooms button').first().click();
  await expect(host.getByRole('heading', { name: 'Conversación anónima', exact: true })).toBeVisible();
  await expect(guest.getByRole('heading', { name: 'Conversación anónima', exact: true })).toBeVisible();
  return room.id;
}
async function send(page, text) {
  await expect(page.getByLabel('Mensaje', { exact: true })).toBeEnabled();
  await page.getByLabel('Mensaje', { exact: true }).fill(text);
  await page.getByRole('button', { name: 'Enviar mensaje', exact: true }).click();
}
async function keepGrace(host, guest, keep) {
  await host.getByRole('button', { name: 'Aceptar conversación' }).click();
  await expect(host.getByRole('button', { name: 'Enviar mensaje', exact: true })).toBeDisabled();
  await expect(guest.getByRole('button', { name: 'Enviar mensaje', exact: true })).toBeDisabled();
  async function ballot(page, name) {
    const response = page.waitForResponse(reply => pathOf(reply).endsWith('/ballots') && reply.request().method() === 'POST');
    await page.getByRole('button', { name, exact: true }).click();
    expect((await response).status()).toBe(200);
  }
  await ballot(host, keep ? 'Conservar' : 'Eliminar');
  if (keep) await ballot(guest, 'Conservar');
  // Esperar el plazo normativo permite comprobar la resolución y las cuotas reales.
  for (const page of [host, guest]) {
    await expect(page.locator('#vote')).toContainText(keep ? 'se han conservado los mensajes anteriores a la aceptación' : 'se han eliminado los mensajes anteriores a la aceptación', { timeout: 35000 });
    await expect(page.getByLabel('Mensaje', { exact: true })).toBeEnabled();
  }
}
function observe(page) {
  const errors = [], requests = [], frames = []; let tickets = 0;
  page.on('pageerror', error => errors.push(error.name));
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/ws-ticket')) tickets += 1;
    if (path.startsWith('/api/v1/')) requests.push(request.postData() || '');
  });
  page.on('websocket', socket => socket.on('framesent', event => frames.push(event.payload.toString())));
  return { errors, requests, frames, tickets: () => tickets };
}

test('stored real: identidad, claves, gracia aprobada, cifrado, ACK, reconexión, historial y TTL', async ({ page, context }) => {
  const host = page, guest = await context.newPage();
  const a = observe(host), b = observe(guest);
  const account = await registerKey(host), other = await registerKey(guest);
  const id = await pair(host, guest, 'stored'), text = 'Mensaje privado E2E almacenado 👋';
  await send(guest, text);
  await expect(host.locator('#messages')).toContainText(text);
  await expect(guest.locator('#messages')).toContainText('entregado');
  await keepGrace(host, guest, true);
  await expect.poll(() => control(host, 'conversation', { id, markers: [text] })).toMatchObject({ stored: 1, deliveries: ['delivered'], plaintext_found: false });
  expect(a.requests.concat(b.requests, a.frames, b.frames).some(value => value.includes(text))).toBe(false);
  const tickets = a.tickets();
  await control(host, 'disconnect');
  await expect.poll(a.tickets).toBeGreaterThan(tickets);
  await expect(host.locator('#connection')).toContainText('Conectado');
  await host.getByRole('button', { name: 'Mi cuenta', exact: true }).click();
  await host.getByRole('button', { name: 'Bloquear clave', exact: true }).click();
  await expect(host.locator('#messages')).toHaveCount(0);
  await restore(host, account.backup);
  await host.locator('#rooms button').first().click();
  await expect(host.locator('#messages')).toContainText(text);
  await control(host, 'conversation', { id, expire_content: true });
  await expect.poll(async () => (await control(host, 'conversation', { id })).stored).toBe(0);
  // El plazo se adelantó en BD: reabrir elimina la vista que conocía el TTL original.
  for (const [p, backup] of [[host, account.backup], [guest, other.backup]]) {
    await p.getByRole('button', { name: 'Mi cuenta', exact: true }).click();
    await p.getByRole('button', { name: 'Bloquear clave', exact: true }).click();
    await restore(p, backup); await p.locator('#rooms button').first().click();
  }
  await expect(host.locator('#messages')).not.toContainText(text);
  await expect(guest.locator('#messages')).not.toContainText(text);
  expect(a.errors.concat(b.errors)).toEqual([]);
});

test('ephemeral real: offer/ready, sin ciphertext durable, rechazo de gracia y upgrade irreversible', async ({ page, context }) => {
  const host = page, guest = await context.newPage(); const a = observe(host), b = observe(guest);
  await registerKey(host); await registerKey(guest);
  const id = await pair(host, guest, 'ephemeral'), text = 'Gracia E2E efímera';
  await send(guest, text);
  await expect(host.locator('#messages')).toContainText(text);
  await expect(guest.locator('#messages')).toContainText('entregado');
  await expect.poll(() => control(host, 'conversation', { id })).toMatchObject({ mode: 'ephemeral', stored: 0, events: 1, deliveries: ['delivered'] });
  await keepGrace(host, guest, false);
  for (const p of [host, guest]) await expect(p.locator('#messages')).not.toContainText(text);
  host.on('dialog', dialog => dialog.accept());
  await host.getByLabel('Opciones de conversación', { exact: true }).click();
  await host.getByRole('button', { name: 'Guardar mensajes futuros' }).click();
  await expect(guest.locator('#room-state')).toContainText('Historial cifrado en el servidor');
  const future = 'Mensaje E2E después del upgrade'; await send(guest, future);
  await expect(host.locator('#messages')).toContainText(future);
  await expect.poll(() => control(host, 'conversation', { id, markers: [future] })).toMatchObject({ mode: 'stored', stored: 1, plaintext_found: false });
  await expect(host.getByRole('button', { name: 'Guardar mensajes futuros' })).toHaveCount(0);
  expect(a.errors.concat(b.errors)).toEqual([]);
});

test('dos dispositivos reales: bootstrap firmado, permiso residual, transferencia y borrado tras restaurar', async ({ page, browser }) => {
  const source = page, a = observe(source); const account = await registerKey(source);
  await source.getByRole('button', { name: 'Mi cuenta', exact: true }).click();
  await source.getByRole('button', { name: 'Enviar claves a otro dispositivo' }).click();
  const targetContext = await browser.newContext(test.info().project.name === 'mobile' ? devices['Pixel 7'] : devices['Desktop Chrome']);
  try {
    const main = await targetContext.newPage();
    const bound = await targetContext.request.post(mainOrigin + '/__test__/bind', { headers: controlHeaders, data: { user: account.id } });
    expect(bound.status()).toBe(204);
    await main.goto(mainOrigin);
    const popup = main.waitForEvent('popup'); await main.getByRole('button', { name: 'Abrir chat' }).click(); const target = await popup;
    const b = observe(target);
    await expect(main.getByRole('status')).toHaveText('Acceso confirmado');
    await expect(target.getByRole('heading', { name: 'Ajustes de usuario' })).toBeVisible();
    expect(target.url()).toBe(process.env.CHAT_E2E_BASE_URL + '/');
    expect(await target.evaluate(() => window.opener === null)).toBe(true);
    const revoked = await source.context().request.get('/api/v1/users/me', { headers: { Authorization: 'Bearer ' + account.access } });
    expect(revoked.status()).toBe(401);
    await target.getByRole('button', { name: 'Gestionar claves de conversación' }).click();
    await target.getByRole('button', { name: 'Recibir claves de otro dispositivo' }).click();
    await target.getByRole('button', { name: 'Crear solicitud de claves' }).click();
    await expect(target.getByLabel('Solicitud para copiar al dispositivo anterior')).toHaveValue(/\S+/);
    const request = await target.getByLabel('Solicitud para copiar al dispositivo anterior').inputValue();
    const id = JSON.parse(request).transfer_id;
    await source.getByLabel('Solicitud del nuevo dispositivo', { exact: true }).fill(request);
    await source.getByRole('button', { name: 'Cifrar y enviar claves' }).click();
    await expect(source.getByText('Copia cifrada enviada.', { exact: false })).toBeVisible();
    const code = await source.getByLabel('Código privado para copiar manualmente').inputValue();
    const secret = JSON.parse(code).secret;
    await target.getByLabel('Código privado del dispositivo anterior', { exact: true }).fill(code);
    await target.getByRole('button', { name: 'Comprobar y recibir claves' }).click();
    await expect(target.getByRole('heading', { name: 'Guarda una copia en este dispositivo' })).toBeVisible();
    expect(await control(source, 'transfer', { id, user: account.id })).toMatchObject({ blob: true, meta: true, active_sessions: 1 });
    await target.getByLabel('Contraseña para la nueva copia', { exact: true }).fill(password);
    await target.getByLabel('Repite la contraseña de la nueva copia', { exact: true }).fill(password);
    const download = target.waitForEvent('download');
    await target.getByRole('button', { name: 'Descargar copia de claves recibidas' }).click();
    const received = await readFile(await (await download).path(), 'utf8');
    const original = await LocalKey.restore(account.backup, password, account.id), restored = await LocalKey.restore(received, password, account.id);
    try { expect(restored.publicKey).toBe(original.publicKey); } finally { original.lock(); restored.lock(); }
    expect((await control(source, 'transfer', { id, user: account.id })).blob).toBe(true);
    await restore(target, received);
    expect(await control(source, 'transfer', { id, user: account.id })).toMatchObject({ blob: false, meta: false, active_sessions: 1 });
    expect(a.requests.concat(b.requests).some(value => value.includes(secret))).toBe(false);
    await source.getByRole('button', { name: 'Finalizar y borrar códigos de este dispositivo' }).click();
    await expect(source.getByLabel('Código privado para copiar manualmente')).toHaveCount(0);
    expect(await target.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
    expect(a.errors.concat(b.errors)).toEqual([]);
  } finally { await targetContext.close(); }
});
