import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { ApiError } from '../public/api.js';
import { applicationKey, WebPush, stopLocalPush } from '../public/push.js';
const identifier = '12345678-1234-4234-8234-123456789abc';
const key = Uint8Array.from({ length: 65 }, (_, i) => i === 0 ? 4 : i);
const publicKey = Buffer.from(key).toString('base64url');
function fixture({ permission = 'granted', lostPost = false, lostDelete = false } = {}) {
  let current = null, saved = null;
  const calls = [];
  const subscription = { endpoint: 'https://push.example.test/device', options: { applicationServerKey: key.buffer },
    getKey: name => name === 'p256dh' ? key.buffer : new Uint8Array(16).buffer,
    async unsubscribe() { calls.push('native-unsubscribe'); current = null; return true; } };
  const registration = { active: { scriptURL: 'https://example.test/service-worker.js' },
    pushManager: { async getSubscription() { return current; }, async subscribe(options) { calls.push(options); current = subscription; return subscription; } },
    async getNotifications() { return [{ close() { calls.push('close'); } }]; } };
  const env = { isSecureContext: true, PushManager: class {}, Notification: { permission,
    async requestPermission() { calls.push('permission'); return permission; } },
    navigator: { serviceWorker: { async register(path, options) { calls.push([path, options]); return registration; }, async getRegistration() { return registration; } } } };
  const api = { signedIn: true, async pushConfig() { return { vapid_public_key: publicKey }; },
    async pushSubscriptions() { calls.push('list'); return { items: saved ? [{ id: identifier, endpoint: subscription.endpoint }] : [] }; },
    async subscribe(body) { calls.push(body); saved = body; if (lostPost) { lostPost = false; throw new ApiError('NETWORK_ERROR'); } return { id: identifier }; },
    async unsubscribe(id) { assert.equal(id, identifier); calls.push('delete'); saved = null; if (lostDelete) { lostDelete = false; throw new ApiError('NETWORK_ERROR'); } } };
  const push = new WebPush(api, env);
  return { push, api, env, calls, subscription, registration, setCurrent(value) { current = value; } };
}
test('Push voluntario: configuración pública, claves URL canónicas y baja', async () => {
  assert.deepEqual(applicationKey(publicKey), key);
  for (const invalid of [publicKey + '=', 'bad', Buffer.from(new Uint8Array(65)).toString('base64url')]) assert.throws(() => applicationKey(invalid));
  const f = fixture(); await f.push.init();
  assert.equal(f.calls.filter(call => call === 'permission').length, 0);
  assert.equal(f.push.enabled, false);
  await f.push.enable(); assert.equal(f.push.enabled, true);
  const body = f.calls.find(call => call?.endpoint);
  assert.deepEqual(Object.keys(body).sort(), ['auth_secret', 'endpoint', 'p256dh']);
  assert.equal(body.p256dh, publicKey);
  assert.equal(f.calls.find(call => call?.userVisibleOnly).userVisibleOnly, true);
  await f.push.disable(); assert.equal(f.push.enabled, false); assert.equal(f.push.canDisable, false);
  assert.ok(f.calls.includes('delete') && f.calls.includes('native-unsubscribe'));
});
test('permiso denegado y falta de soporte no crean suscripciones', async () => {
  const f = fixture({ permission: 'denied' }); await f.push.init();
  await assert.rejects(f.push.enable(), { code: 'PUSH_PERMISSION_DENIED' });
  assert.equal(f.calls.some(call => call?.endpoint || call?.userVisibleOnly), false);
  f.env.isSecureContext = false;
  await assert.rejects(new WebPush(f.api, f.env).init(), { code: 'PUSH_UNAVAILABLE' });
});
test('POST de respuesta perdida se reconcilia para desactivar sin otra alta', async () => {
  const f = fixture({ lostPost: true }); await f.push.init();
  await assert.rejects(f.push.enable(), { code: 'NETWORK_ERROR' });
  assert.equal(f.push.enabled, false); assert.equal(f.push.canDisable, true);
  await f.push.disable(); assert.equal(f.push.canDisable, false);
  assert.equal(f.calls.filter(call => call?.endpoint).length, 1);
  assert.ok(f.calls.includes('delete'));
});
test('DELETE perdido corta recepción nativa y exige confirmar antes de activar', async () => {
  const f = fixture({ lostDelete: true }); await f.push.init(); await f.push.enable();
  await assert.rejects(f.push.disable(), { code: 'NETWORK_ERROR' });
  assert.ok(f.calls.includes('native-unsubscribe')); assert.equal(f.push.removalPending, true);
  await assert.rejects(f.push.enable(), { code: 'PUSH_PENDING' });
  await f.push.disable(); assert.equal(f.push.removalPending, false);
  assert.equal(f.calls.filter(call => call === 'delete').length, 2);
});
test('la suscripción nativa requiere alta explícita en la nueva sesión y clave VAPID coincidente', async () => {
  const f = fixture(); f.setCurrent(f.subscription); await f.push.init();
  assert.equal(f.push.enabled, false); assert.equal(f.calls.some(call => call?.endpoint), false);
  f.subscription.options.applicationServerKey = new Uint8Array(65).buffer;
  await assert.rejects(f.push.enable(), { code: 'PUSH_KEY_CHANGED' });
  assert.equal(f.calls.some(call => call?.endpoint), false);
  await f.push.disable(); assert.equal(f.push.canDisable, false);
});
test('suscripción que termina después de abandonar la pantalla se da de baja sin registrar', async () => {
  const f = fixture(); await f.push.init(); let finish;
  f.registration.pushManager.subscribe = () => new Promise(resolve => { finish = resolve; });
  const enabling = f.push.enable(); await new Promise(resolve => setImmediate(resolve));
  f.push.dispose(); finish(f.subscription);
  await assert.rejects(enabling, { code: 'SESSION_LOST' });
  assert.ok(f.calls.includes('native-unsubscribe'));
  assert.equal(f.calls.some(call => call?.endpoint), false);
});
test('logout limpia la suscripción local incluso sin abrir el panel', async () => {
  const f = fixture(); f.setCurrent(f.subscription); await stopLocalPush(f.env);
  assert.ok(f.calls.includes('native-unsubscribe') && f.calls.includes('close'));
});
test('POST tardío después de dispose no conserva una nueva suscripción nativa', async () => {
  const f = fixture(); await f.push.init(); let finish;
  f.api.subscribe = () => new Promise((_, reject) => { finish = reject; });
  const enabling = f.push.enable(); await new Promise(resolve => setImmediate(resolve));
  f.push.dispose(); finish(new ApiError('SESSION_LOST'));
  await assert.rejects(enabling, { code: 'SESSION_LOST' });
  assert.ok(f.calls.includes('native-unsubscribe'));
});

