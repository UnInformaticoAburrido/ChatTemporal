"""Sockets TCP reales, dos instancias ASGI, PostgreSQL y Redis aislados."""

import asyncio
import contextlib
import json
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import uvicorn
from test_conversations_integration import client, contact
from test_conversations_integration import n4 as n4
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from chat.app import create_app
from chat.config import RateRule, Settings, read_secret
from chat.identity_redis import redis_connection
from chat.protocol import decode_binary, encode_binary
from chat.realtime_redis import RealtimeRedis
from chat.reconciliation import reconcile_once
from chat.ws_protocol import frame
from chat_client.crypto import EncryptedMessage, KeyPair, decrypt_text, encrypt_text

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL/Redis aislados y RUN_INTEGRATION=1",
)]


@asynccontextmanager
async def server(settings: Settings) -> AsyncIterator[tuple[str, object]]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    app = create_app(settings)
    instance = uvicorn.Server(uvicorn.Config(app, log_config=None, log_level="critical", access_log=False,
        lifespan="off", ws="websockets", ws_max_size=settings.max_envelope_bytes,
        ws_ping_interval=settings.ws_ping_interval_seconds, ws_ping_timeout=settings.ws_dead_after_seconds,
        timeout_graceful_shutdown=2))
    task = asyncio.create_task(instance.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not instance.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        yield f"ws://127.0.0.1:{port}/ws/v1", app
    finally:
        instance.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


async def ticket(api) -> str:
    response = await api.post("/api/v1/auth/ws-ticket")
    assert response.status_code == 201, response.text
    return response.json()["ticket"]


async def received(ws: ClientConnection, kind: str) -> dict:
    async with asyncio.timeout(4):
        while True:
            result = json.loads(await ws.recv())
            if result["type"] == kind:
                return result
            assert result["type"] != "system.error", result


def sending(conversation: str, identifier: UUID | None = None) -> dict:
    return frame("message.send", UUID(conversation), {"message_id": str(identifier or uuid4()),
        "protocol_version": 1, "ciphertext": encode_binary(b"encrypted" * 4), "crypto_meta": encode_binary(b"m" * 56)})


async def control(ws, kind: str, conversation: str, message: str) -> None:
    await ws.send(json.dumps(frame(kind, UUID(conversation), {"message_id": message})))


async def open_socket(stack, url, api) -> ClientConnection:
    ws = await stack.enter_async_context(connect(url + "?ticket=" + await ticket(api)))
    ready = await received(ws, "session.ready")
    assert ready["conversation_id"] is None and ready["payload"]["heartbeat_interval_seconds"] > 0
    return ws


def test_stored_delivery_two_instances_history_ack_and_duplicate(n4: tuple) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with server(settings) as (url_a, _), server(settings) as (url_b, _), \
                client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest, \
                client(settings, tokens[2]) as stranger, contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest, "stored"))["id"]
            a = await open_socket(stack, url_a, guest)
            b = await open_socket(stack, url_b, host)
            message = sending(conversation)
            sender, recipient = KeyPair.generate(), KeyPair.generate()
            encrypted = encrypt_text("Hola por WebSocket", sender, recipient.public_key)
            message["payload"].update(ciphertext=encode_binary(encrypted.ciphertext), crypto_meta=encode_binary(encrypted.crypto_meta))
            await a.send(json.dumps(message))
            incoming = (await received(b, "message.new"))["payload"]
            assert incoming["sender_role"] == "guest" and "sender_id" not in incoming
            assert incoming["sent_at"].endswith("Z")
            assert decrypt_text(EncryptedMessage(decode_binary(incoming["ciphertext"], maximum=4096),
                                decode_binary(incoming["crypto_meta"], maximum=56)), recipient) == "Hola por WebSocket"
            identifier = incoming["message_id"]
            path = f"/api/v1/messages/{identifier}/status"
            assert (await guest.get(path)).json()["status"] == "pending"
            assert (await stranger.get(path)).status_code == 404
            await asyncio.gather(*(a.send(json.dumps(message)) for _ in range(5)))
            await control(b, "message.ack", conversation, identifier)
            delivered = await received(a, "message.delivered")
            assert delivered["payload"]["message_id"] == identifier
            assert delivered["payload"]["received_at"].endswith("Z")
            await control(b, "message.ack", conversation, identifier)
            assert (await received(a, "message.delivered"))["payload"] == delivered["payload"]
            assert (await guest.get(path)).json()["status"] == "delivered"
            assert len((await host.get(f"/api/v1/conversations/{conversation}/messages")).json()["items"]) == 1
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT grace_messages_used FROM conversations WHERE id=%s", (conversation,)).fetchone() == (1,)
                assert db.execute("SELECT mode_at_send FROM message_events WHERE id=%s", (identifier,)).fetchone() == ("stored",)
            message["payload"]["ciphertext"] = encode_binary(b"different" * 4)
            await a.send(json.dumps(message))
            assert (await received(a, "system.error"))["payload"]["code"] == "MESSAGE_ID_CONFLICT"
    asyncio.run(run())


