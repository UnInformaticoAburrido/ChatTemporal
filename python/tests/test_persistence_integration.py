"""PostgreSQL real; mismas restricciones de aislamiento que test_integration."""

import asyncio
import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from chat.config import read_secret
from chat.persistence import (
    ConversationUnavailable,
    MessageIdConflict,
    MessageWrite,
    Position,
    transaction,
)
from chat.worker import CLEANUP_SQL

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere servicios aislados, APP_ENV=test y RUN_INTEGRATION=1",
)]


@pytest.fixture
def pair() -> Iterator[tuple[UUID, UUID, UUID]]:
    host, guest, conversation = uuid4(), uuid4(), uuid4()
    with psycopg.connect(read_secret("DATABASE_URL")) as connection:
        for user in (host, guest):
            connection.execute("INSERT INTO users(id,nick,email,memory_hash) VALUES (%s,%s,%s,'hash')",
                               (user, str(user), f"{user}@test.invalid"))
        connection.execute("INSERT INTO conversations(id,mode) VALUES (%s,'stored')", (conversation,))
        for user, role in ((host, "host"), (guest, "guest")):
            connection.execute("""INSERT INTO conversation_members(conversation_id,user_id,role)
                                VALUES (%s,%s,%s)""", (conversation, user, role))
    yield host, guest, conversation
    with psycopg.connect(read_secret("DATABASE_URL")) as connection:
        connection.execute("DELETE FROM conversations WHERE id=%s", (conversation,))
        connection.execute("DELETE FROM users WHERE id IN (%s,%s)", (host, guest))


def new_message(pair: tuple[UUID, UUID, UUID]) -> MessageWrite:
    return MessageWrite(uuid4(), pair[2], pair[1], 1, os.urandom(56), os.urandom(40))


def test_atomic_commit_duplicate_conflict_and_current_delivery(pair: tuple[UUID, UUID, UUID]) -> None:
    message = new_message(pair)

    async def run() -> None:
        async with transaction() as unit:
            first = await unit.messages.record(message)
            assert first.created
            assert first.event.expires_at - first.event.sent_at == timedelta(days=30)
            assert [d.recipient_user_id for d in first.deliveries] == [pair[0]]
            await unit.connection.execute("""UPDATE message_deliveries SET status='delivered',
                received_at=clock_timestamp() WHERE message_id=%s""", (message.id,))
            await unit.connection.execute("UPDATE conversations SET status='closed' WHERE id=%s", (pair[2],))
        async with transaction() as unit:
            retry = await unit.messages.record(message)
            assert not retry.created
            assert retry.deliveries[0].status == "delivered"
            assert retry.event == first.event
            for changed in (replace(message, ciphertext=b"other"), replace(message, sender_id=pair[0]),
                            replace(message, crypto_meta=b"a" * 56)):
                with pytest.raises(MessageIdConflict):
                    await unit.messages.record(changed)
            conversation = await unit.conversations.lock(pair[2])
            assert conversation.grace_messages_used == 1
            history = await unit.messages.history(pair[2], pair[0])
            assert [m.id for m in history] == [message.id]
            assert history[0].sender_role == "guest"
            assert history[0].ciphertext == message.ciphertext
    asyncio.run(run())


def test_rollback_including_grace_and_deliveries(pair: tuple[UUID, UUID, UUID]) -> None:
    message = new_message(pair)

    async def run() -> None:
        with pytest.raises(RuntimeError):
            async with transaction() as unit:
                await unit.messages.record(message)
                raise RuntimeError("simulated failure after write")
        async with transaction() as unit:
            assert (await unit.conversations.lock(pair[2])).grace_messages_used == 0
            for table in ("message_events", "messages", "message_deliveries"):
                column = "message_id" if table == "message_deliveries" else "id"
                row = await (await unit.connection.execute(
                    sql.SQL("SELECT count(*) FROM {} WHERE {}=%s").format(
                        sql.Identifier(table), sql.Identifier(column)), (message.id,),
                )).fetchone()
                assert row == (0,)
    asyncio.run(run())


