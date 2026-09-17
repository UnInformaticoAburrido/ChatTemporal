"""Identidad con PostgreSQL/Redis reales y buzón de prueba sin envíos externos.

ASGITransport prueba los handlers; el lifespan/validación de secrets se prueba
por separado en test_infrastructure. No se simula la persistencia ni Redis.
"""

import asyncio
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import jwt
import psycopg
import pytest
from redis import Redis

from chat.app import create_app
from chat.config import RateRule, Settings, read_secret
from chat.errors import APIError, unavailable
from chat.identity import Identity
from chat.identity_crypto import token_hash
from chat.identity_dto import Registration
from chat.identity_redis import IdentityRedis

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL/Redis aislados y RUN_INTEGRATION=1",
)]


class Mailbox:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.fail = False

    async def verification(self, email: str, token: str) -> None:
        if self.fail:
            raise unavailable()
        self.messages.append((email, token))


@pytest.fixture
def identity_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Settings, Mailbox, str]]:
    # Generar claves temporales de verdad, preservando los DSN del runner aislado.
    database, redis = os.environ["DATABASE_URL_FILE"], os.environ["REDIS_URL_FILE"]
    settings = request.getfixturevalue("local_settings")
    monkeypatch.setenv("DATABASE_URL_FILE", database)
    monkeypatch.setenv("REDIS_URL_FILE", redis)
    monkeypatch.setenv("APP_ENV", "test")
    settings = settings.model_copy(update={"trusted_proxy_host": ""})
    prefix = "n2_" + uuid4().hex[:12]
    yield settings, Mailbox(), prefix
    with psycopg.connect(read_secret("DATABASE_URL")) as connection:
        connection.execute("DELETE FROM users WHERE email LIKE %s", (prefix + "%@example.com",))


def client_for(settings: Settings, mailbox: Mailbox) -> httpx.AsyncClient:
    # Peer diferente por test; no borrar claves Redis que no sean de esta prueba.
    peer = f"198.18.{uuid4().int % 250}.{uuid4().int % 250}"
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings, mailer=mailbox), client=(peer, 1)),
                             base_url="http://test")


def bootstrap(user: UUID) -> str:
    stamp = int(time.time())
    key = Path(os.environ["BOOTSTRAP_PUBLIC_KEYS_DIR"]).parent / "bootstrap_local_private.pem"
    return jwt.encode({"sub": str(user), "iss": "main-app", "aud": "chat-api", "iat": stamp,
                       "nbf": stamp, "exp": stamp + 300, "jti": str(uuid4())}, key.read_bytes(),
                      algorithm="EdDSA", headers={"typ": "JWT", "kid": "main-local-1"})