def test_ephemeral_handshake_ack_after_upgrade_and_close(n4: tuple) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest))["id"]
            a, b = await open_socket(stack, url, guest), await open_socket(stack, url, host)
            message = sending(conversation)
            identifier = message["payload"]["message_id"]
            await a.send(json.dumps(message))
            assert (await received(a, "message.failed"))["payload"]["code"] == "OFFER_TIMEOUT"
            await control(a, "message.offer", conversation, identifier)
            assert (await received(b, "message.offer"))["payload"] == {"message_id": identifier}
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT 1 FROM message_events WHERE id=%s", (identifier,)).fetchone() is None
            await control(b, "message.ready", conversation, identifier)
            await received(a, "message.ready")
            redis = RealtimeRedis(settings)
            attempt = await redis.get(identifier)
            assert attempt and attempt.phase == "ready" and not await redis.has_payload(attempt)
            await a.send(json.dumps(message))
            await received(b, "message.new")
            attempt = await redis.get(identifier)
            assert attempt and await redis.has_payload(attempt)
            assert (await host.post(f"/api/v1/conversations/{conversation}/accept")).status_code == 200
            await received(b, "vote.opened")
            assert (await host.post(f"/api/v1/conversations/{conversation}/upgrade")).status_code == 200
            assert (await received(b, "conversation.mode_changed"))["payload"]["changed_at"].endswith("Z")
            assert (await host.delete(f"/api/v1/conversations/{conversation}")).status_code == 204
            assert (await received(b, "conversation.closed"))["payload"]["closed_at"].endswith("Z")
            await control(b, "message.ack", conversation, identifier)
            await received(a, "message.delivered")
            assert not await redis.has_payload(attempt)
            assert (await guest.get(f"/api/v1/messages/{identifier}/status")).json()["status"] == "delivered"
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT mode_at_send FROM message_events WHERE id=%s", (identifier,)).fetchone() == ("ephemeral",)
                assert db.execute("SELECT 1 FROM messages WHERE id=%s", (identifier,)).fetchone() is None
    asyncio.run(run())


def test_ephemeral_disconnect_timeout_and_orphan_reconciliation(n4: tuple) -> None:
    settings, users, _, tokens = n4
    settings = settings.model_copy(update={"ephemeral_offer_timeout_seconds": 1, "ephemeral_delivery_timeout_seconds": 1})

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest))["id"]
            a, b = await open_socket(stack, url, guest), await open_socket(stack, url, host)
            message = sending(conversation)
            identifier = message["payload"]["message_id"]
            await control(a, "message.offer", conversation, identifier)
            await received(b, "message.offer")
            await asyncio.sleep(1.05)
            await reconcile_once(settings)
            assert (await received(a, "message.failed"))["payload"]["code"] == "OFFER_TIMEOUT"
            assert (await guest.get(f"/api/v1/messages/{identifier}/status")).status_code == 404
            message = sending(conversation)
            identifier = message["payload"]["message_id"]
            await control(a, "message.offer", conversation, identifier)
            await received(b, "message.offer")
            await control(b, "message.ready", conversation, identifier)
            await received(a, "message.ready")
            await a.send(json.dumps(message))
            await received(b, "message.new")
            await b.close()
            assert (await received(a, "message.failed"))["payload"]["code"] == "RECIPIENT_DISCONNECTED"
            assert (await guest.get(f"/api/v1/messages/{identifier}/status")).json()["status"] == "failed"
            attempt = await RealtimeRedis(settings).get(identifier)
            assert attempt and not await RealtimeRedis(settings).has_payload(attempt)
            # Simular pérdida completa de metadata Redis tras un commit ephemeral.
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                db.execute("UPDATE message_deliveries SET status='pending',deadline_at=clock_timestamp() WHERE message_id=%s",
                           (identifier,))
            async with redis_connection() as redis:
                await redis.delete(f"ephemeral:attempt:{identifier}")
            await reconcile_once(settings)
            assert (await guest.get(f"/api/v1/messages/{identifier}/status")).json()["status"] == "expired"
    asyncio.run(run())