def test_ephemeral_has_no_persistent_ciphertext_even_after_upgrade(pair: tuple[UUID, UUID, UUID]) -> None:
    message = new_message(pair)

    async def run() -> None:
        async with transaction() as unit:
            await unit.conversations.lock(pair[2])
            await unit.connection.execute("UPDATE conversations SET mode='ephemeral' WHERE id=%s", (pair[2],))
            await unit.messages.record(message)
            await unit.connection.execute("UPDATE conversations SET mode='stored' WHERE id=%s", (pair[2],))
            assert not (await unit.messages.record(message)).created
            assert await unit.messages.history(pair[2], pair[0]) == []
            assert await (await unit.connection.execute(
                "SELECT 1 FROM messages WHERE id=%s", (message.id,),
            )).fetchone() is None
    asyncio.run(run())


def test_history_expiration_authorization_and_tied_cursor(pair: tuple[UUID, UUID, UUID]) -> None:
    async def run() -> None:
        async with transaction() as unit:
            messages = [new_message(pair) for _ in range(4)]
            for message in messages:
                await unit.messages.record(message)
            stamp = datetime.now(UTC) - timedelta(hours=1)
            await unit.connection.execute("UPDATE message_events SET sent_at=%s WHERE conversation_id=%s",
                                          (stamp, pair[2]))
            await unit.connection.execute("UPDATE messages SET content_expires_at=clock_timestamp() WHERE id=%s",
                                          (messages[0].id,))
            first = await unit.messages.history(pair[2], pair[0], limit=2)
            second = await unit.messages.history(pair[2], pair[0], before=Position(stamp, first[-1].id))
            assert [m.id for m in first + second] == sorted([m.id for m in messages[1:]], reverse=True)
            assert await unit.messages.history(pair[2], uuid4(), before=Position(stamp, first[0].id)) == []
            # now() se fijó al iniciar la transacción. Caducar dentro de ella debe
            # excluir inmediatamente incluso si la purga aún no se ejecutó.
            await unit.connection.execute("UPDATE messages SET content_expires_at=clock_timestamp() WHERE id=%s",
                                          (messages[1].id,))
            assert len(await unit.messages.history(pair[2], pair[0])) == 2
            await unit.connection.execute("""UPDATE conversation_members SET membership_status='left'
                                           WHERE conversation_id=%s AND user_id=%s""", (pair[2], pair[0]))
            assert await unit.messages.history(pair[2], pair[0]) == []
            assert await unit.conversations.list_for_member(pair[0]) == []
            assert pair[2] in [c.id for c in await unit.conversations.list_for_member(pair[1])]
    asyncio.run(run())


def test_concurrent_retries_and_grace_cap(pair: tuple[UUID, UUID, UUID]) -> None:
    async def send(message: MessageWrite) -> bool:
        async with transaction() as unit:
            return (await unit.messages.record(message)).created

    async def run() -> None:
        message = new_message(pair)
        retries = await asyncio.gather(*(send(message) for _ in range(8)))
        assert retries.count(True) == 1
        outcomes = await asyncio.gather(*(send(new_message(pair)) for _ in range(8)), return_exceptions=True)
        assert outcomes.count(True) == 4
        assert sum(isinstance(result, ConversationUnavailable) for result in outcomes) == 4
        async with transaction() as unit:
            assert (await unit.conversations.lock(pair[2])).grace_messages_used == 5
            assert len(await unit.messages.history(pair[2], pair[0])) == 5
    asyncio.run(run())


def test_send_waits_for_close_lock(pair: tuple[UUID, UUID, UUID]) -> None:
    async def run() -> None:
        started: asyncio.Future[int] = asyncio.get_running_loop().create_future()

        async def send() -> None:
            async with transaction() as unit:
                started.set_result(unit.connection.info.backend_pid)
                await unit.messages.record(new_message(pair))

        async with transaction() as unit:
            await unit.conversations.lock(pair[2])
            sending = asyncio.create_task(send())
            pid = await asyncio.wait_for(started, timeout=3)
            # Observar el bloqueo real en PostgreSQL; no depender de que una
            # pausa arbitraria deje a la otra tarea en el punto esperado.
            async with asyncio.timeout(3):
                while True:
                    waiting = await (await unit.connection.execute(
                        "SELECT 1 FROM pg_locks WHERE pid=%s AND NOT granted", (pid,),
                    )).fetchone()
                    if waiting:
                        break
                    await asyncio.sleep(0.01)
            assert not sending.done()
            await unit.connection.execute("UPDATE conversations SET status='closed' WHERE id=%s", (pair[2],))
        with pytest.raises(ConversationUnavailable):
            await asyncio.wait_for(sending, timeout=3)
        async with transaction() as unit:
            assert await unit.messages.history(pair[2], pair[0]) == []
    asyncio.run(run())