async function worker() {
  const handlers = {}, notifications = [], opens = [], focused = [];
  let windows = [];
  const self = { location: { origin: 'https://example.test' }, addEventListener(name, fn) { handlers[name] = fn; },
    skipWaiting: async () => {}, registration: { async showNotification(title, options) { notifications.push({ title, options }); } },
    clients: { async matchAll() { return windows; }, async openWindow(url) { opens.push(url); } } };
  vm.runInNewContext(await readFile(new URL('../public/service-worker.js', import.meta.url), 'utf8'), { self, URL });
  async function send(name, event) { let promise; handlers[name]({ ...event, waitUntil(value) { promise = value; } }); await promise; }
  return { handlers, notifications, opens, focused, send, setWindows(urls) { windows = urls.map(url => ({ url, async focus() { focused.push(url); } })); } };
}
test('worker ignora texto/URLs del payload, agrupa duplicados y no almacena credenciales', async () => {
  const w = await worker();
  const payload = { event_type: 'message.new', message_id: identifier, body: 'secret message', nick: 'secret nick', url: 'https://evil.invalid/' };
  await w.send('push', { data: { json: () => payload } });
  await w.send('push', { data: { json: () => payload } });
  assert.equal(w.notifications[0].options.tag, w.notifications[1].options.tag);
  assert.equal(w.notifications[0].options.renotify, false);
  assert.equal(JSON.stringify(w.notifications).includes('secret'), false);
  assert.equal(JSON.stringify(w.notifications).includes('evil'), false);
  assert.equal(w.handlers.fetch, undefined); assert.equal(w.handlers.message, undefined);
  await w.send('push', { data: { json() { throw Error('malformed'); } } });
  assert.equal(w.notifications.at(-1).options.tag, 'chat-activity');
});
test('clic enfoca el chat abierto conservando RAM, o abre raíz sin seguir enlaces recibidos', async () => {
  const w = await worker(); let closed = 0;
  const notification = { data: { url: 'https://evil.invalid/' }, close() { closed += 1; } };
  w.setWindows(['https://evil.invalid/', 'https://example.test/other', 'https://example.test/']);
  await w.send('notificationclick', { notification });
  assert.deepEqual(w.focused, ['https://example.test/']); assert.deepEqual(w.opens, []);
  w.setWindows(['https://example.test/other']); await w.send('notificationclick', { notification });
  assert.deepEqual(w.opens, ['/']); assert.equal(closed, 2);
});
