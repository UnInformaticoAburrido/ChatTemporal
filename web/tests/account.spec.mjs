import { test, expect } from '@playwright/test';

const user = { id: '12345678-1234-4234-8234-123456789abc', nick: 'ana', email: 'ana@example.com', email_verified: true };
const phrase = Array(23).fill('abandon').concat('art').join(' ');
const tokens = { access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 };

async function account(page, { hasKey = true, clipboardFails = false } = {}) {
  const calls = [];
  let generation = 0;
  await page.addInitScript(fails => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText: async value => { if (fails) throw new Error('denied'); window.copiedCode = value; },
    } });
  }, clipboardFails);
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    calls.push({ path, method: request.method() });
    const reply = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path.endsWith('/recover')) return reply(tokens);
    if (path.endsWith('/users/me')) return reply(user);
    if (path.endsWith('/keys')) return hasKey ? reply({ public_key: 'synthetic-public-key', protocol_version: 1 })
      : reply({ error: { code: 'USER_NOT_FOUND' } }, 404);
    if (path.endsWith('/invitations/regenerate')) generation += 1;
    if (path.endsWith('/invitations/me') || path.endsWith('/invitations/regenerate')) {
      return reply({ stored_code: `stored-code-${generation}`, ephemeral_code: `ephemeral-code-${generation}` });
    }
    if (path.endsWith('/logout')) return route.fulfill({ status: 204 });
    throw new Error('Unexpected endpoint: ' + path);
  });
  await page.goto('/');
  await expect(page.locator('#session-status')).toHaveText('Sin sesión');
  await expect(page.locator('.intro')).toBeVisible();
  await page.getByRole('button', { name: 'Iniciar sesión', exact: true }).click();
  await page.getByLabel('Correo electrónico', { exact: true }).fill(user.email);
  await page.getByLabel('Palabra 1', { exact: true }).fill(phrase);
  await page.getByRole('button', { name: 'Acceder a mi cuenta', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Ajustes de usuario' })).toBeVisible();
  return calls;
}

test('sesión visible e invitaciones compartibles sin abrir la clave privada', async ({ page }) => {
  const calls = await account(page);
  await expect(page.locator('#session-status')).toHaveText('Sesión iniciada · ana');
  await expect(page.locator('#session-status')).toBeVisible();
  await expect(page.locator('.intro')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Obtener código para invitar' })).toBeVisible();
  await page.getByRole('button', { name: 'Obtener código para invitar' }).click();
  await expect(page.getByLabel('Con historial cifrado', { exact: true })).toHaveValue('stored-code-0');
  await expect(page.getByLabel('Efímera (requiere conexión simultánea)', { exact: true })).toHaveValue('ephemeral-code-0');
  await page.getByRole('button', { name: 'Copiar invitación con historial' }).click();
  await expect.poll(() => page.evaluate(() => window.copiedCode)).toBe('stored-code-0');
  await page.getByRole('button', { name: 'Copiar invitación efímera' }).click();
  await expect.poll(() => page.evaluate(() => window.copiedCode)).toBe('ephemeral-code-0');
  expect(calls.some(call => call.path.endsWith('/invitations/me'))).toBe(true);
  await expect(page.getByLabel('Contraseña de la copia', { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Cerrar sesión' }).click();
  await expect(page.locator('#session-status')).toHaveText('Sin sesión');
  await expect(page.locator('.intro')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('stored-code-0');
  await expect(page.locator('#codes')).toHaveCount(0);
});

test('sin clave publicada el botón lleva a crear una copia y no ofrece invitaciones inutilizables', async ({ page }) => {
  const calls = await account(page, { hasKey: false });
  await page.getByRole('button', { name: 'Obtener código para invitar' }).click();
  await expect(page.getByRole('button', { name: 'Crear y descargar copia cifrada' })).toBeVisible();
  await expect(page.locator('#notice')).toContainText('Primero crea y comprueba');
  expect(calls.some(call => call.path.endsWith('/invitations/me'))).toBe(false);
  await expect(page.getByRole('button', { name: 'Copiar invitación con historial' })).toHaveCount(0);
});

test('portapapeles denegado permite seleccionar el código y renovar sustituye los códigos visibles', async ({ page }) => {
  await account(page, { clipboardFails: true });
  await page.getByRole('button', { name: 'Obtener código para invitar' }).click();
  await page.getByRole('button', { name: 'Copiar invitación con historial' }).click();
  await expect(page.locator('#notice')).toContainText('Código seleccionado');
  const field = page.getByLabel('Con historial cifrado', { exact: true });
  expect(await field.evaluate(element => [element.selectionStart, element.selectionEnd])).toEqual([0, 'stored-code-0'.length]);
  page.on('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Regenerar invitación con historial' }).click();
  await expect(field).toHaveValue('stored-code-1');
  await expect(page.getByLabel('Efímera (requiere conexión simultánea)', { exact: true })).toHaveValue('ephemeral-code-1');
});
