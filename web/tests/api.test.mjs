import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ChatApi, ApiError, errorMessage } from '../public/api.js';

const tokens = (version = 'first') => ({ access_token: 'access-' + version, refresh_token: 'refresh-' + version,
  token_type: 'Bearer', expires_in: 60 });
const response = (data, status = 200) => new Response(status === 204 ? null : JSON.stringify(data), { status });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

test('registro usa origen relativo y devuelve frase sin exponer tokens', async () => {
  const calls = [];
  const api = new ChatApi(async (path, options) => {
    calls.push({ path, options });
    return response({ user: { nick: 'ana' }, recovery_mnemonic: 'synthetic phrase', tokens: tokens() }, 201);
  });
  const result = await api.register('ana', 'ana@example.com');
  assert.deepEqual(result, { user: { nick: 'ana' }, recovery_mnemonic: 'synthetic phrase' });
  assert.equal(api.signedIn, true);
  assert.equal(calls[0].path, '/api/v1/users/register');
  assert.deepEqual(JSON.parse(calls[0].options.body), { nick: 'ana', email: 'ana@example.com' });
  assert.equal(calls[0].options.credentials, 'omit');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.redirect, 'error');
  assert.equal(calls[0].options.headers.Authorization, undefined);
  assert.doesNotMatch(JSON.stringify(api), /access-first|refresh-first|synthetic phrase/);
});

test('lecturas simultáneas renuevan exactamente una vez y usan el token nuevo', async () => {
  let now = 0, refreshes = 0;
  const wait = deferred();
  const authorizations = [];
  const api = new ChatApi(async (path, options) => {
    if (path.endsWith('/recover')) return response(tokens());
    if (path.endsWith('/refresh')) {
      refreshes++;
      assert.equal(JSON.parse(options.body).refresh_token, 'refresh-first');
      await wait.promise;
      return response(tokens('next'));
    }
    authorizations.push(options.headers.Authorization);
    return response({ nick: 'ana' });
  }, () => now);
  await api.recover('ana@example.com', 'phrase'); now = 40000;
  const calls = [api.me(), api.me(), api.me()];
  wait.resolve(); await Promise.all(calls);
  assert.equal(refreshes, 1);
  assert.deepEqual(authorizations, Array(3).fill('Bearer access-next'));
});

test('refresh dudoso no reintenta ni conserva un token que pudo consumirse', async () => {
  let now = 0, refreshes = 0;
  const api = new ChatApi(async path => {
    if (path.endsWith('/recover')) return response(tokens());
    refreshes++; throw Error('RAW SECRET NETWORK DATA');
  }, () => now);
  await api.recover('ana@example.com', 'phrase'); now = 40000;
  await assert.rejects(api.me(), { message: 'NETWORK_ERROR' });
  assert.equal(api.signedIn, false);
  await assert.rejects(api.me(), { message: 'SESSION_LOST' });
  assert.equal(refreshes, 1);
});

test('clear durante refresh impide que una respuesta tardía restaure la sesión', async () => {
  let now = 0;
  const wait = deferred();
  const api = new ChatApi(async path => path.endsWith('/recover') ? response(tokens()) : wait.promise, () => now);
  await api.recover('ana@example.com', 'phrase'); now = 40000;
  const call = api.me();
  api.clear(); wait.resolve(response(tokens('late')));
  await assert.rejects(call, { message: 'SESSION_LOST' });
  assert.equal(api.signedIn, false);
});

test('401 elimina la sesión sin reintentar la operación', async () => {
  let reads = 0;
  const api = new ChatApi(async path => {
    if (path.endsWith('/recover')) return response(tokens());
    reads++; return response({ error: { code: 'SESSION_REVOKED' } }, 401);
  });
  await api.recover('ana@example.com', 'phrase');
  await assert.rejects(api.me(), { message: 'SESSION_REVOKED' });
  assert.equal(api.signedIn, false); assert.equal(reads, 1);
});

test('logout borra tokens locales incluso cuando no hay confirmación del servidor', async () => {
  const api = new ChatApi(async path => {
    if (path.endsWith('/recover')) return response(tokens());
    throw Error('network');
  });
  await api.recover('ana@example.com', 'phrase');
  await assert.rejects(api.logout(), { message: 'NETWORK_ERROR' });
  assert.equal(api.signedIn, false);
});

test('verificar no envía credenciales y resend sí exige sesión', async () => {
  const seen = [];
  const api = new ChatApi(async (path, options) => {
    seen.push([path, options.headers.Authorization, options.body]);
    if (path.endsWith('/recover')) return response(tokens());
    return response(null, 204);
  });
  await api.verify('verification-code');
  await assert.rejects(api.resend(), { message: 'SESSION_LOST' });
  await api.recover('ana@example.com', 'phrase'); await api.resend();
  assert.deepEqual(seen[0], ['/api/v1/users/verify-email', undefined, '{"token":"verification-code"}']);
  assert.equal(seen.at(-1)[1], 'Bearer access-first');
});

test('errores no muestran contenido arbitrario del servidor y reconocen conflictos reales', async () => {
  const api = new ChatApi(async () => response({ error: { code: '<script>SECRET</script>', message: 'SECRET' } }, 500));
  await assert.rejects(api.register('ana', 'ana@example.com'), { message: 'REQUEST_FAILED' });
  assert.doesNotMatch(errorMessage(new ApiError('SECRET', 500)), /SECRET/);
  assert.match(errorMessage(new ApiError('EMAIL_TAKEN', 409)), /registrado/);
  assert.match(errorMessage(new ApiError('NICK_TAKEN', 409)), /en uso/);
  assert.equal(typeof errorMessage(new ApiError('constructor', 500)), 'string');
});

test('registro interrumpido no instala sesión y red caída no repite la creación', async () => {
  const wait = deferred();
  const api = new ChatApi(async () => wait.promise);
  const call = api.register('ana', 'ana@example.com'); api.clear();
  wait.resolve(response({ tokens: tokens(), user: {}, recovery_mnemonic: 'phrase' }, 201));
  await assert.rejects(call, { message: 'SESSION_LOST' });
  assert.equal(api.signedIn, false);
  let calls = 0;
  const failing = new ChatApi(async () => { calls++; throw Error('network'); });
  await assert.rejects(failing.register('ana', 'ana@example.com'), { message: 'NETWORK_ERROR' });
  assert.equal(calls, 1);
});

test('respuesta de autenticación malformada no abre sesión', async () => {
  const api = new ChatApi(async () => response({ access_token: 'bad' }));
  await assert.rejects(api.recover('ana@example.com', 'phrase'), { message: 'SESSION_LOST' });
  assert.equal(api.signedIn, false);
});

test('una petición antigua no utiliza ni revoca la sesión que la sustituye', async () => {
  let reads = 0;
  const api = new ChatApi(async path => {
    if (path.endsWith('/recover')) return response(tokens());
    reads++; return response({ error: { code: 'SESSION_REVOKED' } }, 401);
  });
  await api.recover('ana@example.com', 'phrase');
  const oldRead = api.me();
  const login = api.recover('ana@example.com', 'phrase');
  await assert.rejects(oldRead, { message: 'SESSION_LOST' });
  await login;
  assert.equal(reads, 0); assert.equal(api.signedIn, true);
});
