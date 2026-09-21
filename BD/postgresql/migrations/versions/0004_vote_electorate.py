"""N4: conservar las identidades del censo congelado al aceptar."""

from alembic import op

revision = "0004_vote_electorate"
down_revision = "0003_email_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sin FK a users: borrar una cuenta no reduce ni altera el censo congelado.
    # No reconstruir censos históricos a partir de la pertenencia actual.
    op.execute("""CREATE TABLE vote_eligible_members (
        vote_id UUID NOT NULL REFERENCES votes(id) ON DELETE CASCADE,
        user_id UUID NOT NULL,
        PRIMARY KEY (vote_id,user_id)
    )""")


def downgrade() -> None:
    raise RuntimeError("Downgrade destructivo deshabilitado")
