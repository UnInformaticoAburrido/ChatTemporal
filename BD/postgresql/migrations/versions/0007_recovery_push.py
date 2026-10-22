"""N7: permisos residuales de transferencia y cola de Push solo con metadatos."""

from alembic import op

revision = "0007_recovery_push"
down_revision = "0006_ballot_electorate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE web_push_subscriptions ADD COLUMN session_id UUID
        REFERENCES auth_sessions(id) ON DELETE CASCADE""")
    op.execute("""CREATE TABLE transfer_upload_grants (
        user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        source_sid UUID NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
        target_sid UUID NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
        expires_at TIMESTAMPTZ NOT NULL,
        CHECK (source_sid<>target_sid)
    )""")
    op.execute("""CREATE TABLE push_jobs (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        message_id UUID NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN ('message.new','message.offer')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        expires_at TIMESTAMPTZ NOT NULL,
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        attempts INTEGER NOT NULL DEFAULT 0,
        finished_at TIMESTAMPTZ,
        UNIQUE(user_id,message_id,event_type)
    )""")
    op.execute("CREATE INDEX idx_push_jobs_pending ON push_jobs(next_attempt_at) WHERE finished_at IS NULL")
    op.execute("""CREATE TABLE push_job_deliveries (
        job_id UUID NOT NULL REFERENCES push_jobs(id) ON DELETE CASCADE,
        subscription_id UUID NOT NULL REFERENCES web_push_subscriptions(id) ON DELETE CASCADE,
        PRIMARY KEY(job_id,subscription_id)
    )""")


def downgrade() -> None:
    raise RuntimeError("Downgrade destructivo deshabilitado")
