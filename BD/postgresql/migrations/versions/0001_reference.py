"""DDL íntegro de §17; ampliaciones normativas en 0002."""

from pathlib import Path

from alembic import op

revision = "0001_reference"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (Path(__file__).parents[1] / "sql/0001_reference.sql").read_text()
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # §30.2: no rollback destructivo automático; usar release compatible/restauración.
    raise RuntimeError("Downgrade destructivo deshabilitado")
