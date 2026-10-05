import { test } from 'node:test';
import assert from 'node:assert/strict';
import { receiveBootstrap } from '../public/bootstrap.js';
import { launchChat } from '../../integrations/main-app/launch-chat.mjs';
const mainOrigin = 'https://main.example.test', chatOrigin = 'https://chat.example.test';
const challenge = '12345678-1234-4234-8234-123456789abc';
function environment() {
  const listeners = new Map(), sent = [];
  const source = { closed: false, postMessage(data, target) { sent.push({ data, target }); } };
  const env = { opener: source, crypto: { randomUUID: () => challenge },
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    emit(type, data) { for (const fn of listeners.get(type) ?? []) fn(data); } };
  return { env, source, sent, listeners };
}
const tokenMessage = extra => ({ type: 'chat.bootstrap.token', protocol_version: 1, request_id: challenge, bootstrap_token: 'synthetic-bootstrap', ...extra });
const tick = () => new Promise(resolve => setImmediate(resolve));
test('recepción valida origen, ventana, nonce y campos; entrega única sin devolver token', async () => {
  const f = environment(), tokens = [], errors = [];
  receiveBootstrap({ origins: [mainOrigin], env: f.env, onToken: value => { tokens.push(value); }, onError: e => errors.push(e) });
  assert.equal(f.sent[0].target, mainOrigin);
  for (const event of [
    { origin: 'https://evil.test', source: f.source, data: tokenMessage() },
    { origin: mainOrigin, source: {}, data: tokenMessage() },
    { origin: mainOrigin, source: f.source, data: tokenMessage({ request_id: 'wrong' }) },
    { origin: mainOrigin, source: f.source, data: tokenMessage({ injected: 'extra' }) },
    { origin: mainOrigin, source: f.source, data: tokenMessage({ protocol_version: '1' }) },
    { origin: mainOrigin, source: f.source, data: tokenMessage({ bootstrap_token: 'x'.repeat(8193) }) },
  ]) f.env.emit('message', event);
  assert.equal(tokens.length, 0);
  f.env.emit('message', { origin: mainOrigin, source: f.source, data: tokenMessage() });
  f.env.emit('message', { origin: mainOrigin, source: f.source, data: tokenMessage() });
  await tick(); assert.deepEqual(tokens, ['synthetic-bootstrap']); assert.deepEqual(errors, []);
  assert.equal(f.env.opener, null); assert.equal(f.listeners.get('message').size, 0);
  assert.equal(f.sent.at(-1).data.status, 'accepted');
  assert.equal(JSON.stringify(f.sent).includes('synthetic-bootstrap'), false);
});
test('sin origen explícito/opener no hay canal, y cancelar invalida códigos tardíos', async () => {
  for (const origins of [[], ['*'], ['http://main.example.test'], [mainOrigin + '/path'], [mainOrigin, mainOrigin]]) {
    const f = environment(); assert.throws(() => receiveBootstrap({ origins, env: f.env }), { code: 'BOOTSTRAP_UNAVAILABLE' });
    assert.equal(f.listeners.size, 0);
  }
  const f = environment(); let calls = 0;
  const dispose = receiveBootstrap({ origins: [mainOrigin], env: f.env, onToken: () => { calls += 1; }, onError() {} });
  dispose(); f.env.emit('message', { origin: mainOrigin, source: f.source, data: tokenMessage() });
  await tick(); assert.equal(calls, 0); assert.equal(f.env.opener, null);
});
test('timeout y fallo de exchange no exponen excepciones o tokens al emisor', async () => {
  const f = environment();
  const timedOut = new Promise(resolve => receiveBootstrap({ origins: [mainOrigin], env: f.env, onToken() {}, onError: resolve, timeoutMs: 5 }));
  assert.equal((await timedOut).code, 'BOOTSTRAP_TIMEOUT'); assert.equal(f.listeners.get('message').size, 0);
  const other = environment();
  receiveBootstrap({ origins: [mainOrigin], env: other.env, onToken() { throw Error('private content'); }, onError() {} });
  other.env.emit('message', { origin: mainOrigin, source: other.source, data: tokenMessage() }); await tick();
  assert.equal(other.sent.at(-1).data.status, 'failed'); assert.equal(JSON.stringify(other.sent).includes('private'), false);
});
test('launcher abre por gesto, emite una vez solo para popup/origen correctos y espera resultado', async () => {
  const f = environment(), opened = []; let emissions = 0;
  f.env.open = (url, target) => { opened.push([url, target]); return f.source; };
  const launch = launchChat({ chatOrigin, env: f.env, createBootstrapToken: () => { emissions += 1; return 'synthetic-bootstrap'; } });
  assert.deepEqual(opened, [[chatOrigin + '/#main-app', '_blank']]); assert.equal(emissions, 0);
  const ready = { type: 'chat.bootstrap.ready', protocol_version: 1, request_id: challenge };
  f.env.emit('message', { origin: mainOrigin, source: f.source, data: ready });
  f.env.emit('message', { origin: chatOrigin, source: {}, data: ready }); await tick(); assert.equal(emissions, 0);
  f.env.emit('message', { origin: chatOrigin, source: f.source, data: ready });
  f.env.emit('message', { origin: chatOrigin, source: f.source, data: ready }); await tick();
  assert.equal(emissions, 1); assert.equal(f.sent[0].target, chatOrigin);
  assert.equal(f.sent[0].data.bootstrap_token, 'synthetic-bootstrap');
  f.env.emit('message', { origin: chatOrigin, source: f.source, data: { type: 'chat.bootstrap.result', protocol_version: 1, request_id: challenge, status: 'accepted' } });
  await launch.result; assert.equal(f.listeners.get('message').size, 0);
});
test('launcher bloqueado/cancelado falla sin emitir, y token tardío no se transmite', async () => {
  const blocked = environment(); blocked.env.open = () => null;
  const noPopup = launchChat({ chatOrigin, env: blocked.env, createBootstrapToken() { throw Error('unexpected'); } });
  await assert.rejects(noPopup.result, { code: 'CHAT_POPUP_BLOCKED' }); assert.equal(blocked.listeners.get('message').size, 0);
  const f = environment(); f.env.open = () => f.source; let finish;
  const launch = launchChat({ chatOrigin, env: f.env, createBootstrapToken: () => new Promise(resolve => { finish = resolve; }) });
  const rejected = assert.rejects(launch.result, { code: 'CHAT_LAUNCH_CANCELLED' });
  f.env.emit('message', { origin: chatOrigin, source: f.source, data: { type: 'chat.bootstrap.ready', protocol_version: 1, request_id: challenge } }); await tick();
  f.env.emit('pagehide'); finish('synthetic-bootstrap'); await rejected; await tick(); assert.equal(f.sent.length, 0);
  for (const invalid of ['*', chatOrigin + '/', 'http://main.example.test', chatOrigin + '/?token=x']) assert.throws(() => launchChat({ chatOrigin: invalid, createBootstrapToken() {} }), { code: 'CHAT_ORIGIN_INVALID' });
});
