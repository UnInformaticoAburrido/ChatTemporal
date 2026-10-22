"""N6: un ballot pertenece al censo y sobrevive al borrado del elector."""

from alembic import op

revision = "0006_ballot_electorate"
down_revision = "0005_delivery_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE vote_ballots DROP CONSTRAINT vote_ballots_user_id_fkey")
    # NOT VALID conserva filas históricas externas sin inventar su censo. Las
    # nuevas escrituras sí deben pertenecer al censo desde esta migración.
    op.execute("""ALTER TABLE vote_ballots ADD CONSTRAINT vote_ballots_electorate_fkey
        FOREIGN KEY (vote_id,user_id) REFERENCES vote_eligible_members(vote_id,user_id)
        ON DELETE CASCADE NOT VALID""")


def downgrade() -> None:
    raise RuntimeError("Downgrade destructivo deshabilitado")
