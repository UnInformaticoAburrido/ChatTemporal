import json
import random
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from nacl import bindings
from nacl.exceptions import CryptoError

from chat.app import create_app
from chat.config import Settings
from chat.errors import APIError
from chat.pagination import decode_cursor, encode_cursor, pagination
from chat.persistence import Position
from chat.protocol import canonical_uuid, decode_binary, encode_binary, utc_timestamp, uuid4_value
from chat.resource_dto import parse_message_send
from chat_client.crypto import EncryptedMessage, KeyPair, decrypt_text, encrypt_text


def frame() -> dict:
    return {"type": "message.send", "request_id": str(uuid4()), "conversation_id": str(uuid4()),
            "timestamp": "2026-09-17T12:00:00.123456Z", "payload": {
                "message_id": str(uuid4()), "protocol_version": 1,
                "ciphertext": encode_binary(b"x" * 16), "crypto_meta": encode_binary(b"m" * 56)}}


def test_crypto_box_interoperability_unicode_limits_and_tampering() -> None:
    sender, recipient = KeyPair.generate(), KeyPair.generate()
    text = "🙂" * 256
    encrypted = encrypt_text(text, sender, recipient.public_key)
    assert len(encrypted.ciphertext) == 1040 and len(encrypted.crypto_meta) == 56
    assert encrypted.crypto_meta[24:] == sender.public_key
    assert decrypt_text(encrypted, recipient) == text
    assert bindings.crypto_box_open_easy(encrypted.ciphertext, encrypted.crypto_meta[:24],
                                        sender.public_key, recipient.private_key) == text.encode()
    nonce = b"n" * 24
    foreign = bindings.crypto_box_easy(b"interop", nonce, recipient.public_key, sender.private_key)
    assert decrypt_text(EncryptedMessage(foreign, nonce + sender.public_key), recipient) == "interop"
    assert encrypt_text(text, sender, recipient.public_key).crypto_meta[:24] != encrypted.crypto_meta[:24]
    with pytest.raises(ValueError):
        encrypt_text(text + "a", sender, recipient.public_key)
    assert decrypt_text(encrypt_text("a" * 300, sender, recipient.public_key, max_characters=300), recipient) == "a" * 300
    for changed in (replace(encrypted, ciphertext=bytes([encrypted.ciphertext[0] ^ 1]) + encrypted.ciphertext[1:]),
                    replace(encrypted, crypto_meta=b"z" + encrypted.crypto_meta[1:])):
        with pytest.raises(CryptoError):
            decrypt_text(changed, recipient)
    with pytest.raises(CryptoError):
        decrypt_text(encrypted, KeyPair.generate())
    assert sender.private_key.hex() not in repr(sender)


def test_binary_and_cursor_deterministic_fuzz() -> None:
    rng = random.Random(531)
    for size in range(150):
        raw = rng.randbytes(size)
        value = encode_binary(raw)
        assert decode_binary(value, minimum=size, maximum=size) == raw
        for invalid in (value + "=", value + " ", "é" + value, "+" + value, "/" + value):
            with pytest.raises(ValueError):
                decode_binary(invalid, maximum=200)
        if size != 42:
            with pytest.raises(APIError, match="INVALID_CURSOR"):
                decode_cursor(value, "conversations")
    # Nonzero unused bits decode to the same bytes but are not canonical.
    with pytest.raises(ValueError):
        decode_binary("AB", maximum=1)
    position = Position(datetime(2026, 9, 17, 12, 30, 15, 123456, tzinfo=UTC), uuid4())
    scope = uuid4()
    cursor = encode_cursor(position, "messages", scope)
    assert decode_cursor(cursor, "messages", scope) == position
    for kind, other in (("messages", uuid4()), ("conversations", None)):
        with pytest.raises(APIError, match="INVALID_CURSOR"):
            decode_cursor(cursor, kind, other)
    with pytest.raises(APIError, match="INVALID_CURSOR"):
        decode_cursor(encode_binary(b"\x01\x01" + b"\0" * 16 + b"\x7f" * 8 + b"\0" * 16), "conversations")


def test_canonical_identifiers_and_utc() -> None:
    identifier = uuid4()
    assert canonical_uuid(str(identifier)) == uuid4_value(str(identifier)) == identifier
    for invalid in (identifier.hex, str(identifier).upper(), "{" + str(identifier) + "}", 1, True):
        with pytest.raises(ValueError):
            canonical_uuid(invalid)
    with pytest.raises(ValueError):
        uuid4_value(str(UUID(int=0)))
    assert utc_timestamp("2026-09-17T00:00:00+00:00").tzinfo == UTC
    for invalid in ("2026-09-17", "2026-09-17T00:00:00", "2026-09-17T01:00:00+01:00",
                    "2026-09-17T00:00:00.1234567Z", "2026-02-30T00:00:00Z", 0):
        with pytest.raises(ValueError):
            utc_timestamp(invalid)


