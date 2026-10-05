import { test, expect } from '@playwright/test';
const identifier = '12345678-1234-4234-8234-123456789abc';
const user = { id: identifier, nick: 'ana', email: 'ana@example.com', email_verified: true };
const phrase = Array(23).fill('abandon').concat('art').join(' ');
const publicKey = Buffer.from(Uint8Array.from({ length: 65 }, (_, i) => i === 0 ? 4 : i)).toString('base64url');
async function setup(page, { deny = false, lostPost = false, lostDelete = false, conflict = false } = {}) {
  const calls = []; let saved = false;
  await page.addInitScript(({ deny, publicKey }) => {
    let permission = 'default', current = null;
    const key = Uint8Array.from(atob(publicKey.replaceAll('-', '+').replaceAll('_', '/') + '='), c => c.charCodeAt(0));
    window.__pushCounts = { permission: 0, subscribe: 0, unsubscribe: 0 };
    Object.defineProperty(Notification, 'permission', { configurable: true, get: () => permission });
    Notification.requestPermission = async () => { window.__pushCounts.permission += 1; permission = deny ? 'denied' : 'granted'; return permission; };
    // Registro/activación del worker son reales; solo el proveedor Push se sustituye.
    PushManager.prototype.getSubscription = async () => current;
    PushManager.prototype.subscribe = async options => {
      window.__pushCounts.subscribe += 1;
      if (!options.userVisibleOnly) throw Error('userVisibleOnly required');
      current = { endpoint: 'https://push.example.test/device', options: { applicationServerKey: key.buffer },
        getKey: name => name === 'p256dh' ? key.buffer : new Uint8Array(16).buffer,
        async unsubscribe() { window.__pushCounts.unsubscribe += 1; current = null; return true; } };
      return current;
    };
    ServiceWorkerRegistration.prototype.getNotifications = async () => [];
  }, { deny, publicKey });
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    calls.push({ path, method, body: request.postDataJSON() });
    const reply = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (path.endsWith('/recover')) return reply({ access_token: 'synthetic-access', refresh_token: 'synthetic-refresh', token_type: 'Bearer', expires_in: 86400 });
    if (path.endsWith('/users/me')) return reply(user);
    if (path.endsWith('/push/config')) return reply({ vapid_public_key: publicKey });
    if (path.endsWith('/push/subscriptions') && method === 'GET') return reply({ items: saved ? [{ id: identifier, endpoint: 'https://push.example.test/device' }] : [] });
    if (path.endsWith('/push/subscriptions') && method === 'POST') {
      if (conflict) return reply({ error: { code: 'PUSH_SUBSCRIPTION_CONFLICT' } }, 409);
      expect(Object.keys(request.postDataJSON()).sort()).toEqual(['auth_secret', 'endpoint', 'p256dh']);
      saved = true;
      if (lostPost) { lostPost = false; return route.abort(); }
      return reply({ id: identifier }, 201);
    }
    if (path.endsWith('/push/subscriptions/' + identifier) && method === 'DELETE') {
      saved = false;
      if (lostDelete) { lostDelete = false; return route.abort(); }
      return route.fulfill({ status: 204 });
    }
    if (path.endsWith('/logout')) { saved = false; return route.fulfill({ status: 204 }); }
    throw Error(`Unexpected ${method} ${path}`);
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).click();
  await page.getByLabel('Correo electrónico', { exact: true }).fill(user.email);
  await page.getByLabel('Frase de recuperación', { exact: true }).fill(phrase);
  await page.getByRole('button', { name: 'Recuperar acceso', exact: true }).last().click();
  await page.getByRole('button', { name: 'Gestionar notificaciones' }).click();
  await expect(page.locator('#push-state')).toContainText('desactivadas');
  return calls;
}
test('activa solo por gesto, registra worker real sin caches y logout corta recepción nativa', async ({ page }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const calls = await setup(page);
  expect(await page.evaluate(() => window.__pushCounts)).toEqual({ permission: 0, subscribe: 0, unsubscribe: 0 });
  const registration = await page.evaluate(async () => {
    const r = await navigator.serviceWorker.getRegistration('/');
    return { script: r.active.scriptURL, caches: await caches.keys(), storage: [localStorage.length, sessionStorage.length] };
  });
  expect(registration.script).toContain('/service-worker.js'); expect(registration.caches).toEqual([]); expect(registration.storage).toEqual([0, 0]);
  await page.getByRole('button', { name: 'Activar notificaciones', exact: true }).click();
  await expect(page.locator('#push-state')).toContainText('activas para esta sesión');
  expect(await page.evaluate(() => window.__pushCounts)).toEqual({ permission: 1, subscribe: 1, unsubscribe: 0 });
  await page.getByRole('button', { name: 'Cerrar sesión', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Vuelve a tu cuenta' })).toBeVisible();
  expect(await page.evaluate(() => window.__pushCounts.unsubscribe)).toBe(1);
  expect(calls.filter(call => call.method === 'POST' && call.path.endsWith('/push/subscriptions'))).toHaveLength(1);
  expect(errors).toEqual([]);
});
test('respuestas perdidas no afirman alta/baja y permiten desactivar y confirmar sin segundo POST', async ({ page }) => {
  const calls = await setup(page, { lostPost: true, lostDelete: true });
  await page.getByRole('button', { name: 'Activar notificaciones', exact: true }).click();
  await expect(page.locator('#notice')).toContainText('No hemos podido confirmar');
  await expect(page.locator('#push-state')).toContainText('activa para vincularla');
  await page.getByRole('button', { name: 'Desactivar en este navegador' }).click();
  await expect(page.locator('#push-state')).toContainText('pendiente de confirmar');
  await expect(page.getByRole('button', { name: 'Activar notificaciones', exact: true })).toBeDisabled();
  expect(await page.evaluate(() => window.__pushCounts.unsubscribe)).toBe(1);
  await page.getByRole('button', { name: 'Desactivar en este navegador' }).click();
  await expect(page.locator('#push-state')).toContainText('desactivadas');
  expect(calls.filter(call => call.method === 'POST' && call.path.endsWith('/push/subscriptions'))).toHaveLength(1);
  expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(2);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
});
test('permiso rechazado no registra suscripciones ni insiste', async ({ page }) => {
  const calls = await setup(page, { deny: true });
  await page.getByRole('button', { name: 'Activar notificaciones', exact: true }).click();
  await expect(page.locator('#notice')).toContainText('No se han permitido');
  await expect(page.locator('#push-state')).toContainText('bloqueadas');
  await expect(page.getByRole('button', { name: 'Activar notificaciones', exact: true })).toBeDisabled();
  expect(await page.evaluate(() => window.__pushCounts)).toEqual({ permission: 1, subscribe: 0, unsubscribe: 0 });
  expect(calls.some(call => call.method === 'POST' && call.path.endsWith('/push/subscriptions'))).toBe(false);
});
test('conflicto de cuenta permite baja nativa sin vincular ni revocar otra cuenta', async ({ page }) => {
  const calls = await setup(page, { conflict: true });
  await page.getByRole('button', { name: 'Activar notificaciones', exact: true }).click();
  await expect(page.locator('#notice')).toContainText('otra cuenta');
  await page.getByRole('button', { name: 'Desactivar en este navegador' }).click();
  await expect(page.locator('#push-state')).toContainText('desactivadas');
  expect(calls.filter(call => call.method === 'DELETE')).toHaveLength(0);
  expect(await page.evaluate(() => window.__pushCounts.unsubscribe)).toBe(1);
});
