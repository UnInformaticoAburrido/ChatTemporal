"""N2: hash binario de verificación e índices de familia/usuario."""

from alembic import op

revision = "0003_email_hash"
down_revision = "0002_production"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DEC-34: convertir hashes SHA-256 hex previos, sin borrar/inventar tokens.
    # Una fila antigua no hexadecimal debe fallar la migración para ser revisada.
    op.execute("""ALTER TABLE email_verification_tokens ALTER COLUMN token_hash
               TYPE BYTEA USING decode(token_hash, 'hex')""")
    op.execute("""ALTER TABLE email_verification_tokens ADD CONSTRAINT ck_email_hash_sha256
               CHECK (octet_length(token_hash)=32)""")
    op.execute("CREATE INDEX idx_email_verification_user ON email_verification_tokens(user_id)")
    op.execute("CREATE INDEX idx_auth_sessions_family ON auth_sessions(token_family_id)")


def downgrade() -> None:
    raise RuntimeError("Downgrade destructivo deshabilitado")
