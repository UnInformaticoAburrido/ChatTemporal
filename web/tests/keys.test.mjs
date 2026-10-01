import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { LocalKey, binary } from '../public/keys.js';

const user = '12345678-1234-4234-8234-123456789abc';
const other = '12345678-1234-4234-8234-123456789abd';
const password = 'synthetic test password 123';

test('copia aleatoria, restauración fiel y bloqueo de operaciones', async () => {
  const key = LocalKey.generate(user);
  const first = await key.backup(password), second = await key.backup(password);
  assert.notEqual(first, second);
  assert.equal(JSON.stringify(key), '{}');
  const restored = await LocalKey.restore(first, password, user);
  assert.equal(restored.publicKey, key.publicKey);
  const message = key.encrypt('Hola 👋 á', restored.publicKey);
  assert.equal(restored.decrypt(message), 'Hola 👋 á');
  key.lock(); restored.lock();
  assert.equal(key.unlocked, false);
  assert.throws(() => key.encrypt('text', restored.publicKey), { code: 'KEY_LOCKED' });
  await assert.rejects(key.backup(password), { code: 'KEY_LOCKED' });
});

test('contraseña errónea, cuenta distinta y manipulación se rechazan', async () => {
  const key = LocalKey.generate(user), text = await key.backup(password);
  await assert.rejects(LocalKey.restore(text, 'another wrong password', user), { code: 'KEY_DECRYPT_FAILED' });
  await assert.rejects(LocalKey.restore(text, password, other), { code: 'KEY_ACCOUNT_MISMATCH' });
  for (const field of ['ciphertext', 'public_key', 'salt', 'nonce']) {
    const data = JSON.parse(text);
    data[field] = (data[field][0] === 'A' ? 'B' : 'A') + data[field].slice(1);
    await assert.rejects(LocalKey.restore(JSON.stringify(data), password, user), { code: 'KEY_DECRYPT_FAILED' });
  }
  key.lock();
});

test('esquema, versión, costes, duplicados y tamaños se validan antes de derivar', async () => {
  const key = LocalKey.generate(user), text = await key.backup(password);
  for (const patch of [{ version: 2 }, { iterations: 1 }, { iterations: 999999999 }, { extra: true }, { nonce: 'a' }]) {
    await assert.rejects(LocalKey.restore(JSON.stringify({ ...JSON.parse(text), ...patch }), password, user), { code: 'KEY_FILE_INVALID' });
  }
  await assert.rejects(LocalKey.restore(text.replace('"version":1', '"version":2,"version":1'), password, user), { code: 'KEY_FILE_INVALID' });
  await assert.rejects(LocalKey.restore('x'.repeat(4097), password, user), { code: 'KEY_FILE_INVALID' });
  await assert.rejects(key.backup('short'), { code: 'KEY_PASSWORD_LENGTH' });
  key.lock();
});

test('bloquear durante derivación impide terminar una copia pendiente', async () => {
  const key = LocalKey.generate(user);
  const pending = key.backup(password); key.lock();
  await assert.rejects(pending, { code: 'KEY_LOCKED' });
});

test('crypto_box interoperable en ambos sentidos con el cliente Python real', () => {
  const sender = LocalKey.generate(user), secret = Uint8Array.from({ length: 32 }, (_, i) => i + 1);
  const receiver = new LocalKey(other, secret), clear = 'Compatibilidad 👋 café 漢字';
  const message = sender.encrypt(clear, receiver.publicKey);
  const result = spawnSync(process.env.CHAT_TEST_PYTHON || 'python3', [fileURLToPath(new URL('./interop.py', import.meta.url))], {
    input: JSON.stringify({ private: binary(secret), sender_public: sender.publicKey, message, clear }), encoding: 'utf8',
  });
  assert.equal(result.status, 0, 'Python interop failed (install requirements-client.lock)');
  const reply = JSON.parse(result.stdout);
  assert.equal(reply.public, receiver.publicKey);
  assert.equal(sender.decrypt(reply), clear);
  assert.equal(sender.encrypt('👋'.repeat(256), receiver.publicKey).protocol_version, 1);
  assert.throws(() => sender.encrypt('👋'.repeat(257), receiver.publicKey), { code: 'MESSAGE_TOO_LONG' });
  reply.ciphertext = (reply.ciphertext[0] === 'A' ? 'B' : 'A') + reply.ciphertext.slice(1);
  assert.throws(() => sender.decrypt(reply), { code: 'MESSAGE_CORRUPTED' });
  secret.fill(0); sender.lock(); receiver.lock();
});