def test_global_uuid_conflict_across_conversations(pair: tuple[UUID, UUID, UUID]) -> None:
    async def run() -> None:
        other = uuid4()
        try:
            async with transaction() as unit:
                await unit.connection.execute("INSERT INTO conversations(id,mode) VALUES (%s,'stored')", (other,))
                await unit.connection.execute("""INSERT INTO conversation_members(conversation_id,user_id,role)
                    SELECT %s,user_id,role FROM conversation_members WHERE conversation_id=%s""", (other, pair[2]))
            message = new_message(pair)

            async def send(msg: MessageWrite) -> bool:
                async with transaction() as unit:
                    return (await unit.messages.record(msg)).created

            results = await asyncio.gather(send(message), send(replace(message, conversation_id=other)),
                                           return_exceptions=True)
            assert results.count(True) == 1
            assert sum(isinstance(result, MessageIdConflict) for result in results) == 1
            async with transaction() as unit:
                counts = [(await unit.conversations.lock(cid)).grace_messages_used for cid in (other, pair[2])]
                assert sorted(counts) == [0, 1]
        finally:
            async with transaction() as unit:
                await unit.connection.execute("DELETE FROM conversations WHERE id=%s", (other,))
    asyncio.run(run())


def test_refresh_audit_uses_whole_family_expiration(pair: tuple[UUID, UUID, UUID]) -> None:
    family, old_session, new_session = uuid4(), uuid4(), uuid4()
    with psycopg.connect(read_secret("DATABASE_URL")) as connection:
        for sid, days in ((old_session, -90), (new_session, 1)):
            connection.execute("""INSERT INTO auth_sessions
                (id,user_id,refresh_token_hash,token_family_id,expires_at)
                VALUES (%s,%s,%s,%s,now()+%s*interval '1 day')""", (sid, pair[0], str(sid), family, days))
        connection.execute("""INSERT INTO auth_refresh_tokens(token_hash,session_id,token_family_id,expires_at)
            VALUES (%s,%s,%s,now()-interval '90 days')""", (os.urandom(32), old_session, family))
        connection.execute(CLEANUP_SQL, prepare=False)
        assert connection.execute("SELECT 1 FROM auth_refresh_tokens WHERE token_family_id=%s", (family,)).fetchone()
        assert connection.execute("SELECT 1 FROM auth_sessions WHERE id=%s", (old_session,)).fetchone()
        connection.execute("UPDATE auth_sessions SET expires_at=now()-interval '31 days' WHERE id=%s", (new_session,))
        connection.execute(CLEANUP_SQL, prepare=False)
        assert connection.execute("SELECT 1 FROM auth_refresh_tokens WHERE token_family_id=%s", (family,)).fetchone() is None
        assert connection.execute("SELECT 1 FROM auth_sessions WHERE token_family_id=%s", (family,)).fetchone() is None


def test_real_constraints_and_user_repository(pair: tuple[UUID, UUID, UUID]) -> None:
    async def run() -> None:
        async with transaction() as unit:
            name = str(uuid4())
            user = await unit.users.create(nick=name, email=f"{name}@test.invalid", memory_hash="test-hash")
            assert await unit.users.get(user.id) == user
            assert "test-hash" not in repr(user)
            try:
                with pytest.raises(psycopg.errors.UniqueViolation):
                    async with unit.connection.transaction():
                        await unit.users.create(nick=name, email=f"other-{name}@test.invalid", memory_hash="hash")
                message = new_message(pair)
                await unit.messages.record(message)
                with pytest.raises(psycopg.errors.CheckViolation):
                    async with unit.connection.transaction():
                        await unit.connection.execute("UPDATE messages SET crypto_meta=%s WHERE id=%s",
                                                      (b"wrong-length", message.id))
                await unit.connection.execute("""INSERT INTO invitations(creator_user_id,generation)
                    VALUES (%s,4294967295)""", (pair[0],))
                with pytest.raises(psycopg.errors.CheckViolation):
                    async with unit.connection.transaction():
                        await unit.connection.execute("UPDATE invitations SET generation=4294967296 WHERE creator_user_id=%s",
                                                      (pair[0],))
                with pytest.raises(psycopg.errors.UniqueViolation):
                    async with unit.connection.transaction():
                        await unit.connection.execute("""UPDATE conversation_members SET role='host'
                            WHERE conversation_id=%s AND user_id=%s""", (pair[2], pair[1]))
            finally:
                await unit.connection.execute("DELETE FROM users WHERE id=%s", (user.id,))
    asyncio.run(run())


