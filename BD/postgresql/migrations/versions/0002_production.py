"""Completa §24.3, §28.2 y campos requeridos por el contrato normativo."""

from pathlib import Path

from alembic import op

revision = "0002_production"
down_revision = "0001_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (Path(__file__).parents[1] / "sql/0002_production.sql").read_text()
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise RuntimeError("Downgrade destructivo deshabilitado")
