"""N3 sobre servicios reales; fixtures SQL hasta que N4 publique invitaciones."""

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from chat.app import create_app
from chat.config import Settings, read_secret
from chat.identity_crypto import Tokens
from chat.persistence import MessageWrite, transaction
from chat.protocol import decode_binary, encode_binary
from chat_client.crypto import EncryptedMessage, KeyPair, decrypt_text, encrypt_text

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL/Redis aislados y RUN_INTEGRATION=1",
)]


@pytest.fixture
def resources_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple]:
    database, redis = os.environ["DATABASE_URL_FILE"], os.environ["REDIS_URL_FILE"]
    settings = request.getfixturevalue("local_settings").model_copy(update={"trusted_proxy_host": ""})
    monkeypatch.setenv("DATABASE_URL_FILE", database)
    monkeypatch.setenv("REDIS_URL_FILE", redis)
    monkeypatch.setenv("APP_ENV", "test")
    users, sessions, conversations = [uuid4() for _ in range(3)], [uuid4() for _ in range(3)], [uuid4() for _ in range(4)]
    with psycopg.connect(read_secret("DATABASE_URL")) as connection:
        for user, session in zip(users, sessions, strict=True):
            connection.execute("INSERT INTO users(id,nick,email,memory_hash) VALUES (%s,%s,%s,'fixture')",
                               (user, "n3_" + user.hex[:12], user.hex + "@example.com"))
            connection.execute("""INSERT INTO auth_sessions(id,user_id,refresh_token_hash,token_family_id,expires_at)
                VALUES (%s,%s,%s,%s,now()+interval '1 day')""", (session, user, uuid4().hex, uuid4()))
        for conversation in conversations:
            connection.execute("INSERT INTO conversations(id,mode,updated_at) VALUES (%s,'stored',%s)",
                               (conversation, datetime(2026, 1, 1, tzinfo=UTC)))
            for user, role in zip(users[:2], ("host", "guest"), strict=True):
                connection.execute("INSERT INTO conversation_members(conversation_id,user_id,role) VALUES (%s,%s,%s)",
                                   (conversation, user, role))
    tokens = [Tokens(settings).access(user, sid) for user, sid in zip(users, sessions, strict=True)]
    yield settings, users, sessions, conversations, tokens
    with psycopg.connect(read_secret("DATABASE_URL")) as connection:
        connection.execute("DELETE FROM conversations WHERE id=ANY(%s)", (conversations,))
        connection.execute("DELETE FROM users WHERE id=ANY(%s)", (users,))


def client_for(settings: Settings, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://test",
                             headers={"Authorization": "Bearer " + token})


def key_body(key: KeyPair) -> dict:
    return {"public_key": encode_binary(key.public_key), "protocol_version": 1}


