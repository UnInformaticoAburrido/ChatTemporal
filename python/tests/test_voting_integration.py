"""N6 con REST, locks PostgreSQL, Redis, worker y WebSocket reales."""

import asyncio
import contextlib
import json
import os
import sys
from uuid import UUID, uuid4

import psycopg
import pytest
from redis.exceptions import RedisError
from test_conversations_integration import client, contact
from test_conversations_integration import n4 as n4
from test_websocket_integration import control, open_socket, received, sending, server

from chat.config import read_secret
from chat.persistence import MessageWrite, transaction
from chat.vote_store import VoteStore
from chat.voting import resolve_votes_once

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL/Redis aislados y RUN_INTEGRATION=1",
)]


def expire(identifier: str) -> None:
    with psycopg.connect(read_secret("DATABASE_URL")) as db:
        db.execute("UPDATE votes SET expires_at=clock_timestamp() WHERE id=%s", (identifier,))


async def accepted(host, guest, mode: str = "stored") -> tuple[str, str]:
    conversation = (await contact(host, guest, mode))["id"]
    response = await host.post(f"/api/v1/conversations/{conversation}/accept")
    assert response.status_code == 200, response.text
    return conversation, response.json()["vote"]["id"]


def test_ballot_contract_permissions_idempotency_and_expiry(n4: tuple) -> None:
    settings, users, sessions, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest, \
                client(settings, tokens[2]) as outsider:
            conversation, vote = await accepted(host, guest)
            path = f"/api/v1/votes/{vote}"
            for body in ({}, {"choice": "true"}, {"choice": 1}, {"choice": None}, {"choice": True, "user_id": str(users[1])}):
                assert (await host.post(path + "/ballots", json=body)).status_code == 422
            assert (await host.get("/api/v1/votes/not-a-uuid")).status_code == 422
            assert (await host.get(f"/api/v1/votes/{uuid4()}")).status_code == 404
            assert (await outsider.get(path)).status_code == 404
            assert (await outsider.post(path + "/ballots", json={"choice": True})).status_code == 404
            first = await host.post(path + "/ballots", json={"choice": True})
            assert first.status_code == 200, first.text
            assert first.json()["my_vote"] is True and first.json()["yes_votes"] == 1
            assert first.json()["status"] == "open" and first.json()["no_votes"] == 0
            assert (await guest.get(path)).json()["my_vote"] is None
            assert (await host.post(path + "/ballots", json={"choice": True})).json() == first.json()
            conflict = await host.post(path + "/ballots", json={"choice": False})
            assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "VOTE_CONFLICT"
            expire(vote)
            late = await guest.post(path + "/ballots", json={"choice": True})
            assert late.status_code == 410 and late.json()["error"]["code"] == "VOTE_EXPIRED"
            result = (await guest.get(path)).json()
            assert (result["status"], result["yes_votes"], result["no_votes"], result["my_vote"]) == ("rejected", 1, 1, None)
            retry = await host.post(path + "/ballots", json={"choice": True})
            assert retry.status_code == 200 and retry.json()["status"] == "rejected"
            assert (await host.post(f"/api/v1/conversations/{conversation}/accept")).json()["vote"] == retry.json()
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT count(*) FROM vote_ballots WHERE vote_id=%s", (vote,)).fetchone() == (1,)
                db.execute("UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE id=%s", (sessions[0],))
            assert (await host.get(path)).status_code == 401
    asyncio.run(run())


