"""N4 con API, PostgreSQL y Redis reales; ninguna conversación sembrada por fixture."""

import asyncio
import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from chat.app import create_app
from chat.config import RateRule, Settings, read_secret
from chat.conversations import invitation_secret
from chat.identity_crypto import Tokens
from chat.invitation_codes import decode_code, encode_code
from chat.persistence import ConversationUnavailable, MessageWrite, transaction
from chat.protocol import encode_binary

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL/Redis aislados y RUN_INTEGRATION=1",
)]


@pytest.fixture
def n4(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple]:
    database, redis = os.environ["DATABASE_URL_FILE"], os.environ["REDIS_URL_FILE"]
    settings = request.getfixturevalue("local_settings").model_copy(update={"trusted_proxy_host": ""})
    monkeypatch.setenv("DATABASE_URL_FILE", database)
    monkeypatch.setenv("REDIS_URL_FILE", redis)
    monkeypatch.setenv("APP_ENV", "test")
    users, sessions = [uuid4() for _ in range(3)], [uuid4() for _ in range(3)]
    with psycopg.connect(read_secret("DATABASE_URL")) as connection:
        for user, sid in zip(users, sessions, strict=True):
            connection.execute("""INSERT INTO users(id,nick,email,memory_hash,email_verified)
                VALUES (%s,%s,%s,'fixture',true)""", (user, "n4_" + user.hex[:12], user.hex + "@example.com"))
            connection.execute("""INSERT INTO auth_sessions(id,user_id,refresh_token_hash,token_family_id,expires_at)
                VALUES (%s,%s,%s,%s,now()+interval '1 day')""", (sid, user, uuid4().hex, uuid4()))
            connection.execute("INSERT INTO user_keys(user_id,public_key) VALUES (%s,%s)",
                               (user, encode_binary(user.bytes * 2)))
    tokens = [Tokens(settings).access(user, sid) for user, sid in zip(users, sessions, strict=True)]
    yield settings, users, sessions, tokens
    with psycopg.connect(read_secret("DATABASE_URL")) as connection:
        connection.execute("""DELETE FROM conversations WHERE id IN
            (SELECT conversation_id FROM conversation_members WHERE user_id=ANY(%s))""", (users,))
        connection.execute("DELETE FROM users WHERE id=ANY(%s)", (users,))


def client(settings: Settings, token: str, ip: str = "127.0.0.1") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings), client=(ip, 123)),
                             base_url="http://test", headers={"Authorization": "Bearer " + token})


async def contact(host: httpx.AsyncClient, guest: httpx.AsyncClient, mode: str = "ephemeral") -> dict:
    codes = await host.get("/api/v1/invitations/me")
    assert codes.status_code == 200, codes.text
    response = await guest.post("/api/v1/invitations/redeem", json={"code": codes.json()[mode + "_code"]})
    assert response.status_code == 201, response.text
    return response.json()


