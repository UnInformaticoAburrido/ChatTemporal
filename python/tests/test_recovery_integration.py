"""Transferencias y replay con PostgreSQL, Redis y sockets de dos instancias."""

import asyncio
import contextlib
import json
import os
from uuid import UUID, uuid4

import psycopg
import pytest
from test_conversations_integration import client, contact
from test_conversations_integration import n4 as n4
from test_identity_integration import bootstrap
from test_voting_integration import expire
from test_websocket_integration import open_socket, received, server

from chat.config import RateRule, read_secret
from chat.identity_redis import redis_connection
from chat.protocol import encode_binary
from chat.ws_protocol import frame
from chat_client.recovery import decrypt_bundle, encrypt_bundle

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL/Redis aislados y RUN_INTEGRATION=1",
)]


def test_transfer_contract_ownership_single_upload_ttl_and_delete(n4: tuple, capsys) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as owner, client(settings, tokens[1]) as outsider:
            created = await owner.post("/api/v1/key-transfers")
            assert created.status_code == 201 and created.json()["max_blob_bytes"] == 65536
            identifier = created.json()["transfer_id"]
            path = f"/api/v1/key-transfers/{identifier}"
            blob = encode_binary(b"x" * 65536)
            assert (await owner.get(path)).status_code == 409
            for request in (outsider.get(path), outsider.put(path, json={"encrypted_blob": blob}), outsider.delete(path)):
                assert (await request).status_code == 404
            assert (await owner.put(path, json={"encrypted_blob": "bad!"})).status_code == 422
            assert (await owner.put(path, json={"encrypted_blob": blob, "secret": "forbidden"})).status_code == 422
            assert (await owner.put(path, json={"encrypted_blob": encode_binary(b"x" * 65537)})).status_code == 413
            async with redis_connection() as redis:
                before = await redis.pttl(f"key_transfer_meta:{identifier}")
                assert 86390000 <= before <= 86400000
            results = await asyncio.gather(*(owner.put(path, json={"encrypted_blob": blob}) for _ in range(4)))
            assert sorted(r.status_code for r in results) == [204, 409, 409, 409]
            first, second = await owner.get(path), await owner.get(path)
            assert first.json() == second.json() and first.json()["encrypted_blob"] == blob
            async with redis_connection() as redis:
                assert 0 < await redis.pttl(f"key_transfer:{identifier}") <= before
            assert (await owner.delete(path)).status_code == 204
            async with redis_connection() as redis:
                assert not await redis.exists(f"key_transfer:{identifier}", f"key_transfer_meta:{identifier}")
            assert (await owner.get(path)).json()["error"]["code"] == "KEY_TRANSFER_EXPIRED"
            identifier = (await owner.post("/api/v1/key-transfers")).json()["transfer_id"]
            async with redis_connection() as redis:
                await redis.pexpire(f"key_transfer_meta:{identifier}", 0)
            assert (await owner.get(f"/api/v1/key-transfers/{identifier}")).status_code == 410
            assert blob not in capsys.readouterr().out
    asyncio.run(run())


def test_real_device_handoff_keeps_old_session_upload_only(n4: tuple) -> None:
    settings, users, sessions, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as old, client(settings, tokens[0]) as new:
            pair = await new.post("/api/v1/auth/exchange", json={"bootstrap_token": bootstrap(users[0])})
            assert pair.status_code == 200, pair.text
            new.headers["Authorization"] = "Bearer " + pair.json()["access_token"]
            assert (await old.get("/api/v1/users/me")).status_code == 401
            assert (await old.post("/api/v1/auth/ws-ticket")).status_code == 401
            assert (await old.post("/api/v1/key-transfers")).status_code == 401
            identifier = (await new.post("/api/v1/key-transfers")).json()["transfer_id"]
            path = f"/api/v1/key-transfers/{identifier}"
            bundle = encrypt_bundle(UUID(identifier), b"local private bundle")
            assert (await old.put(path, json=bundle.upload())).status_code == 204
            assert (await old.get(path)).status_code == 401
            downloaded = (await new.get(path)).json()["encrypted_blob"]
            assert decrypt_bundle(UUID(identifier), downloaded, bundle.qr_payload()) == b"local private bundle"
            assert (await new.delete(path)).status_code == 204
            assert (await old.put(path, json=bundle.upload())).status_code == 401
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT revoked_at IS NOT NULL FROM auth_sessions WHERE id=%s", (sessions[0],)).fetchone() == (True,)
                assert db.execute("SELECT count(*) FROM auth_sessions WHERE user_id=%s AND revoked_at IS NULL", (users[0],)).fetchone() == (1,)
                assert db.execute("SELECT count(*) FROM transfer_upload_grants WHERE user_id=%s", (users[0],)).fetchone() == (0,)
    asyncio.run(run())


