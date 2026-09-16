"""DEC-13: Alembic usa conexión directa y lock; el DSN nunca pasa por logs/INI."""

from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from chat.config import read_secret

if context.is_offline_mode():
    raise RuntimeError("Migrar requiere PostgreSQL y lock de despliegue")

dsn = read_secret("DATABASE_URL").replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True)
with engine.connect() as connection:
    # DEC-13: serializa invocaciones concurrentes, incluso de dos operadores.
    connection.execute(text("SET lock_timeout = '120s'"))
    connection.execute(text("SELECT pg_advisory_lock(6734511)"))
    connection.commit()
    try:
        context.configure(connection=connection, target_metadata=None, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.rollback()
        connection.execute(text("SELECT pg_advisory_unlock(6734511)"))
        connection.commit()
engine.dispose()
