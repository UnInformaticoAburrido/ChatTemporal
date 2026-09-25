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