def test_registration_verification_ticket_refresh_logout(identity_env: tuple[Settings, Mailbox, str],
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    settings, mailbox, prefix = identity_env

    async def run() -> None:
        async with client_for(settings, mailbox) as client:
            response = await client.post("/api/v1/users/register", json={"nick": prefix, "email": prefix + "@example.com"})
            assert response.status_code == 201, response.text
            body = response.json()
            assert len(body["recovery_mnemonic"].split()) == 24
            tokens = body["tokens"]
            client.headers["Authorization"] = "Bearer " + tokens["access_token"]
            me = await client.get("/api/v1/users/me")
            assert me.json() == body["user"] and "memory_hash" not in me.text
            assert not me.json()["email_verified"]
            assert (await client.post("/api/v1/auth/ws-ticket")).status_code == 403
            verification = mailbox.messages[-1][1]
            assert verification not in response.text
            assert (await client.post("/api/v1/users/verify-email", json={"token": verification})).status_code == 204
            assert (await client.post("/api/v1/users/verify-email", json={"token": verification})).status_code == 401
            ticket = await client.post("/api/v1/auth/ws-ticket")
            assert ticket.status_code == 201
            with Redis.from_url(read_secret("REDIS_URL")) as redis:
                key = "ws:ticket:" + token_hash(ticket.json()["ticket"]).hex()
                assert 0 < redis.ttl(key) <= 30
                assert json.loads(redis.get(key))["sid"] == tokens["sid"]
                redis.delete(key)
            refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
            assert refresh.status_code == 200
            assert refresh.json()["refresh_token"] != tokens["refresh_token"]
            assert refresh.json()["sid"] == tokens["sid"]
            client.headers["Authorization"] = "Bearer " + refresh.json()["access_token"]
            assert (await client.post("/api/v1/auth/logout")).status_code == 204
            assert (await client.get("/api/v1/users/me")).json()["error"]["code"] == "SESSION_REVOKED"
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                hashes = connection.execute("SELECT token_hash,used_at,revoked_at FROM auth_refresh_tokens WHERE session_id=%s",
                                            (tokens["sid"],)).fetchall()
                assert len(hashes) == 2 and all(len(row[0]) == 32 and row[2] is not None for row in hashes)
            logs = capsys.readouterr().out
            assert all(secret not in logs for secret in (body["recovery_mnemonic"], tokens["access_token"],
                                                         tokens["refresh_token"], verification))
    asyncio.run(run())


def test_concurrent_refresh_reuse_revokes_successful_replacement(identity_env: tuple[Settings, Mailbox, str]) -> None:
    settings, mailbox, prefix = identity_env

    async def run() -> None:
        service = Identity(settings, mailer=mailbox)
        registered = await service.register(Registration(nick=prefix, email=prefix + "@example.com"))
        with Redis.from_url(read_secret("REDIS_URL")) as redis:
            with redis.pubsub() as subscriber:
                subscriber.subscribe("auth:session_revoked")
                subscriber.get_message(timeout=1)
                results = await asyncio.gather(service.refresh(registered.tokens.refresh_token),
                                               service.refresh(registered.tokens.refresh_token), return_exceptions=True)
                success = [result for result in results if not isinstance(result, BaseException)]
                errors = [result for result in results if isinstance(result, APIError)]
                assert len(success) == 1 and [e.code for e in errors] == ["REFRESH_REUSE_DETECTED"]
                notice = subscriber.get_message(timeout=2)
                assert notice and json.loads(notice["data"])["sid"] == str(registered.tokens.sid)
        with pytest.raises(APIError, match="SESSION_REVOKED"):
            await service.authenticate(success[0].access_token)
        with pytest.raises(APIError, match="SESSION_REVOKED"):
            await service.refresh(success[0].refresh_token)
        # Una nueva instancia (sin memoria de la anterior) sigue detectando reuse.
        with pytest.raises(APIError, match="REFRESH_REUSE_DETECTED"):
            await Identity(settings, mailer=mailbox).refresh(registered.tokens.refresh_token)
    asyncio.run(run())


def test_recovery_and_bootstrap_single_use_single_session(identity_env: tuple[Settings, Mailbox, str]) -> None:
    settings, mailbox, prefix = identity_env

    async def run() -> None:
        service = Identity(settings, mailer=mailbox)
        registered = await service.register(Registration(nick=prefix, email=prefix + "@example.com"))
        recovered = await service.recover(registered.user.email, "\t" + registered.recovery_mnemonic.replace(" ", "  "))
        with pytest.raises(APIError, match="SESSION_REVOKED"):
            await service.authenticate(registered.tokens.access_token)
        token = bootstrap(registered.user.id)
        results = await asyncio.gather(service.exchange(token), service.exchange(token), return_exceptions=True)
        assert sum(isinstance(result, APIError) for result in results) == 1
        with pytest.raises(APIError, match="SESSION_REVOKED"):
            await service.authenticate(recovered.access_token)
        with psycopg.connect(read_secret("DATABASE_URL")) as connection:
            assert connection.execute("SELECT count(*) FROM auth_sessions WHERE user_id=%s AND revoked_at IS NULL",
                                      (registered.user.id,)).fetchone() == (1,)
        for email, phrase in ((registered.user.email, "wrong phrase"), (prefix + "missing@example.com", "wrong phrase")):
            with pytest.raises(APIError, match="INVALID_RECOVERY_PHRASE"):
                await service.recover(email, phrase)
    asyncio.run(run())


def test_resend_email_change_expiration_and_account_deletion(identity_env: tuple[Settings, Mailbox, str]) -> None:
    settings, mailbox, prefix = identity_env

    async def run() -> None:
        async with client_for(settings, mailbox) as client:
            registered = (await client.post("/api/v1/users/register", json={"nick": prefix, "email": prefix + "@example.com"})).json()
            client.headers["Authorization"] = "Bearer " + registered["tokens"]["access_token"]
            first = mailbox.messages[-1][1]
            assert (await client.post("/api/v1/users/resend-verification")).status_code == 202
            second = mailbox.messages[-1][1]
            assert (await client.post("/api/v1/users/verify-email", json={"token": first})).status_code == 401
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("UPDATE email_verification_tokens SET expires_at=clock_timestamp() WHERE user_id=%s",
                                   (registered["user"]["id"],))
            assert (await client.post("/api/v1/users/verify-email", json={"token": second})).status_code == 401
            await client.post("/api/v1/users/resend-verification")
            await client.post("/api/v1/users/verify-email", json={"token": mailbox.messages[-1][1]})
            assert (await client.get("/api/v1/users/me")).json()["email_verified"]
            changed = await client.patch("/api/v1/users/me", json={"email": prefix + "new@example.com"})
            assert changed.status_code == 200 and not changed.json()["email_verified"]
            assert mailbox.messages[-1][0] == prefix + "new@example.com"
            assert (await client.post("/api/v1/auth/ws-ticket")).status_code == 403
            assert (await client.delete("/api/v1/users/me")).status_code == 204
            assert (await client.get("/api/v1/users/me")).status_code == 401
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                assert connection.execute("SELECT 1 FROM auth_sessions WHERE user_id=%s", (registered["user"]["id"],)).fetchone() is None
    asyncio.run(run())


def test_smtp_failure_rolls_back_and_unique_identity_errors(identity_env: tuple[Settings, Mailbox, str]) -> None:
    settings, mailbox, prefix = identity_env

    async def run() -> None:
        async with client_for(settings, mailbox) as client:
            mailbox.fail = True
            response = await client.post("/api/v1/users/register", json={"nick": prefix, "email": prefix + "@example.com"})
            assert response.status_code == 503
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                assert connection.execute("SELECT 1 FROM users WHERE nick=%s", (prefix,)).fetchone() is None
            mailbox.fail = False
            assert (await client.post("/api/v1/users/register", json={"nick": prefix, "email": prefix + "@example.com"})).status_code == 201
            nick = await client.post("/api/v1/users/register", json={"nick": prefix.upper(), "email": prefix + "other@example.com"})
            assert nick.status_code == 409 and nick.json()["error"]["code"] == "NICK_TAKEN"
            email = await client.post("/api/v1/users/register", json={"nick": prefix + "other", "email": prefix.upper() + "@example.com"})
            assert email.status_code == 409 and email.json()["error"]["code"] == "EMAIL_TAKEN"
    asyncio.run(run())


def test_atomic_rate_limits_and_redis_failure(identity_env: tuple[Settings, Mailbox, str],
                                            tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, mailbox, prefix = identity_env

    async def run() -> None:
        limiter = IdentityRedis()
        rule = RateRule(limit=5, seconds=900)
        results = await asyncio.gather(*(limiter.limit("test_window", prefix, rule) for _ in range(10)),
                                       return_exceptions=True)
        assert results.count(None) == 5
        assert sum(isinstance(result, APIError) and result.code == "RATE_LIMITED" for result in results) == 5
        burst = RateRule(limit=120, seconds=60, burst=3)
        for _ in range(3):
            await limiter.limit("test_bucket", prefix, burst)
        with pytest.raises(APIError, match="RATE_LIMITED"):
            await limiter.limit("test_bucket", prefix, burst)
        bad = tmp_path / "missing_redis_url"
        bad.write_text("unix:///tmp/chat-missing-" + uuid4().hex + ".sock")
        bad.chmod(0o600)
        monkeypatch.setenv("REDIS_URL_FILE", str(bad))
        with pytest.raises(APIError, match="TEMPORARY_UNAVAILABLE"):
            await limiter.consume_bootstrap(uuid4(), int(time.time()) + 60)
        async with client_for(settings, mailbox) as client:
            result = await client.post("/api/v1/users/register", json={"nick": prefix, "email": prefix + "@example.com"})
            assert result.status_code == 503
            assert mailbox.messages == []
    asyncio.run(run())


def test_profile_privacy_pending_closed_without_accept_and_revoked_session(identity_env: tuple[Settings, Mailbox, str]) -> None:
    settings, mailbox, prefix = identity_env

    async def run() -> None:
        service = Identity(settings, mailer=mailbox)
        users = [await service.register(Registration(nick=prefix + suffix, email=prefix + suffix + "@example.com"))
                 for suffix in ("a", "b")]
        principal = await service.authenticate(users[0].tokens.access_token)
        conversation = uuid4()
        with psycopg.connect(read_secret("DATABASE_URL")) as connection:
            connection.execute("INSERT INTO conversations(id,mode) VALUES (%s,'stored')", (conversation,))
            for registered, role in zip(users, ("host", "guest"), strict=True):
                connection.execute("INSERT INTO conversation_members(conversation_id,user_id,role) VALUES (%s,%s,%s)",
                                   (conversation, registered.user.id, role))
        try:
            for status in ("pending", "closed"):
                with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                    connection.execute("UPDATE conversations SET status=%s WHERE id=%s", (status, conversation))
                with pytest.raises(APIError, match="USER_NOT_FOUND"):
                    await service.public_user(principal, users[1].user.id)
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("UPDATE conversations SET accepted_at=clock_timestamp() WHERE id=%s", (conversation,))
            assert (await service.public_user(principal, users[1].user.id)).id == users[1].user.id
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("UPDATE auth_sessions SET expires_at=clock_timestamp() WHERE id=%s", (principal.sid,))
            with pytest.raises(APIError, match="SESSION_REVOKED"):
                await service.authenticate(users[0].tokens.access_token)
        finally:
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("DELETE FROM conversations WHERE id=%s", (conversation,))
    asyncio.run(run())


def test_revocation_survives_publish_failure(identity_env: tuple[Settings, Mailbox, str],
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    settings, mailbox, prefix = identity_env

    async def run() -> None:
        service = Identity(settings, mailer=mailbox)
        registered = await service.register(Registration(nick=prefix, email=prefix + "@example.com"))
        rotated = await service.refresh(registered.tokens.refresh_token)
        monkeypatch.setattr(service.redis, "revoke", AsyncMock(side_effect=unavailable()))
        with pytest.raises(APIError, match="TEMPORARY_UNAVAILABLE"):
            await service.refresh(registered.tokens.refresh_token)
        with pytest.raises(APIError, match="SESSION_REVOKED"):
            await service.authenticate(rotated.access_token)
    asyncio.run(run())


def test_http_rate_limit_and_database_failure(identity_env: tuple[Settings, Mailbox, str],
                                            tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, mailbox, prefix = identity_env
    limited = settings.model_copy(update={"rate_limits": settings.rate_limits.model_copy(update={
        "register_ip": RateRule(limit=1, seconds=3600),
    })})

    async def run() -> None:
        async with client_for(limited, mailbox) as client:
            first = await client.post("/api/v1/users/register", json={"nick": prefix, "email": prefix + "@example.com"})
            assert first.status_code == 201
            second = await client.post("/api/v1/users/register", json={"nick": prefix + "b", "email": prefix + "b@example.com"})
            assert second.status_code == 429 and second.json()["error"]["code"] == "RATE_LIMITED"
            assert len(mailbox.messages) == 1
            bad = tmp_path / "missing_postgres_url"
            bad.write_text("postgresql:///chat?host=/tmp/chat-missing-" + uuid4().hex)
            bad.chmod(0o600)
            with monkeypatch.context() as patch:
                patch.setenv("DATABASE_URL_FILE", str(bad))
                failed = await client.get("/api/v1/users/me", headers={
                    "Authorization": "Bearer " + first.json()["tokens"]["access_token"],
                })
                assert failed.status_code == 503
                assert failed.json()["error"]["code"] == "TEMPORARY_UNAVAILABLE"
    asyncio.run(run())
