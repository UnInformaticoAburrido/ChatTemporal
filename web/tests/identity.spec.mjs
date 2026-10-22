import { test, expect } from '@playwright/test';
const mnemonic = Array(23).fill('abandon').concat('art').join(' ');
const tokens = { access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 };
const user = { nick: 'ana', email: 'ana@example.com', email_verified: false };

async function fixture(page, { registerError = null, logoutFails = false, maliciousNick = false, profileFailsOnce = false } = {}) {
  const calls = [];
  let verified = false;
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    calls.push({ path, body: request.postDataJSON(), headers: request.headers() });
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path.endsWith('/register')) {
      if (registerError) return reply({ error: { code: registerError, message: 'RAW UNTRUSTED SECRET' } }, 503);
      return reply({ tokens, user, recovery_mnemonic: mnemonic }, 201);
    }
    if (path.endsWith('/recover')) return reply(tokens);
    if (path.endsWith('/users/me')) {
      if (profileFailsOnce) { profileFailsOnce = false; return reply({ error: { code: 'TEMPORARY_UNAVAILABLE' } }, 503); }
      return reply({ ...user, nick: maliciousNick ? '<img src=x onerror=alert(1)>' : user.nick, email_verified: verified });
    }
    if (path.endsWith('/verify-email')) { verified = true; return route.fulfill({ status: 204 }); }
    if (path.endsWith('/resend-verification')) return route.fulfill({ status: 202 });
    if (path.endsWith('/logout')) return logoutFails ? route.abort() : route.fulfill({ status: 204 });
    throw Error('Unexpected endpoint ' + path);
  });
  await page.goto('/');
  return calls;
}
async function register(page) {
  await page.getByLabel('Nombre de usuario', { exact: true }).fill('ana');
  await page.getByLabel('Correo electrónico', { exact: true }).fill('ana@example.com');
  await page.getByRole('button', { name: 'Crear mi cuenta' }).click();
}

test('registro, confirmación de frase, verificación y logout', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const calls = await fixture(page);
  await register(page);
  await expect(page.getByRole('listitem')).toHaveCount(24);
  await page.getByRole('button', { name: 'Continuar a la verificación' }).click();
  await expect(page.getByRole('listitem')).toHaveCount(24); // Checkbox obligatorio.
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Continuar a la verificación' }).click();
  await expect(page.getByRole('listitem')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('abandon');
  await page.getByRole('button', { name: 'Enviar otro código' }).click();
  await expect(page.getByRole('status')).toContainText('otro código');
  await page.getByLabel('Código de verificación').fill('synthetic-code');
  await page.getByRole('button', { name: 'Verificar correo', exact: true }).click();
  await expect(page.locator('.account-state')).toContainText('Correo verificado');
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  expect(await page.context().cookies()).toEqual([]);
  expect(calls.find(call => call.path.endsWith('/verify-email')).headers.authorization).toBeUndefined();
  await page.getByRole('button', { name: 'Cerrar sesión' }).click();
  await expect(page.getByRole('heading', { name: 'Vuelve a tu cuenta' })).toBeVisible();
  expect(calls.filter(call => call.path.endsWith('/logout'))).toHaveLength(1);
  expect(errors).toEqual([]);
});

test('servicio no disponible no presenta registro ni frase ficticios', async ({ page }) => {
  const calls = await fixture(page, { registerError: 'TEMPORARY_UNAVAILABLE' });
  await register(page);
  await expect(page.getByRole('status')).toContainText('no está disponible');
  await expect(page.getByRole('listitem')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('RAW UNTRUSTED SECRET');
  expect(calls.filter(call => call.path.endsWith('/register'))).toHaveLength(1);
  await expect(page.getByRole('button', { name: 'Crear mi cuenta' })).toBeEnabled();
});

test('recuperación valida longitud, trata identidad como texto y avisa de logout dudoso', async ({ page }) => {
  const calls = await fixture(page, { logoutFails: true, maliciousNick: true });
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).click();
  await page.getByLabel('Correo electrónico', { exact: true }).fill('ana@example.com');
  await page.getByLabel('Frase de recuperación', { exact: true }).fill('too short');
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).last().click();
  await expect(page.getByRole('status')).toContainText('24 palabras');
  expect(calls).toHaveLength(0);
  await page.getByLabel('Frase de recuperación', { exact: true }).fill(mnemonic);
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).last().click();
  await expect(page.getByRole('heading', { name: 'Hola,' })).toContainText('<img src=x');
  await expect(page.locator('#screen img')).toHaveCount(0);
  await expect(page.getByLabel('Frase de recuperación', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Cerrar sesión' }).click();
  await expect(page.getByRole('status')).toContainText('No hemos podido confirmar');
  await expect(page.getByRole('heading', { name: 'Vuelve a tu cuenta' })).toBeVisible();
});

test('recarga no recupera credenciales y diseño no desborda', async ({ page }) => {
  await fixture(page); await register(page);
  await expect(page.getByRole('listitem')).toHaveCount(24);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Tu primera conversación está por venir' })).toBeVisible();
  await expect(page.getByRole('listitem')).toHaveCount(0);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});

test('perfil temporalmente caído no obliga a reenviar la frase ni repetir recuperación', async ({ page }) => {
  const calls = await fixture(page, { profileFailsOnce: true });
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).click();
  await page.getByLabel('Correo electrónico', { exact: true }).fill('ana@example.com');
  await page.getByLabel('Frase de recuperación', { exact: true }).fill(mnemonic);
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).last().click();
  await expect(page.getByRole('status')).toContainText('no está disponible');
  await expect(page.getByLabel('Frase de recuperación', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Consultar mi cuenta' }).click();
  await expect(page.getByRole('heading', { name: 'Hola, ana.' })).toBeVisible();
  expect(calls.filter(call => call.path.endsWith('/recover'))).toHaveLength(1);
});
