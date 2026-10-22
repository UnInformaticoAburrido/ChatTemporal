import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { encryptTransfer, decryptTransfer, parseRequest, parseSecretCode, requestCode } from '../public/transfers.js';
import { LocalKey, binary } from '../public/keys.js';
import { QRCode, jsQR } from '../public/vendor/qr.js';

const user = '12345678-1234-4234-8234-123456789abc', id = '12345678-1234-4234-8234-123456789def';
const encoder = new TextEncoder();

test('transferencia XChaCha20-Poly1305 aleatoria, vinculada a UUID y compatible con Python', () => {
  const bytes = encoder.encode('Bundle sintético 👋');
  const first = encryptTransfer(id, bytes), next = encryptTransfer(id, bytes);
  assert.notEqual(first.encrypted_blob, next.encrypted_blob);
  assert.deepEqual(decryptTransfer(id, first.encrypted_blob, first.qr_payload), bytes);
  assert.ok(!first.encrypted_blob.includes(JSON.parse(first.qr_payload).secret));
  const result = spawnSync(process.env.CHAT_TEST_PYTHON || 'python3', [fileURLToPath(new URL('./interop.py', import.meta.url))], {
    input: JSON.stringify({ action: 'transfer', transfer_id: id, ...first }), encoding: 'utf8', timeout: 15000,
  });
  assert.equal(result.status, 0, 'Python transfer interop failed');
  const reply = JSON.parse(result.stdout);
  assert.equal(reply.clear, binary(bytes));
  assert.deepEqual(decryptTransfer(id, reply.encrypted_blob, reply.qr_payload), bytes);
});

test('QR acepta espacios de Python y rechaza duplicados, extras, versiones, UUID y Base64 ambiguos', () => {
  const bundle = encryptTransfer(id, encoder.encode('bundle'));
  const qr = JSON.parse(bundle.qr_payload);
  assert.equal(parseRequest(requestCode(id)), id); assert.equal(parseRequest(id), id);
  assert.equal(parseSecretCode(JSON.stringify(qr, null, 2), id).transfer_id, id);
  for (const invalid of [
    bundle.qr_payload.replace('"protocol_version":1', '"protocol_version":2,"protocol_version":1'),
    JSON.stringify({ ...qr, extra: true }), JSON.stringify({ ...qr, protocol_version: true }),
    JSON.stringify({ ...qr, secret: qr.secret + '=' }), JSON.stringify({ ...qr, transfer_id: crypto.randomUUID() }),
    'x'.repeat(1025), 'null', '[]',
  ]) assert.throws(() => parseSecretCode(invalid, id), { code: 'TRANSFER_INVALID' });
  assert.throws(() => decryptTransfer(id, bundle.encrypted_blob + '=', bundle.qr_payload), { code: 'TRANSFER_INVALID' });
  const changed = (bundle.encrypted_blob[0] === 'A' ? 'B' : 'A') + bundle.encrypted_blob.slice(1);
  assert.throws(() => decryptTransfer(id, changed, bundle.qr_payload), { code: 'TRANSFER_DECRYPT_FAILED' });
  const another = crypto.randomUUID();
  assert.throws(() => decryptTransfer(another, bundle.encrypted_blob, JSON.stringify({ ...qr, transfer_id: another })), { code: 'TRANSFER_DECRYPT_FAILED' });
});

test('límites del blob se comprueban antes del cifrado y privada recibida respeta cuenta y pública', () => {
  assert.throws(() => encryptTransfer(id, new Uint8Array()), { code: 'TRANSFER_INVALID' });
  assert.throws(() => encryptTransfer(id, new Uint8Array(65497)), { code: 'TRANSFER_INVALID' });
  const largest = encryptTransfer(id, new Uint8Array(65496));
  assert.equal(decryptTransfer(id, largest.encrypted_blob, largest.qr_payload).length, 65496);
  const key = LocalKey.generate(user), sealed = key.sealTransfer(id);
  const restored = LocalKey.fromTransfer(id, sealed.encrypted_blob, sealed.qr_payload, user);
  assert.equal(restored.publicKey, key.publicKey);
  assert.equal(restored.decrypt(key.encrypt('Hola', restored.publicKey)), 'Hola');
  assert.throws(() => LocalKey.fromTransfer(id, sealed.encrypted_blob, sealed.qr_payload, crypto.randomUUID()), { code: 'KEY_ACCOUNT_MISMATCH' });
  const body = JSON.parse(new TextDecoder().decode(decryptTransfer(id, sealed.encrypted_blob, sealed.qr_payload)));
  body.public_key = LocalKey.generate(user).publicKey;
  const wrong = encryptTransfer(id, encoder.encode(JSON.stringify(body)));
  assert.throws(() => LocalKey.fromTransfer(id, wrong.encrypted_blob, wrong.qr_payload, user), { code: 'KEY_MISMATCH' });
  key.lock(); restored.lock();
  assert.throws(() => key.sealTransfer(id), { code: 'KEY_LOCKED' });
});

test('QR de solicitud y secreto se generan y leen sin red ni canvas externo', () => {
  const sealed = encryptTransfer(id, encoder.encode('bundle'));
  for (const text of [requestCode(id), sealed.qr_payload]) {
    const { modules } = QRCode.create(text, { errorCorrectionLevel: 'M' });
    const scale = 5, size = (modules.size + 8) * scale;
    const pixels = new Uint8ClampedArray(size * size * 4); pixels.fill(255);
    for (let y = 0; y < modules.size; y++) for (let x = 0; x < modules.size; x++) if (modules.get(y, x)) {
      for (let dy = 0; dy < scale; dy++) for (let dx = 0; dx < scale; dx++) {
        const pos = (((y + 4) * scale + dy) * size + (x + 4) * scale + dx) * 4;
        pixels[pos] = pixels[pos + 1] = pixels[pos + 2] = 0;
      }
    }
    assert.equal(jsQR(pixels, size, size).data, text);
  }
});