@pytest.mark.parametrize("query,code", [
    (b"offset=1", "UNKNOWN_FIELD"), (b"limit=1&limit=2", "VALIDATION_ERROR"),
    (b"limit=0", "VALIDATION_ERROR"), (b"limit=101", "VALIDATION_ERROR"),
    (b"limit=-1", "VALIDATION_ERROR"), (b"limit=1.0", "VALIDATION_ERROR"),
])
def test_pagination_query_rejects_ambiguity(local_settings: Settings, query: bytes, code: str) -> None:
    with pytest.raises(APIError, match=code):
        pagination(Request({"type": "http", "query_string": query}), local_settings)


@pytest.mark.parametrize("field,value,code", [
    ("protocol_version", 2, "UNSUPPORTED_PROTOCOL_VERSION"),
    ("protocol_version", True, "VALIDATION_ERROR"), ("protocol_version", "1", "VALIDATION_ERROR"),
    ("crypto_meta", encode_binary(b"x" * 55), "VALIDATION_ERROR"),
    ("crypto_meta", encode_binary(b"x" * 57), "VALIDATION_ERROR"),
    ("ciphertext", encode_binary(b"x" * 15), "VALIDATION_ERROR"),
    ("ciphertext", "a=", "VALIDATION_ERROR"), ("private_key", "secret", "UNKNOWN_FIELD"),
    ("message_id", str(UUID(int=0)), "VALIDATION_ERROR"),
])
def test_message_payload_contract(local_settings: Settings, field: str, value: object, code: str) -> None:
    data = frame()
    data["payload"][field] = value
    with pytest.raises(APIError, match=code):
        parse_message_send(json.dumps(data), local_settings)


def test_message_limits_are_bytes_and_never_plaintext_length(local_settings: Settings) -> None:
    data = frame()
    sender, recipient = KeyPair.generate(), KeyPair.generate()
    data["payload"] = encrypt_text("a" * 300, sender, recipient.public_key, max_characters=300).payload(uuid4())
    assert parse_message_send(json.dumps(data), local_settings).payload.ciphertext == data["payload"]["ciphertext"]
    data["payload"]["ciphertext"] = encode_binary(b"x" * local_settings.max_ciphertext_bytes)
    parse_message_send(json.dumps(data), local_settings)
    data["payload"]["ciphertext"] = encode_binary(b"x" * (local_settings.max_ciphertext_bytes + 1))
    with pytest.raises(APIError, match="PAYLOAD_TOO_LARGE"):
        parse_message_send(json.dumps(data), local_settings)
    for invalid in ("x" * 8193, "🙂" * 2049):
        with pytest.raises(APIError, match="PAYLOAD_TOO_LARGE"):
            parse_message_send(invalid, local_settings)
    for invalid in ('{"type":"x","type":"message.send"}', '{"payload":NaN}', "[" * 1500, '"\\ud800"'):
        with pytest.raises(APIError, match="VALIDATION_ERROR"):
            parse_message_send(invalid, local_settings)


def test_cors_errors_request_ids_and_duplicate_json(local_settings: Settings) -> None:
    app = create_app(local_settings)

    @app.get("/api/v1/test-failure")
    async def failure() -> None:
        raise RuntimeError("DO_NOT_EXPOSE")

    origin = local_settings.allowed_origins[0]
    with TestClient(app) as client:
        valid_id = str(uuid4())
        for supplied in (valid_id, valid_id.upper(), str(UUID(int=0)), "invalid"):
            response = client.get("/api/v1/users/me", headers={"X-Request-ID": supplied, "Origin": origin})
            assert response.status_code == 401
            assert uuid4_value(response.headers["X-Request-ID"])
            assert (response.headers["X-Request-ID"] == supplied) == (supplied == valid_id)
            assert response.headers["Access-Control-Allow-Origin"] == origin
            assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        for content_type in (None, "application/json", "application/problem+json"):
            headers = {"Origin": origin}
            if content_type:
                headers["Content-Type"] = content_type
            for body in ('{"nick":"a","nick":"secret"}', '{"nick":NaN}', b"\xff", "[" * 1500):
                response = client.post("/api/v1/users/register", content=body, headers=headers)
                assert response.status_code == 400
                assert response.headers["Access-Control-Allow-Origin"] == origin
                assert "secret" not in response.text
        for allowed in (True, False):
            headers = {"Origin": origin if allowed else "https://untrusted.invalid"}
            response = client.get("/api/v1/test-failure", headers=headers)
            assert response.status_code == 500 and "DO_NOT_EXPOSE" not in response.text
            assert ("Access-Control-Allow-Origin" in response.headers) == allowed
            headers.update({"Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "Authorization"})
            response = client.options("/api/v1/users/me/keys", headers=headers)
            assert response.status_code == (200 if allowed else 400)