@pytest.mark.parametrize("approved", [False, True])
def test_stored_grace_history_purge_and_duplicate_without_resurrection(n4: tuple, approved: bool) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            conversation = (await contact(host, guest, "stored"))["id"]
            grace = MessageWrite(uuid4(), UUID(conversation), users[1], protocol_version=1,
                                 ciphertext=b"encrypted" * 4, crypto_meta=b"m" * 56)
            async with transaction() as unit:
                await unit.messages.record(grace)
            vote = (await host.post(f"/api/v1/conversations/{conversation}/accept")).json()["vote"]["id"]
            path = f"/api/v1/votes/{vote}"
            await host.post(path + "/ballots", json={"choice": True})
            if approved:
                await guest.post(path + "/ballots", json={"choice": True})
            assert (await host.get(path)).json()["status"] == "open"
            assert await resolve_votes_once() == 0
            expire(vote)
            normal = MessageWrite(uuid4(), UUID(conversation), users[1], protocol_version=1,
                                  ciphertext=b"subsequent" * 4, crypto_meta=b"m" * 56)
            # Deadline libera el envío aunque el worker todavía no haya actuado.
            async with transaction() as unit:
                await unit.messages.record(normal)
            history = (await host.get(f"/api/v1/conversations/{conversation}/messages")).json()["items"]
            assert {m["message_id"] for m in history} == ({str(normal.id), str(grace.id)} if approved else {str(normal.id)})
            assert sum(await asyncio.gather(*(resolve_votes_once() for _ in range(3)))) == 1
            assert await resolve_votes_once() == 0
            result = (await host.get(path)).json()
            assert result["status"] == ("approved" if approved else "rejected")
            async with transaction() as unit:
                retry = await unit.messages.record(grace)
                assert not retry.created
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT count(*) FROM messages WHERE id=%s", (grace.id,)).fetchone() == (int(approved),)
                assert db.execute("SELECT count(*) FROM messages WHERE id=%s", (normal.id,)).fetchone() == (1,)
                assert db.execute("SELECT count(*) FROM message_events WHERE id=%s", (grace.id,)).fetchone() == (1,)
                assert db.execute("SELECT count(*) FROM message_deliveries WHERE message_id=%s", (grace.id,)).fetchone() == (1,)
                assert db.execute("SELECT grace_messages_used FROM conversations WHERE id=%s", (conversation,)).fetchone() == (1,)
                assert db.execute("SELECT closed_at=expires_at FROM votes WHERE id=%s", (vote,)).fetchone() == (True,)
    asyncio.run(run())


@pytest.mark.parametrize("members,yes", [(2, 1), (2, 2), (3, 1), (3, 2), (5, 2), (5, 3), (10, 5), (10, 6)])
def test_generic_electorates_and_implicit_no(n4: tuple, members: int, yes: int) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            _, vote = await accepted(host, guest)
            # Censo sintético de grupos futuros; no añade grupos a los endpoints 1:1.
            electorate = users[:2] + [uuid4() for _ in range(members - 2)]
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                for user in electorate[2:]:
                    db.execute("INSERT INTO vote_eligible_members VALUES (%s,%s)", (vote, user))
                db.execute("UPDATE votes SET eligible_members=%s WHERE id=%s", (members, vote))
                for user in electorate[:yes]:
                    db.execute("INSERT INTO vote_ballots(vote_id,user_id,choice) VALUES (%s,%s,true)", (vote, user))
            expire(vote)
            assert await resolve_votes_once() == 1
            result = (await host.get(f"/api/v1/votes/{vote}")).json()
            assert result["status"] == ("approved" if 2 * yes > members else "rejected")
            assert result["yes_votes"] == yes and result["no_votes"] == members - yes
    asyncio.run(run())


def test_concurrent_ballots_keep_first_choice(n4: tuple) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            _, vote = await accepted(host, guest)
            path = f"/api/v1/votes/{vote}/ballots"
            responses = await asyncio.gather(*(host.post(path, json={"choice": bool(i % 2)}) for i in range(8)))
            assert {r.status_code for r in responses} == {200, 409}
            good = [r.json() for r in responses if r.status_code == 200]
            assert all(r == good[0] for r in good)
            assert good[0]["yes_votes"] + good[0]["no_votes"] == 1
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT count(*) FROM vote_ballots WHERE vote_id=%s", (vote,)).fetchone() == (1,)
    asyncio.run(run())


