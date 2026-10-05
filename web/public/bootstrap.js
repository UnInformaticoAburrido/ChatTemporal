import { ApiError } from './api.js';

const requestID = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
function fields(data, names) {
  return !!data && typeof data === 'object' && !Array.isArray(data)
    && Object.keys(data).length === names.length && names.every(name => Object.hasOwn(data, name));
}
function origin(value) {
  try {
    const url = new URL(value);
    return typeof value === 'string' && url.origin === value
      && (url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)));
  } catch { return false; }
}

// Un único token por apertura. Sin tokens en URL, storage, DOM o mensajes de vuelta.
export function receiveBootstrap({ origins, onToken, onError, env = globalThis, timeoutMs = 60000 }) {
  const source = env.opener;
  if (!source || source.closed || !Array.isArray(origins) || !origins.length || origins.length > 20
    || new Set(origins).size !== origins.length || !origins.every(origin)) throw new ApiError('BOOTSTRAP_UNAVAILABLE');
  const challenge = env.crypto.randomUUID();
  if (!requestID(challenge)) throw new ApiError('BOOTSTRAP_UNAVAILABLE');
  let stopped = false;
  const detach = () => { try { if (env.opener === source) env.opener = null; } catch { /* Origen protegido. */ } };
  function dispose() {
    if (stopped) return;
    stopped = true; clearTimeout(timer); env.removeEventListener('message', message); detach();
  }
  function message(event) {
    if (stopped || event.source !== source || !origins.includes(event.origin)) return;
    const data = event.data;
    if (!fields(data, ['type', 'protocol_version', 'request_id', 'bootstrap_token'])
      || data.type !== 'chat.bootstrap.token' || data.protocol_version !== 1 || data.request_id !== challenge
      || typeof data.bootstrap_token !== 'string' || !data.bootstrap_token.length || data.bootstrap_token.length > 8192) return;
    const token = data.bootstrap_token, target = event.origin;
    dispose(); // Retirar listener antes de cualquier await o exchange.
    Promise.resolve().then(() => onToken(token)).then(() => {
      source.postMessage({ type: 'chat.bootstrap.result', protocol_version: 1, request_id: challenge, status: 'accepted' }, target);
    }, () => {
      // No incluir mensajes/errores del backend ni credenciales en el resultado.
      source.postMessage({ type: 'chat.bootstrap.result', protocol_version: 1, request_id: challenge, status: 'failed' }, target);
    }).catch(() => {});
  }
  const timer = setTimeout(() => { dispose(); onError(new ApiError('BOOTSTRAP_TIMEOUT')); }, timeoutMs);
  env.addEventListener('message', message);
  try {
    for (const target of origins) source.postMessage({ type: 'chat.bootstrap.ready', protocol_version: 1, request_id: challenge }, target);
  } catch { dispose(); throw new ApiError('BOOTSTRAP_UNAVAILABLE'); }
  return dispose;
}