def test_key_lifecycle_permissions_privacy_and_session_revocation(resources_env: tuple) -> None:
    settings, users, sessions, conversations, tokens = resources_env
    first, second = KeyPair.generate(), KeyPair.generate()

    async def run() -> None:
        async with client_for(settings, tokens[0]) as host, client_for(settings, tokens[1]) as guest, \
                client_for(settings, tokens[2]) as stranger:
            assert (await host.post("/api/v1/users/me/keys/rotate", json=key_body(first))).status_code == 404
            created = await host.put("/api/v1/users/me/keys", json=key_body(first))
            assert created.status_code == 200, created.text
            assert set(created.json()) == {"public_key", "protocol_version", "updated_at"}
            assert created.json()["updated_at"].endswith("Z")
            assert (await host.put("/api/v1/users/me/keys", json=key_body(first))).json() == created.json()
            assert (await guest.get(f"/api/v1/users/{users[0]}/keys")).json() == created.json()
            assert (await guest.get(f"/api/v1/users/{users[0]}")).status_code == 404
            assert (await stranger.get(f"/api/v1/users/{users[0]}/keys")).status_code == 404
            for client in (host, guest):
                pending = await client.get(f"/api/v1/conversations/{conversations[0]}")
                assert pending.status_code == 200 and pending.json()["peer"] is None
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("UPDATE conversations SET status='closed' WHERE id=%s", (conversations[0],))
            assert (await host.get(f"/api/v1/conversations/{conversations[0]}")).json()["peer"] is None
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("UPDATE conversations SET accepted_at=clock_timestamp() WHERE id=%s", (conversations[0],))
            peer = (await host.get(f"/api/v1/conversations/{conversations[0]}")).json()["peer"]
            assert peer == {"id": str(users[1]), "nick": "n3_" + users[1].hex[:12]}
            rotated = await host.post("/api/v1/users/me/keys/rotate", json=key_body(second))
            assert rotated.status_code == 200 and rotated.json()["public_key"] == key_body(second)["public_key"]
            assert rotated.json()["updated_at"] != created.json()["updated_at"]
            assert (await host.post("/api/v1/users/me/keys/rotate", json=key_body(second))).json() == rotated.json()
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                assert connection.execute("SELECT count(*) FROM user_keys WHERE user_id=%s", (users[0],)).fetchone() == (1,)
                connection.execute("UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE id=%s", (sessions[0],))
            assert (await host.put("/api/v1/users/me/keys", json=key_body(first))).status_code == 401
            assert (await host.get("/api/v1/conversations")).json()["error"]["code"] == "SESSION_REVOKED"
    asyncio.run(run())


def test_key_concurrent_replacement_and_strict_http_contract(resources_env: tuple) -> None:
    settings, users, _, _, tokens = resources_env

    async def run() -> None:
        async with client_for(settings, tokens[0]) as client:
            candidates = [key_body(KeyPair.generate()) for _ in range(5)]
            results = await asyncio.gather(*(client.put("/api/v1/users/me/keys", json=body) for body in candidates))
            assert [result.status_code for result in results] == [200] * 5
            final = (await client.get(f"/api/v1/users/{users[0]}/keys")).json()
            assert final in [result.json() for result in results]
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                assert connection.execute("SELECT count(*) FROM user_keys WHERE user_id=%s", (users[0],)).fetchone() == (1,)
            for field, value, code in (("protocol_version", 2, "UNSUPPORTED_PROTOCOL_VERSION"),
                                        ("protocol_version", True, "VALIDATION_ERROR"),
                                        ("public_key", encode_binary(b"x" * 31), "VALIDATION_ERROR"),
                                        ("public_key", encode_binary(b"x" * 33), "VALIDATION_ERROR"),
                                        ("private_key", "PRIVATE_SECRET", "UNKNOWN_FIELD")):
                response = await client.put("/api/v1/users/me/keys", json={**candidates[0], field: value})
                assert response.status_code == 422 and response.json()["error"]["code"] == code
                assert "PRIVATE_SECRET" not in response.text
            assert (await client.get(f"/api/v1/users/{str(users[0]).upper()}/keys")).status_code == 422
    asyncio.run(run())


def test_conversation_cursor_ties_and_no_horizontal_access(resources_env: tuple) -> None:
    settings, _, _, conversations, tokens = resources_env

    async def run() -> None:
        async with client_for(settings, tokens[0]) as host, client_for(settings, tokens[2]) as stranger:
            first = await host.get("/api/v1/conversations?limit=2")
            assert first.status_code == 200, first.text
            assert first.json()["next_cursor"]
            second = await host.get("/api/v1/conversations", params={"limit": 2, "cursor": first.json()["next_cursor"]})
            assert second.status_code == 200 and second.json()["next_cursor"] is None
            identifiers = [item["id"] for page in (first, second) for item in page.json()["items"]]
            assert identifiers == [str(value) for value in sorted(conversations, reverse=True)]
            assert all(item["peer"] is None for item in first.json()["items"])
            empty = await stranger.get("/api/v1/conversations", params={"cursor": first.json()["next_cursor"]})
            assert empty.json() == {"items": [], "next_cursor": None}
            for suffix in ("", "/messages"):
                assert (await stranger.get(f"/api/v1/conversations/{conversations[0]}{suffix}")).status_code == 404
            for query, status, code in (("cursor=broken", 400, "INVALID_CURSOR"),
                                        ("cursor=", 400, "INVALID_CURSOR"),
                                        ("limit=101", 422, "VALIDATION_ERROR"),
                                        ("offset=1", 422, "UNKNOWN_FIELD")):
                error = await host.get("/api/v1/conversations?" + query)
                assert error.status_code == status and error.json()["error"]["code"] == code
    asyncio.run(run())


