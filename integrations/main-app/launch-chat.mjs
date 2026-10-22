// Copiar/servir este módulo desde main-app; no firma tokens ni conserva sesiones de chat.
export class ChatLaunchError extends Error {
  constructor(code) { super(code); this.code = code; }
}
const requestID = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
function fields(data, names) {
  return !!data && typeof data === 'object' && !Array.isArray(data)
    && Object.keys(data).length === names.length && names.every(name => Object.hasOwn(data, name));
}
export function launchChat({ chatOrigin, createBootstrapToken, env = globalThis, timeoutMs = 60000 }) {
  let url;
  try { url = new URL(chatOrigin); } catch { throw new ChatLaunchError('CHAT_ORIGIN_INVALID'); }
  if (url.origin !== chatOrigin || (url.protocol !== 'https:' && !(url.protocol === 'http:'
    && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) || typeof createBootstrapToken !== 'function') throw new ChatLaunchError('CHAT_ORIGIN_INVALID');
  let popup, request = null, stopped = false;
  let resolveResult, rejectResult;
  const result = new Promise((resolve, reject) => { resolveResult = resolve; rejectResult = reject; });
  function cleanup() { stopped = true; clearTimeout(timer); env.removeEventListener('message', message); env.removeEventListener('pagehide', cancel); }
  function fail(code) { if (stopped) return; cleanup(); rejectResult(new ChatLaunchError(code)); }
  function cancel() { fail('CHAT_LAUNCH_CANCELLED'); }
  function message(event) {
    if (stopped || event.origin !== chatOrigin || event.source !== popup) return;
    const data = event.data;
    if (!request && fields(data, ['type', 'protocol_version', 'request_id']) && data.type === 'chat.bootstrap.ready'
      && data.protocol_version === 1 && requestID(data.request_id)) {
      request = data.request_id; // Emisión única, incluso ante ready repetido o cambiado.
      Promise.resolve().then(createBootstrapToken).then(token => {
        if (stopped) return;
        if (typeof token !== 'string' || !token.length || token.length > 8192) { fail('CHAT_TOKEN_INVALID'); return; }
        try { popup.postMessage({ type: 'chat.bootstrap.token', protocol_version: 1, request_id: request, bootstrap_token: token }, chatOrigin); }
        catch { fail('CHAT_LAUNCH_FAILED'); }
      }, () => fail('CHAT_TOKEN_UNAVAILABLE')).catch(() => fail('CHAT_LAUNCH_FAILED'));
    } else if (request && fields(data, ['type', 'protocol_version', 'request_id', 'status'])
      && data.type === 'chat.bootstrap.result' && data.protocol_version === 1 && data.request_id === request
      && ['accepted', 'failed'].includes(data.status)) {
      cleanup(); data.status === 'accepted' ? resolveResult() : rejectResult(new ChatLaunchError('CHAT_EXCHANGE_FAILED'));
    }
  }
  const timer = setTimeout(() => fail('CHAT_LAUNCH_TIMEOUT'), timeoutMs);
  env.addEventListener('message', message); env.addEventListener('pagehide', cancel);
  try { popup = env.open(chatOrigin + '/#main-app', '_blank'); } catch { fail('CHAT_POPUP_BLOCKED'); }
  if (!popup) fail('CHAT_POPUP_BLOCKED');
  // window.open sucede antes de awaits para conservar el gesto. Cancelar no cierra un chat abierto.
  return { popup, result, cancel };
}
