import asyncio
import json
import os

import psycopg
import pytest
from test_conversations_integration import client, contact
from test_conversations_integration import n4 as n4
from test_load_tool import load_tool
from test_websocket_integration import server

from chat.config import read_secret
from chat.protocol import encode_binary
from chat.voting import resolve_votes_once
from chat_client.crypto import KeyPair

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL y Redis aislados",
)]


@pytest.mark.parametrize(("mode", "profile", "corrupt"), [
    ("stored", "online", False), ("ephemeral", "online", False),
    ("stored", "stored-reconnect", False), ("stored", "stored-reconnect", True),
])
def test_load_roundtrip_history_and_failure_before_sending(n4: tuple, mode: str, profile: str,
                                                        corrupt: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, _, _, tokens = n4
    keys = [KeyPair.generate(), KeyPair.generate()]
    async def run() -> None:
        async with client(settings, tokens[0]) as host, client(settings, tokens[1]) as guest:
            for api, key in zip((host, guest), keys, strict=True):
                assert (await api.put("/api/v1/users/me/keys", json={
                    "public_key": encode_binary(key.public_key), "protocol_version": 1})).status_code == 200
            conversation = (await contact(host, guest, mode))["id"]
            accepted = await host.post(f"/api/v1/conversations/{conversation}/accept")
            assert accepted.status_code == 200
            # Solo fixture: adelantar el vencimiento antes de iniciar la herramienta.
            with psycopg.connect(read_secret("DATABASE_URL")) as db:
                db.execute("UPDATE votes SET expires_at=now()-interval '1 second' WHERE conversation_id=%s", (conversation,))
            await resolve_votes_once()
        async with server(settings) as (url, _):
            from uuid import UUID
            base = url.removesuffix("/ws/v1").replace("ws://", "http://", 1)
            pair = load_tool.Pair(UUID(conversation), mode, tokens[1], tokens[0], keys[1], keys[0])
            if corrupt:
                monkeypatch.setattr(load_tool, "decrypt_text", lambda *args: "corrupted content")
            result = await load_tool.benchmark(base, [pair], expected_peak=1,
                                               duration=1.5 if profile == "stored-reconnect" else .3, interval=.1,
                                               profile=profile, reconnect_every=2, offline_seconds=.05)
            if corrupt:
                assert not result["passed"] and result["confirmed"] == 0 and result["sent"] == 1
                assert result["errors"] == ["HISTORY_CORRUPTED"]
                with psycopg.connect(read_secret("DATABASE_URL")) as db:
                    states = db.execute("""SELECT d.status FROM message_deliveries d
                        JOIN message_events e ON e.id=d.message_id WHERE e.conversation_id=%s""", (conversation,)).fetchall()
                    assert states == [("pending",)]  # Nunca ACK de contenido no verificado.
                return
            assert result["passed"], result
            assert result["target_sockets"] == result["peak_sockets"] == 2
            assert result["attempted"] == result["confirmed"] >= 1
            assert result["verified_pairs"] == 1
            if profile == "stored-reconnect":
                assert result["disconnect_cycles"] >= 1
                assert result["reconnections"] == result["retries"] == 2 * result["disconnect_cycles"]
                with psycopg.connect(read_secret("DATABASE_URL")) as db:
                    count = db.execute("""SELECT count(*) FROM messages m JOIN message_events e ON e.id=m.id
                        WHERE e.conversation_id=%s""", (conversation,)).fetchone()[0]
                    assert count == result["confirmed"]
            assert not result["production_certified"]
            assert not any(token in json.dumps(result) for token in tokens)
            wrong = load_tool.Pair(UUID(conversation), mode, tokens[1], tokens[0], KeyPair.generate(), keys[0])
            failure = await load_tool.benchmark(base, [wrong], expected_peak=1, duration=.3, interval=.1)
            assert not failure["passed"] and failure["sent"] == 0
            assert failure["errors"] == ["ACCOUNT_KEY_MISMATCH"]
    asyncio.run(run())