def test_encrypted_history_pagination_expiry_mode_and_membership(resources_env: tuple,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    settings, users, _, conversations, tokens = resources_env
    conversation, ephemeral = conversations[:2]
    sender, recipient = KeyPair.generate(), KeyPair.generate()
    expected = {}
    sensitive = []

    async def run() -> None:
        with psycopg.connect(read_secret("DATABASE_URL")) as connection:
            connection.execute("UPDATE conversations SET mode='ephemeral' WHERE id=%s", (ephemeral,))
        async with client_for(settings, tokens[0]) as host, client_for(settings, tokens[1]) as guest:
            await host.put("/api/v1/users/me/keys", json=key_body(recipient))
            await guest.put("/api/v1/users/me/keys", json=key_body(sender))
            assert (await host.get(f"/api/v1/users/{users[1]}/keys")).status_code == 200
            public = (await guest.get(f"/api/v1/users/{users[0]}/keys")).json()["public_key"]
            for index in range(5):
                identifier = uuid4()
                text = f"Mensaje privado {index} 🙂"
                encrypted = encrypt_text(text, sender, decode_binary(public, maximum=32))
                expected[identifier] = text
                sensitive.extend((text, encode_binary(encrypted.ciphertext), encode_binary(encrypted.crypto_meta)))
                async with transaction() as unit:
                    await unit.messages.record(MessageWrite(identifier, conversation, users[1], 1,
                                                             encrypted.crypto_meta, encrypted.ciphertext))
            expired = next(iter(expected))
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("UPDATE message_events SET sent_at=statement_timestamp() WHERE conversation_id=%s", (conversation,))
                connection.execute("UPDATE messages SET content_expires_at=clock_timestamp() WHERE id=%s", (expired,))
            path = f"/api/v1/conversations/{conversation}/messages"
            first = await host.get(path, params={"limit": 2})
            assert first.status_code == 200, first.text
            cursor = first.json()["next_cursor"]
            second = await host.get(path, params={"limit": 2, "cursor": cursor})
            assert second.status_code == 200 and second.json()["next_cursor"] is None
            rows = first.json()["items"] + second.json()["items"]
            assert [row["message_id"] for row in rows] == [str(key) for key in sorted(set(expected) - {expired}, reverse=True)]
            for row in rows:
                assert row["sender_role"] == "guest" and row["is_grace_message"]
                assert "sender_id" not in row and "sender_user_id" not in row
                encrypted = EncryptedMessage(decode_binary(row["ciphertext"], maximum=4096),
                                             decode_binary(row["crypto_meta"], maximum=56))
                assert decrypt_text(encrypted, recipient) == expected[UUID(row["message_id"])]
                assert row["sent_at"].endswith("Z")
            error = await host.get(f"/api/v1/conversations/{ephemeral}/messages")
            assert error.status_code == 409 and error.json()["error"]["code"] == "HISTORY_NOT_STORED"
            assert (await host.get(f"/api/v1/conversations/{conversations[2]}/messages", params={"cursor": cursor})).status_code == 400
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("UPDATE conversation_members SET membership_status='left' WHERE user_id=%s", (users[0],))
            assert (await host.get(path, params={"cursor": cursor})).status_code == 404
            assert (await host.get(f"/api/v1/users/{users[1]}/keys")).status_code == 404
            assert (await host.get("/api/v1/conversations")).json() == {"items": [], "next_cursor": None}
    asyncio.run(run())
    logs = capsys.readouterr().out
    assert all(value not in logs for value in sensitive)