def test_lifecycle_privacy_vote_upgrade_close_and_new_contact(n4: tuple, capsys: pytest.CaptureFixture[str]) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest, \
                client(settings, tokens[2]) as outsider:
            codes = (await host.get("/api/v1/invitations/me")).json()
            assert codes == (await host.get("/api/v1/invitations/me")).json()
            assert set(codes) == {"ephemeral_code", "stored_code", "generation", "created_at"}
            conversation = await contact(host, guest)
            path = "/api/v1/conversations/" + conversation["id"]
            assert conversation["role"] == "guest" and conversation["peer"] is None
            assert conversation["host_public_key"] == encode_binary(users[0].bytes * 2)
            assert not any(str(user) in str(conversation) for user in users)
            for viewer in (host, guest):
                assert (await viewer.get(path)).json()["peer"] is None
            assert (await guest.get(f"/api/v1/users/{users[0]}")).status_code == 404
            for suffix in ("accept", "upgrade", "leave"):
                assert (await outsider.post(path + "/" + suffix)).status_code == 404
            assert (await outsider.delete(path)).status_code == 404
            assert (await guest.post(path + "/accept")).json()["error"]["code"] == "NOT_CONVERSATION_HOST"
            assert (await host.post(path + "/upgrade")).json()["error"]["code"] == "CONVERSATION_NOT_ACTIVE"
            accepted = await host.post(path + "/accept")
            assert accepted.status_code == 200, accepted.text
            body = accepted.json()
            assert body["conversation"]["peer"]["id"] == str(users[1])
            vote = body["vote"]
            assert vote["eligible_members"] == 2 and vote["status"] == "open" and vote["my_vote"] is None
            assert vote["yes_votes"] == vote["no_votes"] == 0
            assert (datetime.fromisoformat(vote["expires_at"]) -
                    datetime.fromisoformat(body["conversation"]["accepted_at"])).total_seconds() == 30
            assert (await host.post(path + "/accept")).json() == body
            assert (await guest.get(path)).json()["peer"]["id"] == str(users[0])
            assert (await guest.post(path + "/upgrade")).status_code == 403
            upgraded = await host.post(path + "/upgrade")
            assert upgraded.status_code == 200 and upgraded.json()["mode"] == "stored"
            assert (await host.post(path + "/upgrade")).json() == upgraded.json()
            assert (await guest.delete(path)).status_code == 204
            closed = (await host.get(path)).json()
            assert closed["status"] == "closed" and closed["closed_at"]
            assert (await host.delete(path)).status_code == 204
            assert (await host.get(path)).json() == closed
            for suffix in ("accept", "upgrade"):
                assert (await host.post(path + "/" + suffix)).json()["error"]["code"] == "CONVERSATION_CLOSED"
            second = await contact(host, guest, "stored")
            assert second["id"] != conversation["id"] and second["status"] == "pending"
            assert second["mode"] == "stored" and second["peer"] is None
            logs = capsys.readouterr().out
            assert codes["ephemeral_code"] not in logs and codes["stored_code"] not in logs
    asyncio.run(run())


def test_regeneration_concurrency_revocation_and_atomic_rollback(n4: tuple) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            first = await asyncio.gather(*(host.get("/api/v1/invitations/me") for _ in range(6)))
            assert all(response.status_code == 200 and response.json() == first[0].json() for response in first)
            old = first[0].json()
            responses = await asyncio.gather(*(host.post("/api/v1/invitations/regenerate") for _ in range(5)))
            assert all(response.status_code == 201 for response in responses)
            assert sorted(response.json()["generation"] for response in responses) == list(range(2, 7))
            for field in ("ephemeral_code", "stored_code"):
                error = await guest.post("/api/v1/invitations/redeem", json={"code": old[field]})
                assert error.status_code == 410 and error.json()["error"]["code"] == "INVITATION_REVOKED"
            current = (await host.get("/api/v1/invitations/me")).json()
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                assert connection.execute("SELECT count(*) FROM invitations WHERE creator_user_id=%s AND status='active'",
                                          (users[0],)).fetchone() == (1,)
                connection.execute("UPDATE invitations SET generation=4294967295 WHERE creator_user_id=%s AND status='active'",
                                   (users[0],))
            full = (await host.get("/api/v1/invitations/me")).json()
            assert (await host.post("/api/v1/invitations/regenerate")).status_code == 409
            assert (await host.get("/api/v1/invitations/me")).json() == full
            assert current != full
            assert (await guest.post("/api/v1/invitations/redeem", json={"code": full["stored_code"]})).status_code == 201
    asyncio.run(run())


