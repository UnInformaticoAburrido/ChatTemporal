// La sesión vive solo en memoria. Nunca se reintenta un refresh de resultado dudoso.
export class ApiError extends Error {
  constructor(code, status = 0) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

const messages = {
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
  async #request(path, { body, method = 'GET', access } = {}) {
    let response;
    try {
      response = await this.fetcher('/api/v1' + path, {
        method, credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
        signal: AbortSignal.timeout(15000),
        headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}),
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
  resend() { return this.#authorized('/users/resend-verification', { method: 'POST' }); }
  verify(token) { return this.#request('/users/verify-email', { method: 'POST', body: { token } }); }
  async logout() {
    try { await this.#authorized('/auth/logout', { method: 'POST' }); }
    finally { this.clear(); }
  }
}
