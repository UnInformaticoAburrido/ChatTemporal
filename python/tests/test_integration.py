"""Servicios aislados: scripts/test_containers.sh o scripts/test_local_services.sh."""

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest
from redis import Redis

from chat.config import read_secret
from chat.worker import cleanup_once

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere proyecto de test aislado y RUN_INTEGRATION=1",
)]


def test_migrations_and_retention_against_real_services() -> None:
    user_id, session_id = uuid4(), uuid4()
    with psycopg.connect(read_secret("DATABASE_URL")) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0003_email_hash",)
        connection.execute("INSERT INTO users(id,nick,email,memory_hash) VALUES (%s,%s,%s,%s)",
                           (user_id, str(user_id), f"{user_id}@test.invalid", "test-only"))
        connection.execute("""INSERT INTO auth_sessions(id,user_id,refresh_token_hash,expires_at)
            VALUES (%s,%s,%s,now() - interval '1 day')""", (session_id, user_id, str(uuid4())))
        connection.execute("""INSERT INTO auth_refresh_tokens(token_hash,session_id,token_family_id,expires_at)
            VALUES (%s,%s,%s,now() - interval '1 day')""", (os.urandom(32), session_id, uuid4()))
    try:
        asyncio.run(cleanup_once())
        with psycopg.connect(read_secret("DATABASE_URL")) as connection:
            assert connection.execute("SELECT id FROM auth_sessions WHERE id=%s", (session_id,)).fetchone()
            connection.execute("UPDATE auth_sessions SET expires_at=now()-interval '31 days' WHERE id=%s", (session_id,))
            connection.execute("UPDATE auth_refresh_tokens SET expires_at=now()-interval '31 days' WHERE session_id=%s", (session_id,))
        asyncio.run(cleanup_once())
        with psycopg.connect(read_secret("DATABASE_URL")) as connection:
            assert connection.execute("SELECT id FROM auth_sessions WHERE id=%s", (session_id,)).fetchone() is None
        with Redis.from_url(read_secret("REDIS_URL")) as redis:
            assert redis.ping()
            assert redis.config_get("appendonly")["appendonly"] == "no"
            assert redis.config_get("save")["save"] == ""
            assert redis.config_get("maxmemory-policy")["maxmemory-policy"] == "noeviction"
            assert redis.get("maintenance:last_success")
    finally:
        with psycopg.connect(read_secret("DATABASE_URL")) as connection:
            connection.execute("DELETE FROM users WHERE id=%s", (user_id,))