def test_ticket_one_time_origin_binary_frame_size_revocation_and_draining(n4: tuple) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with server(settings) as (url, app), client(settings, tokens[0]) as host:
            one_time = await ticket(host)
            with pytest.raises(InvalidStatus):
                async with connect(url + "?ticket=" + one_time, origin="https://untrusted.invalid"):
                    pass
            async with connect(url + "?ticket=" + one_time) as ws:
                await received(ws, "session.ready")
                with pytest.raises(InvalidStatus):
                    async with connect(url + "?ticket=" + one_time):
                        pass
                await ws.send(b"binary")
                with pytest.raises(ConnectionClosed) as error:
                    await ws.recv()
                assert error.value.rcvd.code == 1003
            async with connect(url + "?ticket=" + await ticket(host)) as ws:
                await received(ws, "session.ready")
                await ws.send("x" * (settings.max_envelope_bytes+1))
                with pytest.raises(ConnectionClosed) as error:
                    await ws.recv()
                assert error.value.rcvd.code == 1009
            async with connect(url + "?ticket=" + await ticket(host)) as ws:
                await received(ws, "session.ready")
                app.state.draining = True
                with pytest.raises(ConnectionClosed) as error:
                    await asyncio.wait_for(ws.recv(), 3)
                assert error.value.rcvd.code == 1001
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://test", headers=host.headers) as same_api:
                    assert (await same_api.post("/api/v1/auth/ws-ticket")).status_code == 503
                app.state.draining = False
            async with connect(url + "?ticket=" + await ticket(host)) as ws:
                await received(ws, "session.ready")
                assert (await host.post("/api/v1/auth/logout")).status_code == 204
                with pytest.raises(ConnectionClosed) as error:
                    await asyncio.wait_for(ws.recv(), 3)
                assert error.value.rcvd.code == 4401
    asyncio.run(run())


def test_offline_stored_and_offer_on_recipient_reconnection(n4: tuple) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            stored = (await contact(host, guest, "stored"))["id"]
            ephemeral = (await contact(host, guest))["id"]
            a = await open_socket(stack, url, guest)
            message = sending(stored)
            await a.send(json.dumps(message))
            path = f"/api/v1/messages/{message['payload']['message_id']}/status"
            for _ in range(20):
                status = await guest.get(path)
                if status.status_code == 200:
                    break
                await asyncio.sleep(0.02)
            assert status.json()["status"] == "pending"
            await a.close()
            a = await open_socket(stack, url, guest)
            await a.send(json.dumps(message))
            assert len((await host.get(f"/api/v1/conversations/{stored}/messages")).json()["items"]) == 1
            offer = str(uuid4())
            await control(a, "message.offer", ephemeral, offer)
            for _ in range(30):
                if await RealtimeRedis(settings).get(offer):
                    break
                await asyncio.sleep(0.01)
            b = await open_socket(stack, url, host)
            assert (await received(b, "message.offer"))["payload"]["message_id"] == offer
            await control(b, "message.ready", ephemeral, offer)
            await received(a, "message.ready")
    asyncio.run(run())