def test_verification_keys_sessions_validation_and_missing_invitation(n4: tuple) -> None:
    settings, users, sessions, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            code = (await host.get("/api/v1/invitations/me")).json()["ephemeral_code"]
            assert (await host.post("/api/v1/invitations/redeem", json={"code": code})).status_code == 400
            assert (await guest.post("/api/v1/invitations/redeem", json={"code": "broken"})).status_code == 400
            assert (await guest.post("/api/v1/invitations/redeem", json={"code": code, "mode": "stored"})).status_code == 422
            token = decode_code(code, invitation_secret())
            for changed in (replace(token, id=uuid4()), replace(token, generation=2),
                            replace(token, created_at=token.created_at + 1)):
                assert (await guest.post("/api/v1/invitations/redeem",
                                        json={"code": encode_code(changed, invitation_secret())})).status_code == 410
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("DELETE FROM user_keys WHERE user_id=%s", (users[0],))
            assert (await guest.post("/api/v1/invitations/redeem", json={"code": code})).status_code == 409
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                assert connection.execute("SELECT use_count FROM invitations WHERE id=%s", (token.id,)).fetchone() == (0,)
                connection.execute("UPDATE users SET email_verified=false WHERE id=%s", (users[1],))
            for method, path, kwargs in (("GET", "/invitations/me", {}), ("POST", "/invitations/regenerate", {}),
                                         ("POST", "/invitations/redeem", {"json": {"code": code}})):
                error = await guest.request(method, "/api/v1" + path, **kwargs)
                assert error.status_code == 403 and error.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE id=%s", (sessions[0],))
            assert (await host.get("/api/v1/invitations/me")).status_code == 401
            async with client(settings, "invalid") as invalid:
                assert (await invalid.get("/api/v1/invitations/me")).status_code == 401
    asyncio.run(run())


def test_accept_concurrency_and_leave_preserve_frozen_electorate(n4: tuple) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            pending = await contact(host, guest)
            path = "/api/v1/conversations/" + pending["id"]
            results = await asyncio.gather(*(host.post(path + "/accept") for _ in range(5)))
            assert all(response.status_code == 200 for response in results)
            assert len({response.json()["vote"]["id"] for response in results}) == 1
            vote = results[0].json()["vote"]
            assert (await guest.post(path + "/leave")).status_code == 204
            assert (await guest.post(path + "/leave")).status_code == 204
            assert (await guest.get(path)).status_code == 404
            assert (await guest.get(path + "/messages")).status_code == 404
            assert (await guest.get(f"/api/v1/users/{users[0]}/keys")).status_code == 404
            assert (await guest.get("/api/v1/conversations")).json()["items"] == []
            assert (await host.get(path)).json()["status"] == "closed"
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                electorate = connection.execute("SELECT user_id FROM vote_eligible_members WHERE vote_id=%s",
                                                (vote["id"],)).fetchall()
                assert {row[0] for row in electorate} == set(users[:2])
                assert connection.execute("SELECT eligible_members FROM votes WHERE id=%s", (vote["id"],)).fetchone() == (2,)
    asyncio.run(run())


def test_upgrade_and_close_serialize_with_message_writes(n4: tuple) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            pending = await contact(host, guest)
            identifier = UUID(pending["id"])
            path = "/api/v1/conversations/" + str(identifier)
            before = MessageWrite(uuid4(), identifier, users[1], 1, b"m" * 56, b"payload")
            async with transaction() as unit:
                await unit.messages.record(before)
            assert (await host.post(path + "/accept")).status_code == 200
            with pytest.raises(ConversationUnavailable):
                async with transaction() as unit:
                    await unit.messages.record(replace(before, id=uuid4()))
            # Simular el deadline transcurrido; N6 resolverá el resultado del voto.
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("UPDATE votes SET expires_at=clock_timestamp() WHERE conversation_id=%s", (identifier,))
            after = replace(before, id=uuid4())
            async with transaction() as unit:
                await unit.conversations.lock(identifier)
                switching = asyncio.create_task(host.post(path + "/upgrade"))
                await asyncio.sleep(0.1)
                assert not switching.done()
                await unit.messages.record(after)
            assert (await switching).status_code == 200
            stored = replace(before, id=uuid4())
            async with transaction() as unit:
                await unit.messages.record(stored)
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                assert connection.execute("SELECT id FROM messages WHERE id=ANY(%s)",
                                          ([before.id, after.id, stored.id],)).fetchall() == [(stored.id,)]
            async with transaction() as unit:
                await unit.conversations.lock(identifier)
                closing = asyncio.create_task(guest.delete(path))
                await asyncio.sleep(0.1)
                assert not closing.done()
                await unit.messages.record(replace(before, id=uuid4()))
            assert (await closing).status_code == 204
            with pytest.raises(ConversationUnavailable):
                async with transaction() as unit:
                    await unit.messages.record(replace(before, id=uuid4()))
            async with transaction() as unit:
                assert not (await unit.messages.record(stored)).created
    asyncio.run(run())


