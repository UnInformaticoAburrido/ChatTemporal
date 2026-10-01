import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { LocalKey } from '../public/keys.js';
const user = { id: '12345678-1234-4234-8234-123456789abc', nick: 'ana', email: 'ana@example.com', email_verified: true };
const password = 'synthetic backup password';
const phrase = Array(23).fill('abandon').concat('art').join(' ');
const file = text => ({ name: 'test.chatkey', mimeType: 'application/json', buffer: Buffer.from(text) });
async function setup(page, { initial = null, race = false, lostReply = false } = {}) {
  let remote = initial;
  const puts = [];
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const reply = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path.endsWith('/recover')) return reply({ access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 });
    if (path.endsWith('/users/me')) return reply(user);
    if (path.endsWith('/logout')) return route.fulfill({ status: 204 });
    if (path.endsWith('/keys') && request.method() === 'GET') return remote ? reply(remote) : reply({ error: { code: 'USER_NOT_FOUND' } }, 404);
    if (path.endsWith('/keys') && request.method() === 'PUT') {
      const body = request.postDataJSON(); puts.push(body);
      expect(Object.keys(body).sort()).toEqual(['protocol_version', 'public_key']);
      expect(request.headers()['if-none-match']).toBe('*');
      if (race) {
        const other = LocalKey.generate(user.id); remote = { public_key: other.publicKey, protocol_version: 1 }; other.lock();
        return reply({ error: { code: 'KEY_ALREADY_EXISTS' } }, 412);
      }
      remote = body;
      if (lostReply) { lostReply = false; return route.abort(); }
      return reply(remote);
    }
    throw Error('Unexpected API endpoint');
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).click();
  await page.getByLabel('Correo electrónico', { exact: true }).fill(user.email);
  await page.getByLabel('Frase de recuperación', { exact: true }).fill(phrase);
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).last().click();
  await page.getByRole('button', { name: 'Gestionar claves de conversación' }).click();
  return puts;
}
async function generate(page) {
  await page.getByLabel('Contraseña para la copia', { exact: true }).fill(password);
  await page.getByLabel('Repite la contraseña').fill(password);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Crear y descargar copia cifrada' }).click();
  const text = await readFile(await (await download).path(), 'utf8');
  await expect(page.getByRole('heading', { name: 'Comprueba la copia que has guardado' })).toBeVisible();
  return text;
}
async function restore(page, text, pass = password) {
  await page.getByLabel('Copia cifrada de claves').setInputFiles(file(text));
  await page.getByLabel('Contraseña de la copia', { exact: true }).fill(pass);
  await page.getByRole('button', { name: 'Abrir copia y comprobar clave' }).click();
}

test('solo publica después de abrir la copia, permite bloquear y nunca persiste secretos en storage', async ({ page }) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  const puts = await setup(page);
  const text = await generate(page);
  expect(puts).toHaveLength(0);
  const key = await LocalKey.restore(text, password, user.id); key.lock(); // Archivo real del navegador interoperable.
  await restore(page, text, 'wrong backup password');
  await expect(page.getByRole('status')).toContainText('No se puede abrir');
  expect(puts).toHaveLength(0);
  await restore(page, text);
  await expect(page.getByRole('heading', { name: 'Clave preparada en esta sesión' })).toBeVisible();
  expect(puts).toHaveLength(1);
  expect(puts[0].public_key).toBe(key.publicKey);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await page.getByRole('button', { name: 'Bloquear clave', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Abre tu copia de claves' })).toBeVisible();
  await restore(page, text);
  await expect(page.getByRole('heading', { name: 'Clave preparada en esta sesión' })).toBeVisible();
  expect(puts).toHaveLength(1); expect(errors).toEqual([]);
});

test('cuenta con clave exige copia coincidente y no ofrece sustitución automática', async ({ page }) => {
  const key = LocalKey.generate(user.id), wrong = LocalKey.generate(user.id);
  const text = await key.backup(password), bad = await wrong.backup(password);
  const puts = await setup(page, { initial: { public_key: key.publicKey, protocol_version: 1 } });
  await expect(page.getByRole('button', { name: 'Crear y descargar copia cifrada' })).toHaveCount(0);
  await restore(page, bad);
  await expect(page.getByRole('status')).toContainText('No se ha sustituido');
  await restore(page, text);
  await expect(page.getByRole('heading', { name: 'Clave preparada en esta sesión' })).toBeVisible();
  expect(puts).toHaveLength(0);
  await page.getByRole('button', { name: 'Cerrar sesión' }).click();
  await expect(page.getByRole('heading', { name: 'Clave preparada en esta sesión' })).toHaveCount(0);
  key.lock(); wrong.lock();
});

test('otra publicación concurrente no se sobrescribe', async ({ page }) => {
  const puts = await setup(page, { race: true });
  const text = await generate(page);
  await restore(page, text);
  await expect(page.getByRole('status')).toContainText('No se ha sustituido');
  expect(puts).toHaveLength(1);
  await expect(page.getByRole('heading', { name: 'Clave preparada en esta sesión' })).toHaveCount(0);
});

test('respuesta perdida se resuelve leyendo la pública sin repetir la publicación', async ({ page }) => {
  const puts = await setup(page, { lostReply: true });
  const text = await generate(page);
  await restore(page, text);
  await expect(page.getByRole('status')).toContainText('No hemos podido confirmar');
  await restore(page, text);
  await expect(page.getByRole('heading', { name: 'Clave preparada en esta sesión' })).toBeVisible();
  expect(puts).toHaveLength(1);
});