def test_pending_offer_cancelled_by_accept_upgrade_and_never_stored(n4: tuple) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest))["id"]
            a, b = await open_socket(stack, url, guest), await open_socket(stack, url, host)
            message = sending(conversation)
            identifier = message["payload"]["message_id"]
            await control(a, "message.offer", conversation, identifier)
            await received(b, "message.offer")
            await control(b, "message.ready", conversation, identifier)
            await received(a, "message.ready")
            assert (await host.post(f"/api/v1/conversations/{conversation}/accept")).status_code == 200
            assert (await received(a, "message.failed"))["payload"]["code"] == "VOTE_OPEN"
            assert (await host.post(f"/api/v1/conversations/{conversation}/upgrade")).status_code == 200
            # Terminar el plazo del voto permite probar específicamente la carrera de modo.
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                db.execute("UPDATE votes SET expires_at=clock_timestamp() WHERE conversation_id=%s", (conversation,))
            await a.send(json.dumps(message))
            assert (await received(a, "message.failed"))["payload"]["code"] == "TEMPORARY_UNAVAILABLE"
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT 1 FROM message_events WHERE id=%s", (identifier,)).fetchone() is None
                assert db.execute("SELECT 1 FROM messages WHERE id=%s", (identifier,)).fetchone() is None
    asyncio.run(run())


def test_ready_is_bound_to_receiver_connection_and_late_ack_fails(n4: tuple) -> None:
    settings, _, _, tokens = n4
    settings = settings.model_copy(update={"ephemeral_delivery_timeout_seconds": 1})

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest))["id"]
            a, b = await open_socket(stack, url, guest), await open_socket(stack, url, host)
            other = await open_socket(stack, url, host)
            message = sending(conversation)
            identifier = message["payload"]["message_id"]
            await control(a, "message.offer", conversation, identifier)
            await received(b, "message.offer")
            await received(other, "message.offer")
            await control(b, "message.ready", conversation, identifier)
            await received(a, "message.ready")
            await a.send(json.dumps(message))
            await received(b, "message.new")
            await control(other, "message.ack", conversation, identifier)
            assert (await received(other, "system.error"))["payload"]["code"] == "OFFER_NOT_FOUND"
            await asyncio.sleep(1.05)
            await control(b, "message.ack", conversation, identifier)
            assert (await received(b, "system.error"))["payload"]["code"] == "DELIVERY_TIMEOUT"
            await reconcile_once(settings)
            assert (await received(a, "message.failed"))["payload"]["code"] == "DELIVERY_TIMEOUT"
            assert (await guest.get(f"/api/v1/messages/{identifier}/status")).json()["status"] == "expired"
    asyncio.run(run())


def test_redis_loss_closes_sockets_and_reconciles_durable_ephemeral(n4: tuple) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest))["id"]
            a, b = await open_socket(stack, url, guest), await open_socket(stack, url, host)
            message = sending(conversation)
            identifier = message["payload"]["message_id"]
            await control(a, "message.offer", conversation, identifier)
            await received(b, "message.offer")
            await control(b, "message.ready", conversation, identifier)
            await received(a, "message.ready")
            await a.send(json.dumps(message))
            await received(b, "message.new")
            # Redis de este runner es exclusivamente sintético; simula reinicio sin persistencia.
            async with redis_connection() as redis:
                await redis.flushdb()
            for ws in (a, b):
                with pytest.raises(ConnectionClosed) as error:
                    async with asyncio.timeout(4):
                        while True:
                            await ws.recv()
                assert error.value.rcvd.code == 1011
            await reconcile_once(settings)
            assert (await guest.get(f"/api/v1/messages/{identifier}/status")).json()["status"] == "failed"
            b = await open_socket(stack, url, host)
            await control(b, "message.ack", conversation, identifier)
            assert (await received(b, "system.error"))["payload"]["code"] == "DELIVERY_TIMEOUT"
    asyncio.run(run())


def test_ticket_race_expiry_and_persistent_rate_limit(n4: tuple) -> None:
    settings, _, _, tokens = n4
    settings = settings.model_copy(update={"rate_limits": settings.rate_limits.model_copy(update={
        "ws_frames": RateRule(limit=1, seconds=60, burst=1),
    })})

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            expired = await ticket(guest)
            async with redis_connection() as redis:
                await redis.pexpire(f"ws:ticket:{sha256(decode_binary(expired, maximum=32)).hexdigest()}", 1)
            await asyncio.sleep(0.02)
            with pytest.raises(InvalidStatus):
                await connect(url + "?ticket=" + expired)
            one_time = await ticket(guest)
            results = await asyncio.gather(*(connect(url + "?ticket=" + one_time) for _ in range(2)), return_exceptions=True)
            assert sum(isinstance(result, InvalidStatus) for result in results) == 1
            ws = next(result for result in results if isinstance(result, ClientConnection))
            try:
                await received(ws, "session.ready")
                conversation = (await contact(host, guest, "stored"))["id"]
                message = sending(conversation)
                await ws.send(json.dumps(message))
                for _ in range(3):
                    await ws.send(json.dumps(message))
                    assert (await received(ws, "system.error"))["payload"]["code"] == "RATE_LIMITED"
                with pytest.raises(ConnectionClosed) as error:
                    await ws.recv()
                assert error.value.rcvd.code == 4429
            finally:
                await ws.close()
    asyncio.run(run())


