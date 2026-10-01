import sodium from './vendor/sodium.js';
await sodium.ready;

const encoder = new TextEncoder();
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export class KeyError extends Error {
  constructor(code) { super(code); this.code = code; }
}
export function binary(bytes) { return sodium.to_base64(bytes, sodium.base64_variants.URLSAFE_NO_PADDING); }
function decode(text, length) {
  try {
    if (typeof text !== 'string' || text.length > 6000) throw Error();
    const bytes = sodium.from_base64(text, sodium.base64_variants.URLSAFE_NO_PADDING);
    if ((length !== undefined && bytes.length !== length) || binary(bytes) !== text) throw Error();
    return bytes;
  } catch { throw new KeyError('KEY_FILE_INVALID'); }
}
function account(value) {
  if (typeof value !== 'string' || !uuid.test(value)) throw new KeyError('KEY_FILE_INVALID');
}
async function derive(password, salt, usage) {
  if (typeof password !== 'string' || [...password].length < 12 || [...password].length > 256) {
    throw new KeyError('KEY_PASSWORD_LENGTH');
  }
  const bytes = encoder.encode(password);
  try {
    const material = await crypto.subtle.importKey('raw', bytes, 'PBKDF2', false, ['deriveKey']);
    return await crypto.subtle.deriveKey({ name: 'PBKDF2', hash: 'SHA-256', iterations: 600000, salt },
      material, { name: 'AES-GCM', length: 256 }, false, [usage]);
  } finally { sodium.memzero(bytes); }
}

export class LocalKey {
  #secret;
  #public;
  #user;
  constructor(user, secret) {
    account(user);
    if (!(secret instanceof Uint8Array) || secret.length !== 32) throw new KeyError('KEY_FILE_INVALID');
    this.#user = user;
    this.#secret = secret.slice();
    this.#public = binary(sodium.crypto_scalarmult_base(this.#secret));
  }
  static generate(user) {
    const pair = sodium.crypto_box_keypair();
    try { return new LocalKey(user, pair.privateKey); }
    finally { sodium.memzero(pair.privateKey); }
  }
  get publicKey() { return this.#public; }
  get unlocked() { return this.#secret !== null; }
  lock() { if (this.#secret) sodium.memzero(this.#secret); this.#secret = null; }
  #check() { if (!this.#secret) throw new KeyError('KEY_LOCKED'); }
  async backup(password) {
    this.#check();
    const header = { format: 'chat-key-backup', version: 1, user_id: this.#user, public_key: this.#public,
      cipher: 'AES-256-GCM', kdf: 'PBKDF2-SHA256', iterations: 600000,
      salt: binary(sodium.randombytes_buf(16)), nonce: binary(sodium.randombytes_buf(12)) };
    const key = await derive(password, decode(header.salt, 16), 'encrypt');
    this.#check();
    const sealed = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: decode(header.nonce, 12),
      additionalData: encoder.encode(JSON.stringify(header)), tagLength: 128 }, key, this.#secret);
    this.#check();
    return JSON.stringify({ ...header, ciphertext: binary(new Uint8Array(sealed)) });
  }
  static async restore(text, password, user) {
    account(user);
    let data, header;
    try {
      if (typeof text !== 'string' || text.length > 4096) throw Error();
      data = JSON.parse(text);
      const { format, version, user_id, public_key, cipher, kdf, iterations, salt, nonce, ciphertext } = data;
      header = { format, version, user_id, public_key, cipher, kdf, iterations, salt, nonce };
      if (JSON.stringify({ ...header, ciphertext }) !== text.trim() || format !== 'chat-key-backup' || version !== 1
        || cipher !== 'AES-256-GCM' || kdf !== 'PBKDF2-SHA256' || iterations !== 600000) throw Error();
      account(user_id); decode(public_key, 32); decode(salt, 16); decode(nonce, 12); decode(ciphertext, 48);
    } catch { throw new KeyError('KEY_FILE_INVALID'); }
    if (header.user_id !== user) throw new KeyError('KEY_ACCOUNT_MISMATCH');
    const key = await derive(password, decode(header.salt, 16), 'decrypt');
    let secret;
    try {
      secret = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: decode(header.nonce, 12),
        additionalData: encoder.encode(JSON.stringify(header)), tagLength: 128 }, key, decode(data.ciphertext, 48)));
      const restored = new LocalKey(user, secret);
      if (restored.publicKey !== header.public_key) { restored.lock(); throw Error(); }
      return restored;
    } catch { throw new KeyError('KEY_DECRYPT_FAILED'); }
    finally { if (secret) sodium.memzero(secret); }
  }
  encrypt(text, publicKey) {
    this.#check();
    if (typeof text !== 'string' || [...text].length > 256) throw new KeyError('MESSAGE_TOO_LONG');
    const nonce = sodium.randombytes_buf(24);
    const meta = new Uint8Array(56); meta.set(nonce); meta.set(decode(this.#public, 32), 24);
    return { protocol_version: 1, crypto_meta: binary(meta),
      ciphertext: binary(sodium.crypto_box_easy(encoder.encode(text), nonce, decode(publicKey, 32), this.#secret)) };
  }
  decrypt(message, ownRecipient = null) {
    this.#check();
    if (message?.protocol_version !== 1) throw new KeyError('KEY_FILE_INVALID');
    const meta = decode(message.crypto_meta, 56), ciphertext = decode(message.ciphertext);
    if (ciphertext.length < 16 || ciphertext.length > 4096) throw new KeyError('KEY_FILE_INVALID');
    let plain;
    try {
      plain = sodium.crypto_box_open_easy(ciphertext, meta.slice(0, 24), ownRecipient ? decode(ownRecipient, 32) : meta.slice(24), this.#secret);
      return new TextDecoder('utf-8', { fatal: true }).decode(plain);
    } catch { throw new KeyError('MESSAGE_CORRUPTED'); }
    finally { if (plain) sodium.memzero(plain); }
  }
}