def test_resolution_rollback_and_census_constraints(n4: tuple) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            conversation = (await contact(host, guest, "stored"))["id"]
            message = MessageWrite(uuid4(), UUID(conversation), users[1], protocol_version=1,
                                   crypto_meta=b"m" * 56, ciphertext=b"encrypted" * 4)
            async with transaction() as unit:
                await unit.messages.record(message)
            vote = (await host.post(f"/api/v1/conversations/{conversation}/accept")).json()["vote"]["id"]
            with pytest.raises(psycopg.errors.ForeignKeyViolation), psycopg.connect(read_secret("DATABASE_URL")) as db:
                db.execute("INSERT INTO vote_ballots(vote_id,user_id,choice) VALUES (%s,%s,true)", (vote, users[2]))
            expire(vote)
            with pytest.raises(RuntimeError, match="rollback"):
                async with transaction() as unit:
                    store = VoteStore(unit.connection)
                    await store.lock(UUID(vote))
                    assert await store.resolve(UUID(vote))
                    assert await (await unit.connection.execute("SELECT 1 FROM messages WHERE id=%s", (message.id,))).fetchone() is None
                    raise RuntimeError("rollback")
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT status,closed_at FROM votes WHERE id=%s", (vote,)).fetchone() == ("open", None)
                assert db.execute("SELECT count(*) FROM messages WHERE id=%s", (message.id,)).fetchone() == (1,)
            assert await resolve_votes_once() == 1
    asyncio.run(run())


def test_deadline_checked_after_waiting_for_conversation_lock(n4: tuple) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            conversation, vote = await accepted(host, guest)
            async with transaction() as unit:
                await unit.conversations.lock(UUID(conversation))
                task = asyncio.create_task(host.post(f"/api/v1/votes/{vote}/ballots", json={"choice": True}))
                async with asyncio.timeout(3):
                    while True:
                        await unit.connection.execute("SELECT pg_stat_clear_snapshot()")
                        waiting = await (await unit.connection.execute("""SELECT 1 FROM pg_stat_activity
                            WHERE wait_event_type='Lock' AND query LIKE '%%JOIN votes v%%'""")).fetchone()
                        if waiting:
                            break
                        await asyncio.sleep(0.01)
                await unit.connection.execute("UPDATE votes SET expires_at=clock_timestamp() WHERE id=%s", (vote,))
            response = await asyncio.wait_for(task, 3)
            assert response.status_code == 410 and response.json()["error"]["code"] == "VOTE_EXPIRED"
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT count(*) FROM vote_ballots WHERE vote_id=%s", (vote,)).fetchone() == (0,)
                assert db.execute("SELECT status FROM votes WHERE id=%s", (vote,)).fetchone() == ("rejected",)
    asyncio.run(run())


@pytest.mark.parametrize("action", ["leave", "delete"])
def test_departure_preserves_electorate_and_ballots(n4: tuple, action: str) -> None:
    settings, users, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            conversation, vote = await accepted(host, guest)
            path = f"/api/v1/votes/{vote}"
            for api in (host, guest):
                assert (await api.post(path + "/ballots", json={"choice": True})).status_code == 200
            response = (await guest.post(f"/api/v1/conversations/{conversation}/leave") if action == "leave"
                        else await guest.delete("/api/v1/users/me"))
            assert response.status_code == 204
            assert (await guest.get(path)).status_code == (404 if action == "leave" else 401)
            assert (await guest.post(path + "/ballots", json={"choice": True})).status_code == (404 if action == "leave" else 401)
            expire(vote)
            assert await resolve_votes_once() == 1
            result = (await host.get(path)).json()
            assert (result["status"], result["eligible_members"], result["yes_votes"]) == ("approved", 2, 2)
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                assert db.execute("SELECT choice FROM vote_ballots WHERE vote_id=%s AND user_id=%s", (vote, users[1])).fetchone() == (True,)
    asyncio.run(run())


def test_worker_process_closes_without_api_reads(n4: tuple) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            _, vote = await accepted(host, guest)
            expire(vote)
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "chat.worker",
                                                          stdout=asyncio.subprocess.DEVNULL)
            try:
                async with asyncio.timeout(8):
                    while True:
                        with psycopg.connect(read_secret("DATABASE_URL")) as db:
                            status = db.execute("SELECT status FROM votes WHERE id=%s", (vote,)).fetchone()
                        if status == ("rejected",):
                            break
                        assert process.returncode is None
                        await asyncio.sleep(0.05)
            finally:
                if process.returncode is None:
                    process.terminate()
                await asyncio.wait_for(process.wait(), 5)
            assert process.returncode == 0
    asyncio.run(run())


