"""N5: modo histórico y plazo de entrega, sin ciphertext efímero."""

from alembic import op

revision = "0005_delivery_mode"
down_revision = "0004_vote_electorate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Las filas anteriores permanecen NULL: ausencia de contenido no prueba modo.
    op.execute("ALTER TABLE message_events ADD COLUMN mode_at_send conversation_mode NULL")
    op.execute("ALTER TABLE message_deliveries ADD COLUMN deadline_at TIMESTAMPTZ NULL")
    op.execute("ALTER TABLE message_deliveries ADD COLUMN connection_id UUID NULL")
    op.execute("CREATE INDEX idx_delivery_deadline ON message_deliveries(deadline_at) WHERE status='pending'")
    op.execute("CREATE INDEX idx_delivery_connection ON message_deliveries(connection_id) WHERE status='pending'")


def downgrade() -> None:
    raise RuntimeError("Downgrade destructivo deshabilitado")
