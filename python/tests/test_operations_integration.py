import asyncio
import contextlib
import os
import signal
import socket
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
import uvicorn
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_conversations_integration import client, contact
from test_conversations_integration import n4 as n4
from test_websocket_integration import control, open_socket, received, sending
from websockets.exceptions import ConnectionClosed

from chat.app import create_app
from chat.backup import DURABLE, backup, restore
from chat.config import read_secret
from chat.metrics import DEPENDENCY, PG_LIMIT, REDIS_MEMORY, collect
from chat.server import DrainingServer

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL/Redis aislados y RUN_INTEGRATION=1",
)]


def test_encrypted_backup_restore_excludes_ttl_and_future_tables(n4: tuple, tmp_path: Path) -> None:
    settings, users, _, tokens = n4
    dsn = read_secret("DATABASE_URL")
    pg_bin = Path(os.environ["CHAT_TEST_PG_BIN"])
    name = "restore_" + uuid4().hex
    secret = b"TEMPORARY-CONTENT-MUST-NOT-SURVIVE"
    async def seed() -> str:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            return (await contact(host, guest, "stored"))["id"]
    conversation = asyncio.run(seed())
    identifier = uuid4()
    with psycopg.connect(dsn) as db:
        db.execute("CREATE TABLE future_ttl_table (payload text)")
        db.execute("INSERT INTO future_ttl_table VALUES ('TEMPORARY-CONTENT-MUST-NOT-SURVIVE')")
        db.execute("""INSERT INTO message_events(id,conversation_id,sender_id,expires_at)
            VALUES (%s,%s,%s,now()+interval '1 day')""", (identifier, conversation, users[0]))
        db.execute("""INSERT INTO messages(id,ciphertext,crypto_meta,content_expires_at)
            VALUES (%s,%s,%s,now()+interval '1 day')""", (identifier, secret, b'm' * 56))
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        try:
            key = os.urandom(32)
            started = time.monotonic()
            artifact = backup(dsn, tmp_path, key, pg_bin)
            target = make_conninfo(dsn, dbname=name)
            assert artifact.stat().st_mode & 0o777 == 0o600
            # Una copia alterada NO llega a ejecutar SQL.
            damaged = tmp_path / "damaged.chatbak"
            value = bytearray(artifact.read_bytes())
            value[-1] ^= 1
            damaged.write_bytes(value)
            with pytest.raises(Exception):
                restore(target, damaged, key, pg_bin, tmp_path)
            with psycopg.connect(target) as db:
                assert not db.execute("SELECT 1 FROM pg_tables WHERE schemaname='public'").fetchone()
            restore(target, artifact, key, pg_bin, tmp_path)
            assert time.monotonic() - started < 120
            with psycopg.connect(target) as db:
                assert db.execute("SELECT id FROM users WHERE id=ANY(%s)", (users,)).fetchall()
                assert db.execute("SELECT id FROM conversations WHERE id=%s", (conversation,)).fetchone()
                assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ('0007_recovery_push',)
                tables = db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
                for (table,) in tables:
                    if table not in DURABLE:
                        assert db.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone() == (0,)
            with pytest.raises(ValueError, match="vacía"):
                restore(target, artifact, key, pg_bin, tmp_path)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            admin.execute("DROP TABLE future_ttl_table")


def test_dependency_metrics_against_real_services(n4: tuple) -> None:
    asyncio.run(collect(n4[0]))
    assert DEPENDENCY.labels("postgresql")._value.get() == 1
    assert DEPENDENCY.labels("redis")._value.get() == 1
    assert REDIS_MEMORY._value.get() > 0
    assert PG_LIMIT._value.get() > 0


@pytest.mark.parametrize("mode", ["stored", "ephemeral"])
def test_sigterm_allows_inflight_ack_then_closes_1001(n4: tuple, mode: str) -> None:
    settings, _, _, tokens = n4
    async def run() -> None:
        application = create_app(settings)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = DrainingServer(uvicorn.Config(application, log_config=None, access_log=False,
            log_level="critical", lifespan="off", timeout_graceful_shutdown=1), application, drain_seconds=1.5)
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(.01)
            async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest, \
                    contextlib.AsyncExitStack() as stack, httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as live:
                conversation = (await contact(host, guest, mode))["id"]
                url = f"ws://127.0.0.1:{port}/ws/v1"
                sender, recipient = await open_socket(stack, url, guest), await open_socket(stack, url, host)
                import json
                message = sending(conversation)
                identifier = message["payload"]["message_id"]
                if mode == "stored":
                    await sender.send(json.dumps(message))
                    await received(recipient, "message.new")
                else:
                    await control(sender, "message.offer", conversation, identifier)
                    await received(recipient, "message.offer")
                # El handler real de SIGTERM, sin señalizar el runner pytest.
                started = time.monotonic()
                server.handle_exit(signal.SIGTERM, None)
                server._captured_signals.clear()  # No reenviar SIGTERM al runner al cerrar serve().
                assert (await live.get('/health/ready')).status_code == 503
                assert (await live.post('/api/v1/auth/ws-ticket', headers={'Authorization':'Bearer '+tokens[0]})).status_code == 503
                if mode == "ephemeral":
                    await control(recipient, "message.ready", conversation, identifier)
                    await received(sender, "message.ready")
                    await sender.send(json.dumps(message))
                    await received(recipient, "message.new")
                await control(recipient, 'message.ack', conversation, identifier)
                assert (await received(sender, 'message.delivered'))['payload']['message_id'] == identifier
                with pytest.raises(ConnectionClosed) as closed:
                    while True:
                        await recipient.recv()
                assert closed.value.rcvd.code == 1001
                assert 1 <= time.monotonic() - started < 4
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)
            listener.close()
    asyncio.run(run())
