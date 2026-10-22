import sodium from './vendor/sodium.js';
await sodium.ready;

export class TransferError extends Error {
  constructor(code) { super(code); this.code = code; }
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function transferId(value) {
  if (typeof value !== 'string' || !uuid.test(value)) throw new TransferError('TRANSFER_INVALID');
  return value;
}
function encode(bytes) { return sodium.to_base64(bytes, sodium.base64_variants.URLSAFE_NO_PADDING); }
function decode(text, length, maximum = 65536) {
  try {
    if (typeof text !== 'string' || text.length > Math.ceil(maximum * 4 / 3)) throw Error();
    const bytes = sodium.from_base64(text, sodium.base64_variants.URLSAFE_NO_PADDING);
    if (encode(bytes) !== text || bytes.length > maximum || (length !== undefined && bytes.length !== length)) throw Error();
    return bytes;
  } catch { throw new TransferError('TRANSFER_INVALID'); }
}
// Comparación sin espacios fuera de strings: acepta JSON de Python y rechaza campos repetidos/escapes ambiguos.
export function transferObject(text, fields, maximum = 1024) {
  try {
    if (typeof text !== 'string' || text.length > maximum) throw Error();
    let compact = '', quoted = false, escaped = false;
    for (const char of text) {
      if (quoted || !/\s/.test(char)) compact += char;
      if (char === '"' && !escaped) quoted = !quoted;
      escaped = quoted && char === '\\' && !escaped;
    }
    const value = JSON.parse(text);
    if (!value || Array.isArray(value) || typeof value !== 'object' || JSON.stringify(value) !== compact
      || Object.keys(value).length !== fields.length || fields.some(field => !Object.hasOwn(value, field))) throw Error();
    return value;
  } catch { throw new TransferError('TRANSFER_INVALID'); }
}
function aad(id) {
  return new Uint8Array([...new TextEncoder().encode('chat-key-transfer-v1\0'),
    ...id.replaceAll('-', '').match(/../g).map(hex => parseInt(hex, 16))]);
}
export function requestCode(id) {
  return JSON.stringify({ format: 'chat-key-transfer-request', transfer_id: transferId(id), protocol_version: 1 });
}
export function parseRequest(text) {
  if (typeof text === 'string' && uuid.test(text.trim())) return text.trim();
  const value = transferObject(text, ['format', 'transfer_id', 'protocol_version']);
  if (value.format !== 'chat-key-transfer-request' || value.protocol_version !== 1) throw new TransferError('TRANSFER_INVALID');
  return transferId(value.transfer_id);
}
export function parseSecretCode(text, expected) {
  const value = transferObject(text, ['transfer_id', 'secret', 'protocol_version']);
  if (value.protocol_version !== 1 || transferId(value.transfer_id) !== transferId(expected)) throw new TransferError('TRANSFER_INVALID');
  const secret = decode(value.secret, 32);
  sodium.memzero(secret);
  return value;
}
export function encryptTransfer(id, bundle) {
  transferId(id);
  if (!(bundle instanceof Uint8Array) || !bundle.length || bundle.length + 40 > 65536) throw new TransferError('TRANSFER_INVALID');
  const secret = sodium.randombytes_buf(32), nonce = sodium.randombytes_buf(24);
  try {
    const cipher = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(bundle, aad(id), null, nonce, secret);
    const blob = new Uint8Array(nonce.length + cipher.length); blob.set(nonce); blob.set(cipher, nonce.length);
    return { encrypted_blob: encode(blob),
      qr_payload: JSON.stringify({ transfer_id: id, secret: encode(secret), protocol_version: 1 }) };
  } finally { sodium.memzero(secret); }
}
export function decryptTransfer(id, encryptedBlob, qr) {
  const value = parseSecretCode(qr, id), secret = decode(value.secret, 32);
  try {
    const blob = decode(encryptedBlob);
    if (blob.length <= 40) throw new TransferError('TRANSFER_INVALID');
    try { return sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(null, blob.slice(24), aad(id), blob.slice(0, 24), secret); }
    catch { throw new TransferError('TRANSFER_DECRYPT_FAILED'); }
  } finally { sodium.memzero(secret); }
}