def test_redeem_user_and_ip_quotas(n4: tuple) -> None:
    settings, _, _, tokens = n4
    settings = settings.model_copy(update={"rate_limits": settings.rate_limits.model_copy(update={
        "redeem_user": RateRule(limit=1, seconds=60), "redeem_ip": RateRule(limit=1, seconds=60),
    })})

    async def run() -> None:
        async with client(settings, tokens[0], "192.0.2.64") as first, \
                client(settings, tokens[1], "192.0.2.64") as same_ip:
            assert (await first.post("/api/v1/invitations/redeem", json={"code": "invalid"})).status_code == 400
            assert (await first.post("/api/v1/invitations/redeem", json={"code": "invalid"})).status_code == 429
            assert (await same_ip.post("/api/v1/invitations/redeem", json={"code": "invalid"})).status_code == 429
    asyncio.run(run())


def test_redeem_races_regeneration_and_mutual_contacts(n4: tuple) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            codes = (await host.get("/api/v1/invitations/me")).json()
            old = decode_code(codes["stored_code"], invitation_secret())
            async with transaction() as unit:
                await unit.connection.execute("SELECT id FROM users WHERE id=%s FOR UPDATE", (users[0],))
                redeem = asyncio.create_task(guest.post("/api/v1/invitations/redeem",
                                                       json={"code": codes["stored_code"]}))
                rotate = asyncio.create_task(host.post("/api/v1/invitations/regenerate"))
                await asyncio.sleep(0.1)
                assert not redeem.done() and not rotate.done()
            redeemed, regenerated = await asyncio.gather(redeem, rotate)
            assert regenerated.status_code == 201
            assert redeemed.status_code in (201, 410), redeemed.text
            assert (await guest.post("/api/v1/invitations/redeem", json={"code": codes["stored_code"]})).status_code == 410
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                uses = int(redeemed.status_code == 201)
                assert connection.execute("SELECT status,use_count FROM invitations WHERE id=%s",
                                          (old.id,)).fetchone() == ("revoked", uses)
                assert connection.execute("SELECT count(*) FROM conversations WHERE created_from_invitation_id=%s",
                                          (old.id,)).fetchone() == (uses,)
            # Ambos son a la vez host y guest; los locks de usuarios deben tener orden común.
            other = (await guest.get("/api/v1/invitations/me")).json()
            mutual = await asyncio.gather(
                host.post("/api/v1/invitations/redeem", json={"code": other["stored_code"]}),
                guest.post("/api/v1/invitations/redeem", json={"code": regenerated.json()["stored_code"]}),
            )
            assert [response.status_code for response in mutual] == [201, 201]
    asyncio.run(run())


def test_pending_close_never_reveals_peers_and_deleted_guest_cannot_be_accepted(n4: tuple) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            first = await contact(host, guest)
            path = "/api/v1/conversations/" + first["id"]
            assert (await guest.delete(path)).status_code == 204
            for viewer in (host, guest):
                closed = (await viewer.get(path)).json()
                assert closed["peer"] is None and closed["accepted_at"] is None
            assert (await host.get(f"/api/v1/users/{users[1]}")).status_code == 404
            second = await contact(host, guest)
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                connection.execute("DELETE FROM users WHERE id=%s", (users[1],))
            path = "/api/v1/conversations/" + second["id"]
            assert (await host.post(path + "/accept")).status_code == 409
            assert (await host.get(path)).json()["status"] == "pending"
            with psycopg.connect(read_secret("DATABASE_URL")) as connection:
                assert connection.execute("SELECT count(*) FROM votes WHERE conversation_id=%s",
                                          (second["id"],)).fetchone() == (0,)
    asyncio.run(run())
