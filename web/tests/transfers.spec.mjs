import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import QRCode from 'qrcode';
import { LocalKey } from '../public/keys.js';
import { requestCode } from '../public/transfers.js';

const user = { id: '12345678-1234-4234-8234-123456789abc', nick: 'ana', email: 'ana@example.com', email_verified: true };
const password = 'synthetic backup password';
const phrase = Array(23).fill('abandon').concat('art').join(' ');
const image = async text => ({ name: 'qr.png', mimeType: 'image/png', buffer: await QRCode.toBuffer(text, { width: 560, margin: 4 }) });
async function server(context, { lostUpload = false, lostDelete = false } = {}) {
  const key = LocalKey.generate(user.id), backup = await key.backup(password), calls = [], transfers = new Map();
  let replacement = false, grant = false;
  const tokenPair = access_token => ({ access_token, refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 });
  await context.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    const body = request.postDataJSON(); calls.push({ path, method, body, access: request.headers().authorization });
    const reply = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: status === 204 ? '' : JSON.stringify(data) });
    if (path.endsWith('/recover')) return reply(tokenPair('old-access'));
    if (path.endsWith('/exchange')) { replacement = true; grant = true; return reply(tokenPair('new-access')); }
    const old = request.headers().authorization === 'Bearer old-access';
    if (old && replacement && !(grant && method === 'PUT' && path.includes('/key-transfers/'))) return reply({ error: { code: 'SESSION_REVOKED' } }, 401);
    if (path.endsWith('/users/me')) return reply(user);
    if (path.endsWith('/keys')) {
      expect(method).toBe('GET'); return reply({ public_key: key.publicKey, protocol_version: 1 });
    }
    if (path.endsWith('/conversations')) return reply({ items: [], next_cursor: null });
    if (path.endsWith('/ws-ticket')) return reply({ ticket: 'synthetic-ticket' });
    if (path.endsWith('/logout')) return reply(null, 204);
    if (path.endsWith('/key-transfers')) {
      const transfer_id = crypto.randomUUID(), expires_at = new Date(Date.now() + 86400000).toISOString();
      const created = { transfer_id, expires_at, max_blob_bytes: 65536 };
      transfers.set(transfer_id, { ...created, blob: null }); return reply(created, 201);
    }
    if (path.includes('/key-transfers/')) {
      const id = path.split('/').at(-1), transfer = transfers.get(id);
      if (!transfer) return reply({ error: { code: 'KEY_TRANSFER_EXPIRED' } }, 410);
      if (method === 'PUT') {
        expect(Object.keys(body)).toEqual(['encrypted_blob']);
        if (transfer.blob) return reply({ error: { code: 'KEY_TRANSFER_CONFLICT' } }, 409);
        transfer.blob = body.encrypted_blob;
        if (lostUpload) { lostUpload = false; return route.abort(); }
        return reply(null, 204);
      }
      if (method === 'DELETE') {
        transfers.delete(id); grant = false;
        if (lostDelete) { lostDelete = false; return route.abort(); }
        return reply(null, 204);
      }
      if (!transfer.blob) return reply({ error: { code: 'KEY_TRANSFER_NOT_READY' } }, 409);
      return reply({ encrypted_blob: transfer.blob, expires_at: transfer.expires_at });
    }
    throw new Error('Unexpected synthetic endpoint: ' + path);
  });
  await context.routeWebSocket('**/ws/v1?*', socket => {
    socket.onMessage(() => {});
    socket.send(JSON.stringify({ type: 'session.ready', request_id: crypto.randomUUID(), conversation_id: null,
      timestamp: new Date().toISOString(), payload: {} }));
  });
  return { key, backup, calls, transfers };
}
async function oldDevice(page, backup) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).click();
  await page.getByLabel('Correo electrónico', { exact: true }).fill(user.email);
  await page.getByLabel('Frase de recuperación', { exact: true }).fill(phrase);
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).last().click();
  await page.getByRole('button', { name: 'Gestionar claves de conversación' }).click();
  await openBackup(page, backup);
  await expect(page.getByText('Conectado · cifrado de extremo a extremo', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Enviar claves a otro dispositivo', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Traslada tus claves al nuevo dispositivo' })).toBeVisible();
  await expect(page.locator('#chat-panel')).toHaveCount(0);
}
async function newDevice(page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Acceder desde la aplicación principal', exact: true }).click();
  await page.getByLabel('Código de acceso de la aplicación principal', { exact: true }).fill('synthetic-bootstrap');
  await page.getByRole('button', { name: 'Entrar con el código', exact: true }).click();
  await page.getByRole('button', { name: 'Gestionar claves de conversación' }).click();
  await page.getByRole('button', { name: 'Recibir claves de otro dispositivo', exact: true }).click();
  await page.getByRole('button', { name: 'Crear solicitud de claves', exact: true }).click();
  await expect(page.getByLabel('Solicitud para copiar al dispositivo anterior', { exact: true })).not.toHaveValue('');
  return page.getByLabel('Solicitud para copiar al dispositivo anterior', { exact: true }).inputValue();
}
async function openBackup(page, text) {
  await page.getByLabel('Copia cifrada de claves', { exact: true }).setInputFiles({ name: 'test.chatkey', mimeType: 'application/json', buffer: Buffer.from(text) });
  await page.getByLabel('Contraseña de la copia', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Abrir copia y comprobar clave', exact: true }).click();
}

test('dos dispositivos transfieren claves por QR local y solo borran el blob después de abrir la nueva copia', async ({ page, context }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const { key, backup, calls, transfers } = await server(context, { lostDelete: true });
  await oldDevice(page, backup);
  const destination = await context.newPage(); destination.on('pageerror', error => errors.push(error.message));
  const request = await newDevice(destination), id = JSON.parse(request).transfer_id;
  expect(request).toBe(requestCode(id));
  await page.getByLabel('Imagen del QR de solicitud', { exact: true }).setInputFiles(await image(request));
  await expect(page.getByLabel('Solicitud del nuevo dispositivo', { exact: true })).toHaveValue(request);
  await page.getByRole('button', { name: 'Cifrar y enviar claves', exact: true }).click();
  await expect(page.getByText('Copia cifrada enviada.', { exact: false })).toBeVisible();
  const secret = await page.getByLabel('Código privado para copiar manualmente', { exact: true }).inputValue();
  expect(JSON.parse(secret).transfer_id).toBe(id);
  await destination.getByLabel('Imagen del QR privado', { exact: true }).setInputFiles(await image(secret));
  await expect(destination.getByLabel('Código privado del dispositivo anterior', { exact: true })).toHaveValue(secret);
  await destination.getByRole('button', { name: 'Comprobar y recibir claves', exact: true }).click();
  await expect(destination.getByRole('heading', { name: 'Guarda una copia en este dispositivo' })).toBeVisible();
  expect(transfers.has(id)).toBe(true);
  await destination.getByLabel('Contraseña para la nueva copia', { exact: true }).fill(password);
  await destination.getByLabel('Repite la contraseña de la nueva copia', { exact: true }).fill(password);
  const download = destination.waitForEvent('download');
  await destination.getByRole('button', { name: 'Descargar copia de claves recibidas', exact: true }).click();
  const copied = await readFile(await (await download).path(), 'utf8');
  const restored = await LocalKey.restore(copied, password, user.id);
  expect(restored.publicKey).toBe(key.publicKey); restored.lock();
  expect(transfers.has(id)).toBe(true);
  await expect(destination.getByRole('heading', { name: 'Comprueba la copia de tus claves recibidas' })).toBeVisible();
  await openBackup(destination, copied);
  await expect(destination.locator('#notice')).toContainText('No hemos podido confirmar');
  expect(transfers.has(id)).toBe(false);
  await openBackup(destination, copied); // DELETE previo confirmado por 410; no requiere otra transferencia.
  await expect(destination.getByRole('heading', { name: 'Clave preparada en esta sesión' })).toBeVisible();
  expect(transfers.has(id)).toBe(false);
  const upload = calls.find(call => call.method === 'PUT');
  expect(upload.access).toBe('Bearer old-access'); expect(Object.keys(upload.body)).toEqual(['encrypted_blob']);
  expect(JSON.stringify(calls)).not.toContain(JSON.parse(secret).secret);
  expect(calls.some(call => call.path.endsWith('/refresh'))).toBe(false);
  await page.getByRole('button', { name: 'Finalizar y borrar códigos de este dispositivo', exact: true }).click();
  await expect(page.getByLabel('Código privado para copiar manualmente', { exact: true })).toHaveCount(0);
  expect(await destination.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  expect(errors).toEqual([]); key.lock();
});

test('respuesta de subida perdida se verifica en el destino sin repetir PUT ni cambiar el secreto', async ({ page, context }) => {
  const { key, backup, calls } = await server(context, { lostUpload: true });
  await oldDevice(page, backup);
  const destination = await context.newPage(), request = await newDevice(destination);
  await page.getByLabel('Solicitud del nuevo dispositivo', { exact: true }).fill(request);
  await page.getByRole('button', { name: 'Cifrar y enviar claves', exact: true }).click();
  await expect(page.getByText('Envío sin confirmar.', { exact: false })).toBeVisible();
  const secret = await page.getByLabel('Código privado para copiar manualmente', { exact: true }).inputValue();
  await destination.getByLabel('Código privado del dispositivo anterior', { exact: true }).fill(secret);
  await destination.getByRole('button', { name: 'Comprobar y recibir claves', exact: true }).click();
  await expect(destination.getByRole('heading', { name: 'Guarda una copia en este dispositivo' })).toBeVisible();
  expect(calls.filter(call => call.method === 'PUT')).toHaveLength(1);
  key.lock();
});

test('cámara denegada permite alternativa manual; cancelar elimina transferencia y códigos', async ({ page, context }) => {
  const { key, transfers } = await server(context);
  await page.addInitScript(() => { navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('synthetic denial', 'NotAllowedError'); }; });
  await newDevice(page);
  await page.getByRole('button', { name: 'Leer QR privado con cámara', exact: true }).click();
  await expect(page.locator('#notice')).toContainText('No se pudo abrir la cámara');
  await expect(page.getByLabel('Código privado del dispositivo anterior', { exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Cancelar recepción', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Abre tu copia de claves' })).toBeVisible();
  expect(transfers.size).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  key.lock();
});

test('permiso de cámara tardío tras pagehide se cierra y no restaura secretos', async ({ page, context }) => {
  const { key } = await server(context);
  await page.addInitScript(() => {
    window.stoppedTracks = 0;
    navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { window.finishCamera = () => resolve({ getTracks: () => [{ stop: () => window.stoppedTracks++ }] }); });
  });
  await newDevice(page);
  await page.getByRole('button', { name: 'Leer QR privado con cámara', exact: true }).click();
  await expect.poll(() => page.evaluate(() => typeof window.finishCamera)).toBe('function');
  await page.evaluate(() => { dispatchEvent(new Event('pagehide')); window.finishCamera(); });
  await expect.poll(() => page.evaluate(() => window.stoppedTracks)).toBe(1);
  await expect(page.locator('#transfer-panel')).toHaveCount(0);
  key.lock();
});
