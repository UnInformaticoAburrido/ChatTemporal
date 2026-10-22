import { test, expect } from '@playwright/test';
const mainOrigin = 'http://127.0.0.1:4174';
const user = { id: '12345678-1234-4234-8234-123456789abc', nick: 'ana', email: 'ana@example.com', email_verified: true };
async function setup(context, { origins = [mainOrigin], lostExchange = false, profileFails = false, holdConfig = false, holdToken = false } = {}) {
  const calls = []; let releaseConfig, releaseToken;
  const configGate = holdConfig ? new Promise(resolve => { releaseConfig = resolve; }) : Promise.resolve();
  const tokenGate = holdToken ? new Promise(resolve => { releaseToken = resolve; }) : Promise.resolve();
  await context.route(mainOrigin + '/bootstrap', async route => {
    await tokenGate;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ bootstrap_token: 'synthetic-bootstrap' }) });
  });
  await context.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    calls.push({ path, method: request.method(), body: request.postDataJSON() });
    const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path.endsWith('/bootstrap-config')) { await configGate; return reply({ origins, protocol_version: 1 }); }
    if (path.endsWith('/auth/exchange')) {
      expect(request.postDataJSON()).toEqual({ bootstrap_token: 'synthetic-bootstrap' });
      if (lostExchange) return route.abort();
      return reply({ access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 });
    }
    if (path.endsWith('/users/me')) {
      if (profileFails) { profileFails = false; return reply({ error: { code: 'TEMPORARY_UNAVAILABLE' } }, 503); }
      return reply(user);
    }
    if (path.endsWith('/keys')) return reply({ error: { code: 'USER_NOT_FOUND' } }, 404);
    throw Error('Unexpected API ' + path);
  });
  return { calls, releaseConfig, releaseToken };
}
async function open(main) {
  await main.goto(mainOrigin);
  const popup = main.waitForEvent('popup'); await main.getByRole('button', { name: 'Abrir chat' }).click(); return popup;
}
test('acceso automático entre orígenes, sin tokens en URL/storage/DOM; consumo único', async ({ page, context }) => {
  const f = await setup(context), errors = []; page.on('pageerror', error => errors.push(error.message));
  const chat = await open(page); chat.on('pageerror', error => errors.push(error.message));
  await expect(chat.getByRole('heading', { name: 'Ajustes de usuario' })).toBeVisible();
  await expect(page.getByRole('status')).toHaveText('Acceso confirmado');
  expect(await page.evaluate(() => window.__issues)).toBe(1);
  expect(await chat.evaluate(() => window.opener === null)).toBe(true);
  expect(chat.url()).toBe('http://127.0.0.1:4173/');
  expect(await chat.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await expect(chat.locator('body')).not.toContainText('synthetic-');
  await page.evaluate(() => window.__launch.popup.postMessage({ type: 'chat.bootstrap.token', protocol_version: 1,
    request_id: window.__ready.request_id, bootstrap_token: 'attacker-bootstrap' }, 'http://127.0.0.1:4173'));
  await chat.getByRole('button', { name: 'Gestionar claves de conversación' }).click();
  await expect(chat.getByRole('button', { name: 'Recibir claves de otro dispositivo' })).toBeVisible();
  expect(f.calls.filter(call => call.path.endsWith('/auth/exchange'))).toHaveLength(1);
  expect(errors).toEqual([]);
});
test('origen sin configurar y apertura sin emisor permiten alternativa manual sin emitir token', async ({ page, context }) => {
  const f = await setup(context, { origins: [] }); const chat = await open(page);
  await expect(chat.getByLabel('Código de acceso de la aplicación principal')).toBeVisible();
  await expect(chat.locator('#notice')).toContainText('no está disponible');
  expect(await page.evaluate(() => window.__issues)).toBe(0);
  expect(f.calls.some(call => call.path.endsWith('/auth/exchange'))).toBe(false);
  await page.evaluate(() => window.__launch.cancel());
  await chat.goto('/#main-app');
  await expect(chat.getByLabel('Código de acceso de la aplicación principal')).toBeVisible();
  expect(await chat.evaluate(() => window.opener === null)).toBe(true);
});
test('cancelar durante configuración impide enganchar el canal al llegar la respuesta', async ({ page, context }) => {
  const f = await setup(context, { holdConfig: true }); const chat = await open(page);
  await chat.getByRole('button', { name: 'Cancelar acceso automático' }).click(); f.releaseConfig();
  await expect(chat.getByLabel('Código de acceso de la aplicación principal')).toBeVisible();
  await page.evaluate(() => window.__launch.cancel());
  expect(await page.evaluate(() => window.__issues)).toBe(0);
  expect(f.calls.some(call => call.path.endsWith('/auth/exchange'))).toBe(false);
  expect(await chat.evaluate(() => window.opener === null)).toBe(true);
});
test('otra ventana del origen permitido no puede entregar un token para el nonce observado', async ({ page, context }) => {
  const f = await setup(context, { holdToken: true }); const chat = await open(page);
  await expect.poll(() => page.evaluate(() => window.__issues)).toBe(1);
  const attackerPromise = page.waitForEvent('popup');
  await page.evaluate(() => window.open('/?attack=1', '_blank')); const attacker = await attackerPromise;
  await expect(attacker.getByRole('status')).toHaveText('Mensaje ajeno enviado');
  expect(f.calls.some(call => call.path.endsWith('/auth/exchange'))).toBe(false);
  f.releaseToken(); await expect(chat.getByRole('heading', { name: 'Ajustes de usuario' })).toBeVisible();
  expect(f.calls.filter(call => call.path.endsWith('/auth/exchange'))).toHaveLength(1);
});
test('exchange de respuesta perdida no reintenta ni confirma sesión y deja el formulario vacío', async ({ page, context }) => {
  const f = await setup(context, { lostExchange: true }); const chat = await open(page);
  await expect(page.getByRole('status')).toHaveText('CHAT_EXCHANGE_FAILED');
  await expect(chat.getByLabel('Código de acceso de la aplicación principal')).toHaveValue('');
  await expect(chat.locator('#notice')).toContainText('No hemos podido confirmar');
  expect(f.calls.filter(call => call.path.endsWith('/auth/exchange'))).toHaveLength(1);
  expect(f.calls.some(call => call.path.endsWith('/users/me'))).toBe(false);
});
test('perfil caído tras exchange no pide otro bootstrap ni repite emisión', async ({ page, context }) => {
  const f = await setup(context, { profileFails: true }); const chat = await open(page);
  await expect(page.getByRole('status')).toHaveText('Acceso confirmado');
  await chat.getByRole('button', { name: 'Consultar mi cuenta' }).click();
  await expect(chat.getByRole('heading', { name: 'Ajustes de usuario' })).toBeVisible();
  expect(await page.evaluate(() => window.__issues)).toBe(1);
  expect(f.calls.filter(call => call.path.endsWith('/auth/exchange'))).toHaveLength(1);
});