def test_concurrent_active_invitation_uniqueness(pair: tuple[UUID, UUID, UUID]) -> None:
    async def create(generation: int) -> bool:
        async with transaction() as unit:
            await unit.connection.execute("INSERT INTO invitations(creator_user_id,generation) VALUES (%s,%s)",
                                          (pair[0], generation))
            return True

    async def run() -> None:
        results = await asyncio.gather(*(create(i) for i in range(1, 5)), return_exceptions=True)
        assert results.count(True) == 1
        assert sum(isinstance(result, psycopg.errors.UniqueViolation) for result in results) == 3
    asyncio.run(run())


def test_content_and_event_retention_are_independent(pair: tuple[UUID, UUID, UUID]) -> None:
    async def run() -> None:
        content_expired, event_expired = new_message(pair), new_message(pair)
        async with transaction() as unit:
            await unit.messages.record(content_expired)
            await unit.messages.record(event_expired)
            await unit.connection.execute("UPDATE messages SET content_expires_at=clock_timestamp() WHERE id=%s",
                                          (content_expired.id,))
            await unit.connection.execute("UPDATE message_events SET expires_at=clock_timestamp() WHERE id=%s",
                                          (event_expired.id,))
        async with transaction() as unit:
            assert await unit.messages.history(pair[2], pair[0]) == []
            await unit.connection.execute(CLEANUP_SQL, prepare=False)
            assert await (await unit.connection.execute("SELECT 1 FROM messages WHERE id IN (%s,%s)",
                          (content_expired.id, event_expired.id))).fetchone() is None
            for table, column in (("message_events", "id"), ("message_deliveries", "message_id")):
                rows = await (await unit.connection.execute(
                    sql.SQL("SELECT {} FROM {} WHERE {} IN (%s,%s)").format(
                        sql.Identifier(column), sql.Identifier(table), sql.Identifier(column)),
                    (content_expired.id, event_expired.id),
                )).fetchall()
                assert rows == [(content_expired.id,)]
    asyncio.run(run())


@pytest.mark.parametrize("staged", [False, True], ids=["empty_to_head", "0001_to_0002"])
def test_real_migrations_in_disposable_database(staged: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = read_secret("DATABASE_URL")
    database = "chat_migration_test_" + uuid4().hex
    config = Config()
    migrations = os.environ.get("MIGRATIONS_DIR", str(Path(__file__).resolve().parents[2] / "BD/postgresql/migrations"))
    config.set_main_option("script_location", migrations)
    with psycopg.connect(original, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    try:
        secret = tmp_path / "test_database_url"
        # Conservar URI, query del socket y credenciales sin imprimirlas.
        dsn = make_url(original).set(database=database).render_as_string(hide_password=False)
        secret.write_text(dsn)
        secret.chmod(0o600)
        monkeypatch.setenv("DATABASE_URL_FILE", str(secret))
        if staged:
            command.upgrade(config, "0001_reference")
            with psycopg.connect(dsn) as connection:
                connection.execute("INSERT INTO conversations(mode) VALUES ('stored')")
        command.upgrade(config, "head")
        with psycopg.connect(dsn) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0002_production",)
            if staged:
                assert connection.execute("SELECT created_at=updated_at FROM conversations").fetchone() == (True,)
            # DDL ejecutado realmente; constraints comprobadas en la prueba anterior.
            assert connection.execute("SELECT to_regclass('auth_refresh_tokens')").fetchone() == ("auth_refresh_tokens",)
    finally:
        with psycopg.connect(original, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
