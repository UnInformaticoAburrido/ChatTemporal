-- §24.3: historial persistente necesario para detectar refresh reuse tras reinicios.
CREATE TABLE auth_refresh_tokens (
  token_hash BYTEA PRIMARY KEY CHECK (octet_length(token_hash) = 32),
  session_id UUID NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
  token_family_id UUID NOT NULL,
  issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ NULL,
  revoked_at TIMESTAMPTZ NULL
);
CREATE INDEX idx_auth_refresh_tokens_session ON auth_refresh_tokens(session_id, expires_at);
CREATE INDEX idx_auth_refresh_tokens_family ON auth_refresh_tokens(token_family_id);

-- §28.2: nullable por compatibilidad con 0001; cada message.send nuevo DEBE rellenarlo.
ALTER TABLE message_events ADD COLUMN payload_fingerprint BYTEA NULL
  CHECK (payload_fingerprint IS NULL OR octet_length(payload_fingerprint) = 32);

-- DUD-03: §23.4 ordena por updated_at, pero §17 no lo define en conversations.
-- Respuesta usuario: no solicitada. DEC-17: añadirlo e inicializar desde created_at.
-- N4/N5 deberán actualizarlo también al aceptar actividad nueva de la conversación.
ALTER TABLE conversations ADD COLUMN updated_at TIMESTAMPTZ;
UPDATE conversations SET updated_at = created_at;
ALTER TABLE conversations ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE conversations ALTER COLUMN updated_at SET NOT NULL;
CREATE INDEX idx_conversations_updated ON conversations(updated_at DESC, id DESC);

-- DUD-04: generation es uint32 (§7), mientras INTEGER de §17 solo cubre int32.
-- Respuesta usuario: no solicitada. DEC-18: BIGINT + CHECK preserva todo el contrato.
ALTER TABLE invitations ALTER COLUMN generation TYPE BIGINT;
ALTER TABLE invitations ADD CONSTRAINT ck_invitation_generation_uint32
  CHECK (generation <= 4294967295);

-- DEC-19: índice con desempate UUID para la paginación estable de §23.4.
CREATE INDEX idx_message_events_history
  ON message_events(conversation_id, sent_at DESC, id DESC);
