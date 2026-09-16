"""Único punto de entrada: validación, espera y comprobación de esquema antes del puerto."""

import asyncio
import os
import sys

import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from chat.config import load_settings, read_secret, validate_secrets
from chat.dependencies import wait_for_dependencies
from chat.logging import event


def migration_config() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", os.environ.get("MIGRATIONS_DIR", "/migrations"))
    return config


def check_schema() -> None:
    # DEC-13: un reinicio directo tampoco sirve tráfico sobre un esquema antiguo.
    required = ScriptDirectory.from_config(migration_config()).get_current_head()
    with psycopg.connect(read_secret("DATABASE_URL"), connect_timeout=3) as connection:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        if rows != [(required,)]:
            raise RuntimeError("Esquema pendiente de migración")


def main() -> int:
    role = sys.argv[1] if len(sys.argv) == 2 else ""
    if role not in {"api", "worker", "migrate"}:
        return 2
    try:
        settings = load_settings()
        validate_secrets(settings)
        asyncio.run(wait_for_dependencies(settings))
        if role == "migrate":
            command.upgrade(migration_config(), "head")
            event("migration_complete", service="migrate")
            return 0
        check_schema()
    except Exception:
        event("startup_failed", service=role, level="ERROR", error_code="CONFIG_OR_DEPENDENCY")
        return 1
    if role == "worker":
        os.execv(sys.executable, [sys.executable, "-m", "chat.worker"])
    os.execv(sys.executable, [sys.executable, "-m", "chat.server"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