def test_pubsub_failure_does_not_rollback_or_starve_other_votes(n4: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, _, _, tokens = n4

    async def unavailable(*args, **kwargs):
        raise RedisError("synthetic failure")

    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            votes = [(await accepted(host, guest))[1] for _ in range(2)]
            with monkeypatch.context() as patch:
                patch.setattr("chat.voting.publish", unavailable)
                response = await host.post(f"/api/v1/votes/{votes[0]}/ballots", json={"choice": True})
                assert response.status_code == 503
                for vote in votes:
                    expire(vote)
                assert await resolve_votes_once() == 2
            assert (await host.get(f"/api/v1/votes/{votes[0]}")).json()["yes_votes"] == 1
            for vote in votes:
                assert (await host.get(f"/api/v1/votes/{vote}")).json()["status"] == "rejected"
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["stored", "ephemeral"])
def test_websocket_personal_snapshots_and_end_to_end_grace(n4: tuple, mode: str) -> None:
    settings, _, _, tokens = n4

    async def run() -> None:
        async with server(settings) as (url_a, _), server(settings) as (url_b, _), \
                client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest, \
                contextlib.AsyncExitStack() as stack:
            conversation = (await contact(host, guest, mode))["id"]
            a, b = await open_socket(stack, url_a, host), await open_socket(stack, url_b, guest)
            message = sending(conversation)
            identifier = message["payload"]["message_id"]
            if mode == "ephemeral":
                await control(b, "message.offer", conversation, identifier)
                await received(a, "message.offer")
                await control(a, "message.ready", conversation, identifier)
                await received(b, "message.ready")
            await b.send(json.dumps(message))
            await received(a, "message.new")
            await control(a, "message.ack", conversation, identifier)
            await received(b, "message.delivered")
            vote = (await host.post(f"/api/v1/conversations/{conversation}/accept")).json()["vote"]["id"]
            for ws in (a, b):
                assert (await received(ws, "vote.opened"))["payload"]["my_vote"] is None
            path = f"/api/v1/votes/{vote}"
            await host.post(path + "/ballots", json={"choice": True})
            assert (await received(a, "vote.updated"))["payload"]["my_vote"] is True
            assert (await received(b, "vote.updated"))["payload"]["my_vote"] is None
            await guest.post(path + "/ballots", json={"choice": False})
            assert (await received(a, "vote.updated"))["payload"]["my_vote"] is True
            assert (await received(b, "vote.updated"))["payload"]["my_vote"] is False
            await b.send(json.dumps(sending(conversation)))
            assert (await received(b, "message.failed"))["payload"]["code"] == "VOTE_OPEN"
            if mode == "ephemeral":
                await control(b, "message.offer", conversation, str(uuid4()))
                error = json.loads(await asyncio.wait_for(b.recv(), 3))
                assert error["type"] == "system.error" and error["payload"]["code"] == "VOTE_OPEN"
                # Upgrade no convierte la gracia efímera en ciphertext stored.
                await host.post(f"/api/v1/conversations/{conversation}/upgrade")
                await received(a, "conversation.mode_changed")
                await received(b, "conversation.mode_changed")
            expire(vote)
            await resolve_votes_once()
            for ws, choice in ((a, True), (b, False)):
                result = (await received(ws, "vote.updated"))["payload"]
                assert result["status"] == "rejected" and result["my_vote"] is choice
                assert (result["yes_votes"], result["no_votes"]) == (1, 1)
            assert (await host.get(f"/api/v1/conversations/{conversation}/messages")).json()["items"] == []
            await b.send(json.dumps(sending(conversation)))
            assert (await received(a, "message.new"))["payload"]["message_id"] != identifier
    asyncio.run(run())
