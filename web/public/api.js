// La sesión vive solo en memoria. Nunca se reintenta un refresh de resultado dudoso.
export class ApiError extends Error {
  constructor(code, status = 0) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

const messages = {
  INVITATION_INVALID: "El código de invitación no es válido.",
  INVITATION_REVOKED: "Esta invitación ha caducado o se ha renovado.",
  HOST_KEY_UNAVAILABLE: "El anfitrión todavía no ha preparado sus claves.",
  CONVERSATION_NOT_FOUND: "La conversación ya no está disponible.",
  CONVERSATION_CLOSED: "La conversación está cerrada.",
  GRACE_LIMIT_REACHED: "Se ha alcanzado el límite de mensajes antes de aceptar.",
  VOTE_OPEN: "Espera a que termine la votación para enviar mensajes.",
  VOTE_EXPIRED: "La votación ha terminado.",
  VOTE_CONFLICT: "Tu voto ya está registrado y no se puede cambiar.",
  MESSAGE_NOT_FOUND: "Aún no se puede confirmar este envío.",
  MESSAGE_CORRUPTED: "No se ha podido autenticar y descifrar el mensaje.",
  MESSAGE_TOO_LONG: "El mensaje admite hasta 256 caracteres.",
  RECIPIENT_DISCONNECTED: "La otra persona no está conectada.",
  OFFER_TIMEOUT: "La otra persona no respondió a tiempo.",
  DELIVERY_TIMEOUT: "El envío ha caducado.",
  NOT_CONNECTED: "Espera a que se restablezca la conexión.",
  KEY_TRANSFER_NOT_READY: "El otro dispositivo todavía no ha enviado las claves.",
  KEY_TRANSFER_EXPIRED: "La transferencia ha caducado. Inicia una nueva.",
  KEY_TRANSFER_NOT_FOUND: "No se ha encontrado la transferencia.",
  KEY_TRANSFER_CONFLICT: "Esta transferencia ya tiene una copia.",
  PUSH_DISABLED: "Las notificaciones no están configuradas en el servidor.",

  INVALID_RECOVERY_PHRASE: 'El correo o la frase de recuperación no son correctos.',
  TOKEN_INVALID: 'El código o la sesión no son válidos. Comprueba los datos e inténtalo de nuevo.',
  SESSION_REVOKED: 'Esta sesión ha terminado. Recupera el acceso para continuar.',
  SESSION_LOST: 'No se pudo conservar la sesión. Recupera el acceso para continuar.',
  REFRESH_REUSE_DETECTED: 'Esta sesión ya no es válida. Recupera el acceso para continuar.',
  EMAIL_NOT_VERIFIED: 'Verifica tu correo antes de continuar.',
  EMAIL_TAKEN: 'Ese correo ya está registrado.',
  NICK_TAKEN: 'Ese nombre de usuario ya está en uso.',
  RATE_LIMITED: 'Has realizado demasiados intentos. Espera antes de volver a intentarlo.',
  TEMPORARY_UNAVAILABLE: 'El servicio no está disponible ahora. Inténtalo más tarde.',
  NETWORK_ERROR: 'No hemos podido confirmar la operación. Comprueba tu conexión antes de volver a intentarlo.',
  USER_NOT_FOUND: 'No se ha encontrado el recurso solicitado.',
  KEY_ALREADY_EXISTS: 'Ya hay una clave publicada. Abre su copia para continuar.',
  KEY_FILE_INVALID: 'El archivo no es una copia de claves válida de Chat Temporal.',
  KEY_PASSWORD_LENGTH: 'Usa una contraseña de entre 12 y 256 caracteres para proteger tu copia.',
  KEY_ACCOUNT_MISMATCH: 'Esta copia pertenece a otra cuenta.',
  KEY_DECRYPT_FAILED: 'No se puede abrir la copia. Comprueba la contraseña y que el archivo no esté dañado.',
  KEY_MISMATCH: 'La copia no corresponde a la clave publicada. No se ha sustituido ninguna clave.',
  KEY_LOCKED: 'La clave está bloqueada. Abre tu copia para continuar.',
};
export function errorMessage(error) {
  return (Object.hasOwn(messages, error?.code) ? messages[error.code] : null) || (error?.status === 422
    ? 'Revisa los campos del formulario.' : 'No se pudo completar la operación. Inténtalo de nuevo.');
}

export class ChatApi {
  #tokens = null;
  #expires = 0;
  #refreshing = null;
  #generation = 0;
  constructor(fetcher = globalThis.fetch.bind(globalThis), now = Date.now) {
    this.fetcher = fetcher;
    this.now = now;
  }
  get signedIn() { return this.#tokens !== null; }
  clear() {
    this.#generation += 1;
    this.#tokens = null;
    this.#expires = 0;
    this.#refreshing = null;
  }
  #install(tokens) {
    if (!tokens || typeof tokens.access_token !== 'string' || !tokens.access_token
      || typeof tokens.refresh_token !== 'string' || !tokens.refresh_token
      || !Number.isFinite(tokens.expires_in) || tokens.expires_in <= 0 || tokens.token_type !== 'Bearer') {
      throw new ApiError('SESSION_LOST');
    }
    this.#tokens = { access_token: tokens.access_token, refresh_token: tokens.refresh_token };
    this.#expires = this.now() + tokens.expires_in * 1000;
  }
  async #request(path, { body, method = 'GET', access, headers = {} } = {}) {
    let response;
    try {
      response = await this.fetcher('/api/v1' + path, {
        method, credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
        signal: AbortSignal.timeout(15000),
        headers: { ...headers, Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}),
          ...(access ? { Authorization: 'Bearer ' + access } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
    } catch { throw new ApiError('NETWORK_ERROR'); }
    if (!response.ok) {
      let code;
      try { code = (await response.json()).error?.code; } catch { /* No mostrar respuestas del proxy. */ }
      throw new ApiError(typeof code === 'string' && Object.hasOwn(messages, code) ? code : 'REQUEST_FAILED', response.status);
    }
    if (response.status === 204 || response.status === 202) return null;
    try { return await response.json(); } catch { throw new ApiError('INVALID_RESPONSE'); }
  }
  async register(nick, email) {
    const generation = this.#generation;
    const data = await this.#request('/users/register', { method: 'POST', body: { nick, email } });
    if (generation !== this.#generation) throw new ApiError('SESSION_LOST');
    this.#install(data.tokens);
    return { user: data.user, recovery_mnemonic: data.recovery_mnemonic };
  }
  async recover(email, recovery_mnemonic) {
    this.clear();
    const generation = this.#generation;
    const tokens = await this.#request('/auth/recover', { method: 'POST', body: { email, recovery_mnemonic } });
    if (generation !== this.#generation) throw new ApiError('SESSION_LOST');
    this.#install(tokens);
  }
  async #access() {
    if (!this.#tokens) throw new ApiError('SESSION_LOST');
    if (this.now() < this.#expires - 30000) return this.#tokens.access_token;
    if (!this.#refreshing) {
      const generation = this.#generation;
      const refresh_token = this.#tokens.refresh_token;
      this.#refreshing = (async () => {
        try {
          const tokens = await this.#request('/auth/refresh', { method: 'POST', body: { refresh_token } });
          if (generation !== this.#generation) throw new ApiError('SESSION_LOST');
          this.#install(tokens);
        } catch (error) {
          if (generation === this.#generation) this.clear();
          throw error;
        } finally {
          if (generation === this.#generation) this.#refreshing = null;
        }
      })();
    }
    await this.#refreshing;
    if (!this.#tokens) throw new ApiError('SESSION_LOST');
    return this.#tokens.access_token;
  }
  async #authorized(path, options = {}) {
    const generation = this.#generation;
    const access = await this.#access();
    if (generation !== this.#generation) throw new ApiError('SESSION_LOST');
    try {
      const result = await this.#request(path, { ...options, access });
      if (generation !== this.#generation) throw new ApiError('SESSION_LOST');
      return result;
    } catch (error) {
      if (error.status === 401 && generation === this.#generation) this.clear();
      throw error;
    }
  }
  me() { return this.#authorized('/users/me'); }
  async ownKey(userId) {
    try { return await this.#authorized('/users/' + encodeURIComponent(userId) + '/keys'); }
    catch (error) { if (error.status === 404 && error.code === 'USER_NOT_FOUND') return null; throw error; }
  }
  createKey(publicKey) {
    return this.#authorized('/users/me/keys', { method: 'PUT', headers: { 'If-None-Match': '*' },
      body: { public_key: publicKey, protocol_version: 1 } });
  }
  conversations(cursor = null) { return this.#authorized('/conversations' + this.#page(cursor)); }
  #page(cursor) { return '?limit=50' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''); }
  conversation(id) { return this.#authorized('/conversations/' + encodeURIComponent(id)); }
  peerKey(id) { return this.#authorized('/conversations/' + encodeURIComponent(id) + '/key'); }
  history(id, cursor = null) { return this.#authorized('/conversations/' + encodeURIComponent(id) + '/messages' + this.#page(cursor)); }
  invitations() { return this.#authorized('/invitations/me'); }
  regenerate() { return this.#authorized('/invitations/regenerate', { method: 'POST' }); }
  redeem(code) { return this.#authorized('/invitations/redeem', { method: 'POST', body: { code } }); }
  change(id, action) {
    return this.#authorized('/conversations/' + encodeURIComponent(id) + (action === 'close' ? '' : '/' + action),
      { method: action === 'close' ? 'DELETE' : 'POST' });
  }
  currentVote(id) { return this.#authorized('/conversations/' + encodeURIComponent(id) + '/vote'); }
  ballot(id, choice) { return this.#authorized('/votes/' + encodeURIComponent(id) + '/ballots', { method: 'POST', body: { choice } }); }
  status(id) { return this.#authorized('/messages/' + encodeURIComponent(id) + '/status'); }
  ticket() { return this.#authorized('/auth/ws-ticket', { method: 'POST' }); }
  createTransfer() { return this.#authorized('/key-transfers', { method: 'POST' }); }
  transfer(id, method = 'GET', encrypted_blob) {
    return this.#authorized('/key-transfers/' + encodeURIComponent(id), { method, ...(encrypted_blob ? { body: { encrypted_blob } } : {}) });
  }
  pushConfig() { return this.#authorized('/push/config'); }
  subscribe(body) { return this.#authorized('/push/subscriptions', { method: 'POST', body }); }
  unsubscribe(id) { return this.#authorized('/push/subscriptions/' + encodeURIComponent(id), { method: 'DELETE' }); }
  resend() { return this.#authorized('/users/resend-verification', { method: 'POST' }); }
  verify(token) { return this.#request('/users/verify-email', { method: 'POST', body: { token } }); }
  async logout() {
    try { await this.#authorized('/auth/logout', { method: 'POST' }); }
    finally { this.clear(); }
  }
}
