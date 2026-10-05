import { ApiError } from './api.js';

function encode(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}
export function applicationKey(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{87}$/.test(value)) throw new ApiError('PUSH_DISABLED');
  const bytes = Uint8Array.from(atob(value.replaceAll('-', '+').replaceAll('_', '/') + '='), c => c.charCodeAt(0));
  if (bytes.length !== 65 || bytes[0] !== 4 || encode(bytes) !== value) throw new ApiError('PUSH_DISABLED');
  return bytes;
}
function id(value) {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)) throw new ApiError('PUSH_PENDING');
  return value;
}
async function activeRegistration(workers) {
  const registration = await workers.register('/service-worker.js', { scope: '/', updateViaCache: 'none' });
  if (!registration.active) await new Promise((resolve, reject) => {
    const worker = registration.installing || registration.waiting;
    if (!worker) { reject(new ApiError('PUSH_UNAVAILABLE')); return; }
    const timer = setTimeout(() => finish(new ApiError('PUSH_UNAVAILABLE')), 15000);
    function finish(error) {
      clearTimeout(timer); worker.removeEventListener('statechange', changed);
      error ? reject(error) : resolve();
    }
    function changed() {
      if (worker.state === 'activated') finish();
      else if (worker.state === 'redundant') finish(new ApiError('PUSH_UNAVAILABLE'));
    }
    worker.addEventListener('statechange', changed); changed();
  });
  return registration;
}

// El navegador conserva la suscripción nativa. Tokens, relación de sesión e ID solo viven en RAM.
export class WebPush {
  constructor(api, env = globalThis) {
    this.api = api; this.env = env; this.registration = null; this.subscription = null;
    this.identifier = null; this.key = null; this.stopped = false; this.removalPending = false;
  }
  check() { if (this.stopped || !this.api.signedIn) throw new ApiError('SESSION_LOST'); }
  get supported() { return !!(this.env.isSecureContext && this.env.navigator?.serviceWorker && this.env.PushManager && this.env.Notification); }
  get enabled() { return !!(this.identifier && this.subscription && !this.removalPending); }
  get canDisable() { return !!(this.subscription || this.identifier || this.removalPending); }
  get permission() { return this.env.Notification?.permission; }
  async init() {
    this.check();
    if (!this.supported) throw new ApiError('PUSH_UNAVAILABLE');
    const config = await this.api.pushConfig(); this.check();
    this.key = applicationKey(config.vapid_public_key);
    const registration = await activeRegistration(this.env.navigator.serviceWorker); this.check();
    this.registration = registration;
    this.subscription = await registration.pushManager.getSubscription(); this.check();
    await this.reconcile();
  }
  async reconcile() {
    const result = await this.api.pushSubscriptions(); this.check();
    if (!Array.isArray(result?.items)) throw new ApiError('PUSH_PENDING');
    this.identifier = this.subscription ? (result.items.find(item => item.endpoint === this.subscription.endpoint)?.id ?? null) : null;
    if (this.identifier) this.identifier = id(this.identifier);
  }
  async enable() {
    this.check();
    if (!this.registration || !this.key || this.removalPending) throw new ApiError('PUSH_PENDING');
    // Primera llamada del gesto de usuario: no esperar a red antes de pedir permiso.
    if (this.permission !== 'granted') {
      const permission = await this.env.Notification.requestPermission(); this.check();
      if (permission !== 'granted') throw new ApiError('PUSH_PERMISSION_DENIED');
    }
    const existing = await this.registration.pushManager.getSubscription(); this.check();
    if (existing) {
      const oldKey = existing.options?.applicationServerKey;
      if (!oldKey || encode(oldKey) !== encode(this.key)) throw new ApiError('PUSH_KEY_CHANGED');
    }
    let subscription;
    try {
      subscription = existing || await this.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: this.key });
    } catch (error) {
      throw new ApiError(error.name === 'NotAllowedError' ? 'PUSH_PERMISSION_DENIED' : 'PUSH_UNAVAILABLE');
    }
    if (this.stopped || !this.api.signedIn) {
      if (!existing) await subscription.unsubscribe();
      throw new ApiError('SESSION_LOST');
    }
    this.subscription = subscription;
    const publicKey = subscription.getKey('p256dh'), auth = subscription.getKey('auth');
    if (!publicKey || !auth) throw new ApiError('PUSH_UNAVAILABLE');
    let result;
    try { result = await this.api.subscribe({ endpoint: subscription.endpoint, p256dh: encode(publicKey), auth_secret: encode(auth) }); }
    catch (error) {
      if ((this.stopped || !this.api.signedIn) && !existing) await subscription.unsubscribe();
      throw error;
    }
    // La sesión pudo terminar mientras el servidor confirmaba el alta.
    if (this.stopped || !this.api.signedIn) { if (!existing) await subscription.unsubscribe(); throw new ApiError('SESSION_LOST'); }
    this.identifier = id(result.id);
  }
  async disable() {
    this.check();
    this.removalPending = true;
    let remoteError = null, localError = null;
    try {
      if (!this.identifier) await this.reconcile(); // Resuelve un POST cuya respuesta se perdió.
      if (this.identifier) {
        try { await this.api.unsubscribe(this.identifier); }
        catch (error) { if (error.code !== 'PUSH_SUBSCRIPTION_NOT_FOUND') throw error; }
        this.check(); this.identifier = null;
      }
    } catch (error) { remoteError = error; }
    // Incluso sin red, intentar cortar la recepción nativa del navegador.
    try {
      const current = await this.registration.pushManager.getSubscription();
      const subscription = current || this.subscription;
      if (subscription) {
        const removed = await subscription.unsubscribe();
        if (!removed && await this.registration.pushManager.getSubscription()) throw new ApiError('PUSH_PENDING');
      }
    } catch (error) { localError = error; }
    this.check();
    if (remoteError || localError) throw remoteError || new ApiError('PUSH_PENDING');
    this.subscription = null; this.removalPending = false;
    await closeNotifications(this.registration);
  }
  dispose() { this.stopped = true; this.identifier = null; this.key = null; this.subscription = null; }
}

async function closeNotifications(registration) {
  try { (await registration.getNotifications()).forEach(notification => notification.close()); } catch { /* Limpieza visual opcional. */ }
}
export async function stopLocalPush(env = globalThis) {
  if (!env.navigator?.serviceWorker) return;
  const registration = await env.navigator.serviceWorker.getRegistration('/');
  if (!registration || !registration.active?.scriptURL?.endsWith('/service-worker.js')) return;
  const subscription = await registration.pushManager.getSubscription();
  if (subscription && !await subscription.unsubscribe() && await registration.pushManager.getSubscription()) throw new ApiError('PUSH_PENDING');
  await closeNotifications(registration);
}