def test_new_login_and_expiration_invalidate_old_upload_grant(n4: tuple) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as old, client(settings, tokens[0]) as new:
            pair = (await new.post("/api/v1/auth/exchange", json={"bootstrap_token": bootstrap(users[0])})).json()
            new.headers["Authorization"] = "Bearer " + pair["access_token"]
            identifier = (await new.post("/api/v1/key-transfers")).json()["transfer_id"]
            path = f"/api/v1/key-transfers/{identifier}"
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                db.execute("UPDATE transfer_upload_grants SET expires_at=clock_timestamp() WHERE user_id=%s", (users[0],))
            assert (await old.put(path, json={"encrypted_blob": "YQ"})).status_code == 401
            again = (await new.post("/api/v1/auth/exchange", json={"bootstrap_token": bootstrap(users[0])})).json()
            async with client(settings, again["access_token"]) as latest:
                assert (await latest.get(path)).status_code == 404
                assert (await old.put(path, json={"encrypted_blob": "YQ"})).status_code == 401
                next_id = (await latest.post("/api/v1/key-transfers")).json()["transfer_id"]
                assert (await new.put(f"/api/v1/key-transfers/{next_id}", json={"encrypted_blob": "YQ"})).status_code == 204
                assert (await latest.post("/api/v1/auth/logout")).status_code == 204
                assert (await new.put(f"/api/v1/key-transfers/{next_id}", json={"encrypted_blob": "YQ"})).status_code == 401
    asyncio.run(run())


def replay_item(identifier: UUID, sequence: int) -> dict:
    return {"replay_id": str(identifier), "sequence": sequence, "original_message_id": None,
            "original_timestamp": None, "protocol_version": 1,
            "ciphertext": encode_binary(b"ciphertext" * 4), "crypto_meta": encode_binary(b"m" * 56)}


def test_replay_two_instances_order_end_count_and_no_persistence(n4: tuple) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with server(settings) as (url_a, _), server(settings) as (url_b, _), \
                client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest, \
                contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest, "stored"))["id"]
            vote = (await host.post(f"/api/v1/conversations/{conversation}/accept")).json()["vote"]["id"]
            expire(vote)
            a, b = await open_socket(stack, url_a, host), await open_socket(stack, url_b, guest)
            identifier = uuid4()

            async def send(kind, payload):
                await a.send(json.dumps(frame("recovery.replay." + kind, UUID(conversation), payload)))

            await send("begin", {"replay_id": str(identifier)})
            assert (await received(b, "recovery.replay.begin"))["payload"]["replay_id"] == str(identifier)
            await send("item", replay_item(identifier, 1))
            assert json.loads(await a.recv())["payload"]["code"] == "REPLAY_SEQUENCE_INVALID"
            for i in range(3):
                await send("item", replay_item(identifier, i))
                assert (await received(b, "recovery.replay.item"))["payload"]["sequence"] == i
            await send("end", {"replay_id": str(identifier), "item_count": 2})
            assert json.loads(await a.recv())["payload"]["code"] == "REPLAY_SEQUENCE_INVALID"
            await send("end", {"replay_id": str(identifier), "item_count": 3})
            assert (await received(b, "recovery.replay.end"))["payload"]["item_count"] == 3
            await send("item", replay_item(identifier, 3))
            assert json.loads(await a.recv())["payload"]["code"] == "REPLAY_EXPIRED"
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT count(*) FROM message_events WHERE conversation_id=%s", (conversation,)).fetchone() == (0,)
                assert db.execute("SELECT count(*) FROM push_jobs WHERE conversation_id=%s", (conversation,)).fetchone() == (0,)
            async with redis_connection() as redis:
                raw = await redis.get(f"recovery:replay:{identifier}")
                assert b"ciphertext" not in raw and b"crypto_meta" not in raw
                assert 0 < await redis.ttl(f"recovery:replay:{identifier}") <= 900
    asyncio.run(run())


def test_replay_rate_limit_offline_and_connection_binding(n4: tuple) -> None:
    settings, _, _, tokens = n4
    settings = settings.model_copy(update={"rate_limits": settings.rate_limits.model_copy(update={
        "replay": RateRule(limit=2, seconds=1)})})

    async def run() -> None:
        async with server(settings) as (url, _), client(settings, tokens[0]) as host, \
                client(settings, tokens[1]) as guest, client(settings, tokens[2]) as outsider, \
                contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest))["id"]
            vote = (await host.post(f"/api/v1/conversations/{conversation}/accept")).json()["vote"]["id"]
            expire(vote)
            a = await open_socket(stack, url, host)
            identifier = uuid4()
            begin = frame("recovery.replay.begin", UUID(conversation), {"replay_id": str(identifier)})
            await a.send(json.dumps(begin))
            assert json.loads(await a.recv())["payload"]["code"] == "RECIPIENT_DISCONNECTED"
            b, other, stranger = await open_socket(stack, url, guest), await open_socket(stack, url, host), await open_socket(stack, url, outsider)
            await a.send(json.dumps(begin))
            await received(b, "recovery.replay.begin")
            for i in range(2):
                await a.send(json.dumps(frame("recovery.replay.item", UUID(conversation), replay_item(identifier, i))))
                await received(b, "recovery.replay.item")
            await a.send(json.dumps(frame("recovery.replay.item", UUID(conversation), replay_item(identifier, 2))))
            assert json.loads(await a.recv())["payload"]["code"] == "RATE_LIMITED"
            await other.send(json.dumps(frame("recovery.replay.end", UUID(conversation), {"replay_id": str(identifier), "item_count": 2})))
            assert json.loads(await other.recv())["payload"]["code"] == "REPLAY_NOT_FOUND"
            await stranger.send(json.dumps(begin))
            assert json.loads(await stranger.recv())["payload"]["code"] == "CONVERSATION_NOT_FOUND"
            await b.close()
            await a.send(json.dumps(frame("recovery.replay.end", UUID(conversation), {"replay_id": str(identifier), "item_count": 2})))
            assert json.loads(await a.recv())["payload"]["code"] == "TEMPORARY_UNAVAILABLE"
    asyncio.run(run())