def test_native_ping_timeout(n4: tuple) -> None:
    from websockets.legacy.client import WebSocketClientProtocol
    from websockets.legacy.client import connect as legacy_connect

    settings, _, _, tokens = n4
    settings = settings.model_copy(update={"ws_ping_interval_seconds": 1,
        "ws_dead_after_seconds": 2, "presence_ttl_seconds": 4})
    pings = []

    class SilentClient(WebSocketClientProtocol):
        async def pong(self, data=b"") -> None:
            pings.append(data)  # Receptor que recibe ping nativo y deliberadamente no responde.

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host:
            async with legacy_connect(url + "?ticket=" + await ticket(host), create_protocol=SilentClient,
                                      ping_interval=None) as ws:
                assert json.loads(await ws.recv())["type"] == "session.ready"
                with pytest.raises(ConnectionClosed) as error:
                    await asyncio.wait_for(ws.recv(), 5)
                assert pings and error.value.rcvd.code == 1011
    asyncio.run(run())


def test_offer_and_send_count_once_and_ws_horizontal_access_is_denied(n4: tuple) -> None:
    settings, _, _, tokens = n4
    settings = settings.model_copy(update={"rate_limits": settings.rate_limits.model_copy(update={
        "messages_user": RateRule(limit=1, seconds=60, burst=1),
    })})

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, client(settings, tokens[2]) as outsider, \
                contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest))["id"]
            a, b = await open_socket(stack, url, guest), await open_socket(stack, url, host)
            stranger = await open_socket(stack, url, outsider)
            message = sending(conversation)
            identifier = message["payload"]["message_id"]
            await stranger.send(json.dumps(message))
            assert (await received(stranger, "system.error"))["payload"]["code"] == "CONVERSATION_NOT_FOUND"
            await control(a, "message.offer", conversation, identifier)
            await received(b, "message.offer")
            await control(b, "message.ready", conversation, identifier)
            await received(a, "message.ready")
            await a.send(json.dumps(message))
            await received(b, "message.new")  # El send no consume otra cuota lógica.
            await control(b, "message.ack", conversation, identifier)
            await received(a, "message.delivered")
            await control(a, "message.offer", conversation, str(uuid4()))
            assert (await received(a, "system.error"))["payload"]["code"] == "RATE_LIMITED"
    asyncio.run(run())


def test_sends_blocked_by_vote_and_close_and_idle_revocation_without_pubsub(n4: tuple) -> None:
    settings, _, sessions, tokens = n4

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest, "stored"))["id"]
            a = await open_socket(stack, url, guest)
            assert (await host.post(f"/api/v1/conversations/{conversation}/accept")).status_code == 200
            await received(a, "vote.opened")
            message = sending(conversation)
            await a.send(json.dumps(message))
            assert (await received(a, "message.failed"))["payload"]["code"] == "VOTE_OPEN"
            assert (await host.delete(f"/api/v1/conversations/{conversation}")).status_code == 204
            await received(a, "conversation.closed")
            await a.send(json.dumps(message))
            assert (await received(a, "message.failed"))["payload"]["code"] == "CONVERSATION_CLOSED"
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT 1 FROM message_events WHERE id=%s",
                                  (message["payload"]["message_id"],)).fetchone() is None
                db.execute("UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE id=%s", (sessions[1],))
            with pytest.raises(ConnectionClosed) as error:
                await asyncio.wait_for(a.recv(), 3)
            assert error.value.rcvd.code == 4401
    asyncio.run(run())
