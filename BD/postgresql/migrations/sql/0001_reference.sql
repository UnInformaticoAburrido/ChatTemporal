-- Fuente: especificación maestra v1, sección 17. Sin reinterpretaciones.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TYPE conversation_mode AS ENUM ('stored', 'ephemeral');
CREATE TYPE conversation_status AS ENUM ('pending', 'active', 'closed');
CREATE TYPE member_role AS ENUM ('host', 'guest', 'member');
CREATE TYPE member_status AS ENUM ('invited', 'accepted', 'left');
CREATE TYPE invitation_status AS ENUM ('active', 'revoked');
CREATE TYPE delivery_status AS ENUM ('pending', 'delivered', 'failed', 'expired');
CREATE TYPE vote_type AS ENUM (
  'majority_absolute', 'majority_simple', 'unanimous', 'owner_decides'
);
CREATE TYPE vote_status AS ENUM ('open', 'approved', 'rejected', 'cancelled');

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nick CITEXT NOT NULL UNIQUE,
  email CITEXT NOT NULL UNIQUE,
  memory_hash TEXT NOT NULL,
  email_verified BOOLEAN NOT NULL DEFAULT FALSE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Una única clave pública E2EE vigente por usuario.
CREATE TABLE user_keys (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  public_key TEXT NOT NULL,
  protocol_version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE auth_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  refresh_token_hash TEXT NOT NULL UNIQUE,
  token_family_id UUID NOT NULL DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_rotated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ NULL
);
CREATE INDEX idx_auth_sessions_user_exp ON auth_sessions(user_id, expires_at);

CREATE TABLE email_verification_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ NULL
);

-- id es invitation_id. Los códigos visibles NO se guardan completos.
-- Invitation token v1, fixed binary layout, all multibyte integers big-endian:
-- payload = version:uint8[1] || invitation_id:UUID[16] || generation:uint32[4] || created_at:uint64[8] || mode:uint8[1]
-- payload size = 30 bytes; mode 0 = ephemeral, 1 = stored.
-- mac = HMAC-SHA-256(INVITATION_HMAC_SECRET, payload)[32]
-- token = Base64URL(payload || mac) without padding; total raw size 62 bytes / 83 chars Base64URL.
CREATE TABLE invitations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  creator_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  generation INTEGER NOT NULL CHECK (generation > 0),
  status invitation_status NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ NULL,
  use_count INTEGER NOT NULL DEFAULT 0 CHECK (use_count >= 0),
  last_used_at TIMESTAMPTZ NULL,
  UNIQUE (creator_user_id, generation)
);
CREATE INDEX idx_invitations_creator_status
  ON invitations(creator_user_id, status);
CREATE UNIQUE INDEX uq_one_active_invitation_per_user
  ON invitations(creator_user_id) WHERE status = 'active';

CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mode conversation_mode NOT NULL,
  status conversation_status NOT NULL DEFAULT 'pending',
  created_from_invitation_id UUID NULL REFERENCES invitations(id) ON DELETE SET NULL,
  grace_message_limit SMALLINT NOT NULL DEFAULT 5 CHECK (grace_message_limit >= 0),
  grace_messages_used SMALLINT NOT NULL DEFAULT 0 CHECK (grace_messages_used >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  accepted_at TIMESTAMPTZ NULL,
  mode_changed_at TIMESTAMPTZ NULL,
  mode_changed_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
  closed_at TIMESTAMPTZ NULL,
  closed_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
  CHECK (grace_messages_used <= grace_message_limit)
);

CREATE TABLE conversation_members (
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role member_role NOT NULL,
  membership_status member_status NOT NULL DEFAULT 'invited',
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  accepted_at TIMESTAMPTZ NULL,
  left_at TIMESTAMPTZ NULL,
  PRIMARY KEY (conversation_id, user_id)
);
CREATE INDEX idx_conversation_members_user_status
  ON conversation_members(user_id, membership_status);
CREATE UNIQUE INDEX uq_one_host_per_conversation
  ON conversation_members(conversation_id) WHERE role = 'host';

CREATE TABLE message_events (
  id UUID PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  sender_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_message_events_conversation_sent
  ON message_events(conversation_id, sent_at DESC);
CREATE INDEX idx_message_events_expires ON message_events(expires_at);

CREATE TABLE messages (
  id UUID PRIMARY KEY REFERENCES message_events(id) ON DELETE CASCADE,
  ciphertext BYTEA NOT NULL,
  crypto_meta BYTEA NOT NULL CHECK (octet_length(crypto_meta) = 56),
  protocol_version INTEGER NOT NULL DEFAULT 1,
  is_grace_message BOOLEAN NOT NULL DEFAULT FALSE,
  content_expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_messages_content_expires ON messages(content_expires_at);

CREATE TABLE message_deliveries (
  message_id UUID NOT NULL REFERENCES message_events(id) ON DELETE CASCADE,
  recipient_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status delivery_status NOT NULL DEFAULT 'pending',
  received_at TIMESTAMPTZ NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (message_id, recipient_user_id)
);
CREATE INDEX idx_message_deliveries_recipient_status
  ON message_deliveries(recipient_user_id, status);

CREATE TABLE votes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  vote_type vote_type NOT NULL DEFAULT 'majority_absolute',
  subject TEXT NOT NULL,
  eligible_members INTEGER NOT NULL CHECK (eligible_members > 0),
  status vote_status NOT NULL DEFAULT 'open',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 seconds'),
  closed_at TIMESTAMPTZ NULL
);
CREATE INDEX idx_votes_conversation_status ON votes(conversation_id, status);
CREATE INDEX idx_votes_expires ON votes(expires_at) WHERE status = 'open';

CREATE TABLE vote_ballots (
  vote_id UUID NOT NULL REFERENCES votes(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  choice BOOLEAN NOT NULL,
  voted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (vote_id, user_id)
);

CREATE TABLE web_push_subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  endpoint TEXT NOT NULL UNIQUE,
  p256dh TEXT NOT NULL,
  auth_secret TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ NULL
);
CREATE INDEX idx_web_push_user_active
  ON web_push_subscriptions(user_id, revoked_at);
