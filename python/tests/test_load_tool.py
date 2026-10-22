import importlib.util
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from chat.protocol import encode_binary
from chat_client.crypto import KeyPair

spec = importlib.util.spec_from_file_location("chat_load_tool", Path(__file__).resolve().parents[2] / "scripts/load_chat.py")
assert spec and spec.loader
load_tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = load_tool
spec.loader.exec_module(load_tool)


def test_accounts_file_permissions_strict_fields_and_no_repr_secrets(tmp_path: Path) -> None:
    key = encode_binary(KeyPair.generate().private_key)
    data = [{"conversation_id": str(uuid4()), "mode": "stored", "sender_token": "SECRET-SENDER-TOKEN",
             "recipient_token": "SECRET-RECIPIENT-TOKEN", "sender_private_key": key, "recipient_private_key": key}]
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(data))
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        load_tool.read_pairs(path)
    path.chmod(0o600)
    pairs = load_tool.read_pairs(path)
    assert "SECRET" not in repr(pairs) and key not in repr(pairs)
    path.write_text(json.dumps(data * 2))
    with pytest.raises(ValueError, match="reutilizar"):
        load_tool.read_pairs(path)
    data[0]["extra"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Formato"):
        load_tool.read_pairs(path)


@pytest.mark.parametrize("url", ["http://example.com", "https://user:secret@example.com",
                                 "https://example.com/path", "https://example.com?token=secret"])
def test_load_requires_origin_and_tls(url: str) -> None:
    with pytest.raises(ValueError):
        load_tool.validate_target(url, True)
    assert load_tool.validate_target("https://example.com/", False) == "https://example.com"
    assert load_tool.validate_target("http://127.0.0.1:1234", True) == "http://127.0.0.1:1234"
    with pytest.raises(ValueError):
        load_tool.validate_target("http://127.0.0.1:1234", False)


def test_history_rejects_missing_duplicate_and_corrupted_messages() -> None:
    import asyncio

    import httpx

    from chat_client.crypto import encrypt_text
    sender, recipient = KeyPair.generate(), KeyPair.generate()
    pair = load_tool.Pair(uuid4(), "stored", "sender", "recipient", sender, recipient)
    identifier = str(uuid4())
    item = encrypt_text("expected", sender, recipient.public_key).payload(UUID(identifier))
    async def run(items, code):
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"items": items, "next_cursor": None}))
        async with httpx.AsyncClient(base_url="https://test.invalid", transport=transport) as api:
            with pytest.raises(load_tool.LoadFailure, match=code):
                await load_tool.history(api, pair, {identifier: "expected"})
    asyncio.run(run([], "HISTORY_MISSING"))
    asyncio.run(run([item, item], "HISTORY_DUPLICATE"))
    corrupt = encrypt_text("corrupted", sender, recipient.public_key).payload(UUID(identifier))
    asyncio.run(run([corrupt], "HISTORY_CORRUPTED"))


def test_reconnect_profile_rejects_ephemeral_and_invalid_scenario_before_io() -> None:
    import asyncio
    key = KeyPair.generate()
    ephemeral = load_tool.Pair(uuid4(), "ephemeral", "sender", "recipient", key, key)
    stored = load_tool.Pair(uuid4(), "stored", "sender", "recipient", key, key)
    for pair, options in ((ephemeral, {}), (stored, {"reconnect_every": 0}),
                          (stored, {"offline_seconds": 0}), (stored, {"offline_seconds": 11})):
        with pytest.raises(ValueError):
            asyncio.run(load_tool.benchmark("https://test.invalid", [pair], expected_peak=1,
                duration=1, interval=1, profile="stored-reconnect", **options))
    for pair, options in ((stored, {}), (ephemeral, {"reconnect_every": 0}),
                          (ephemeral, {"offline_seconds": 0}), (ephemeral, {"offline_seconds": 11})):
        with pytest.raises(ValueError):
            asyncio.run(load_tool.benchmark("https://test.invalid", [pair], expected_peak=1,
                duration=1, interval=1, profile="ephemeral-disconnect", **options))


@pytest.mark.parametrize(("kind", "code", "wrong_id", "error"), [
    ("message.failed", "RECIPIENT_DISCONNECTED", False, None),
    ("message.failed", "OFFER_TIMEOUT", False, "UNEXPECTED_FAILURE"),
    ("message.failed", "RECIPIENT_DISCONNECTED", True, "UNEXPECTED_MESSAGE"),
    ("message.delivered", None, False, "UNEXPECTED_DELIVERY"),
    ("system.error", None, False, "WS_REMOTE_ERROR"),
])
def test_expected_failure_requires_matching_id_and_reason(kind, code, wrong_id, error) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    identifier = str(uuid4())
    payload = {"message_id": str(uuid4()) if wrong_id else identifier, "code": code}
    socket = AsyncMock()
    socket.recv.return_value = json.dumps({"type": kind, "payload": payload})
    async def run():
        return await load_tool.received(socket, "message.failed", identifier, 1,
                                        failure_code="RECIPIENT_DISCONNECTED")
    if error:
        with pytest.raises(load_tool.LoadFailure, match=error):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == payload


def test_retry_receipts_are_correlated_and_unknown_messages_are_rejected() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    previous, current, request = str(uuid4()), str(uuid4()), str(uuid4())
    def receipt(identifier, rid):
        return json.dumps({"type": "message.delivered", "request_id": rid,
                           "payload": {"message_id": identifier}})
    socket = AsyncMock()
    socket.recv.side_effect = [receipt(previous, "old"), receipt(current, "old"), receipt(current, request)]
    result = asyncio.run(load_tool.received(socket, "message.delivered", current, 1,
                                           settled={previous: "already received"}, request_id=request))
    assert result["message_id"] == current and socket.recv.await_count == 3
    socket.recv.side_effect = [receipt(str(uuid4()), request)]
    with pytest.raises(load_tool.LoadFailure, match="UNEXPECTED_MESSAGE"):
        asyncio.run(load_tool.received(socket, "message.delivered", current, 1, settled={previous: "received"}))


def test_failed_retry_requires_its_receipt_and_reconnected_receiver_rejects_replayed_payload() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    identifier, request = str(uuid4()), str(uuid4())
    socket = AsyncMock()
    socket.recv.side_effect = [json.dumps({"type": "message.failed", "request_id": rid,
        "payload": {"message_id": identifier, "code": code}}) for rid, code in (
            ("old", "RECIPIENT_DISCONNECTED"), (request, "DELIVERY_TIMEOUT"))]
    result = asyncio.run(load_tool.received(socket, "message.failed", identifier, 1,
                         request_id=request, failure_code="DELIVERY_TIMEOUT"))
    assert result["code"] == "DELIVERY_TIMEOUT" and socket.recv.await_count == 2
    socket.recv.side_effect = [json.dumps({"type": "message.new", "payload": {"message_id": identifier}})]
    with pytest.raises(load_tool.LoadFailure, match="UNEXPECTED_MESSAGE"):
        asyncio.run(load_tool.received(socket, "message.offer", str(uuid4()), 1))


@pytest.mark.parametrize("status", [429, 503])
def test_pending_probe_does_not_hide_http_failures(status: int) -> None:
    import asyncio

    import httpx

    async def run():
        transport = httpx.MockTransport(lambda _: httpx.Response(status))
        async with httpx.AsyncClient(base_url="https://test.invalid", transport=transport) as api:
            with pytest.raises(load_tool.LoadFailure, match=f"HTTP_{status}"):
                await load_tool.pending(api, str(uuid4()), 1)
    asyncio.run(run())
