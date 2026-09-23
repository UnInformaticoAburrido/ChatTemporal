"""Codec local, contratos replay y vectores públicos RFC de Web Push."""

import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from nacl.exceptions import CryptoError

from chat.errors import APIError
from chat.protocol import decode_binary, encode_binary
from chat.web_push import encrypt_push, endpoint_url, public_address
from chat.ws_protocol import frame, parse_frame
from chat_client.crypto import EncryptedMessage, KeyPair, decrypt_text
from chat_client.recovery import HistoricalMessage, decrypt_bundle, encrypt_bundle, replay_history


def test_local_transfer_roundtrip_tamper_limits_and_secret_isolation() -> None:
    identifier = uuid4()
    bundle = b"private-client-bundle"
    encrypted = encrypt_bundle(identifier, bundle)
    upload = encrypted.upload()
    assert set(upload) == {"encrypted_blob"} and encrypted.secret.hex() not in repr(encrypted)
    assert encode_binary(encrypted.secret) not in json.dumps(upload)
    assert decrypt_bundle(identifier, upload["encrypted_blob"], encrypted.qr_payload()) == bundle
    with pytest.raises(ValueError):
        decrypt_bundle(uuid4(), upload["encrypted_blob"], encrypted.qr_payload())
    bad = encrypted.encrypted_blob[:-1] + bytes([encrypted.encrypted_blob[-1] ^ 1])
    with pytest.raises(CryptoError):
        decrypt_bundle(identifier, encode_binary(bad), encrypted.qr_payload())
    assert len(encrypt_bundle(identifier, b"x" * (65536 - 40)).encrypted_blob) == 65536
    for invalid in (b"", b"x" * (65536 - 39)):
        with pytest.raises(ValueError):
            encrypt_bundle(identifier, invalid)
    for invalid in ("{}", encrypted.qr_payload().replace('"protocol_version": 1', '"protocol_version": true'),
                    encrypted.qr_payload().replace('"protocol_version": 1', '"protocol_version": 1,"protocol_version": 1')):
        with pytest.raises(ValueError):
            decrypt_bundle(identifier, upload["encrypted_blob"], invalid)


def test_reference_client_reencrypts_local_history_in_order(local_settings) -> None:
    sender, recipient = KeyPair.generate(), KeyPair.generate()
    original = [HistoricalMessage("primero", uuid4()), HistoricalMessage("segundo", uuid4())]
    frames = list(replay_history(uuid4(), uuid4(), original, sender, recipient.public_key))
    assert [item["type"] for item in frames] == ["recovery.replay.begin", "recovery.replay.item", "recovery.replay.item", "recovery.replay.end"]
    assert frames[-1]["payload"]["item_count"] == 2
    for position, value in enumerate(frames[1:-1]):
        payload = parse_frame(json.dumps(value), local_settings).payload
        encrypted = EncryptedMessage(decode_binary(payload.ciphertext, maximum=4096),
                                     decode_binary(payload.crypto_meta, maximum=56))
        assert payload.sequence == position and payload.original_message_id == original[position].original_message_id
        assert decrypt_text(encrypted, recipient) == original[position].text
        assert original[position].text not in json.dumps(value)


def test_replay_strict_contract_and_byte_limits(local_settings) -> None:
    payload = {"replay_id": str(uuid4()), "sequence": 0, "original_message_id": None,
               "original_timestamp": None, "protocol_version": 1,
               "ciphertext": encode_binary(b"c" * 16), "crypto_meta": encode_binary(b"m" * 56)}
    message = frame("recovery.replay.item", uuid4(), payload)
    assert parse_frame(json.dumps(message), local_settings).payload.sequence == 0
    for field, value in (("sequence", -1), ("sequence", True), ("sequence", 2**32), ("protocol_version", 2),
                         ("crypto_meta", "broken"), ("ciphertext", "x"), ("original_timestamp", "yesterday")):
        with pytest.raises(APIError):
            parse_frame(json.dumps({**message, "payload": {**payload, field: value}}), local_settings)
    with pytest.raises(APIError, match="PAYLOAD_TOO_LARGE"):
        parse_frame(json.dumps({**message, "payload": {**payload, "ciphertext": encode_binary(b"c" * 4097)}}), local_settings)
    with pytest.raises(APIError, match="UNKNOWN_FIELD"):
        parse_frame(json.dumps({**message, "payload": {**payload, "secret": "forbidden"}}), local_settings)


def test_web_push_rfc8291_known_answer() -> None:
    # Vector público RFC 8291 §5: ninguna clave de un usuario o proveedor real.
    def decode(value: str) -> bytes:
        return decode_binary(value, maximum=4096)
    receiver = decode("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4")
    sender = ec.derive_private_key(int.from_bytes(decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = encrypt_push(receiver, decode("BTBZMqHH6r4Tts7J_aSIgg"), b"When I grow up, I want to be a watermelon",
                        private=sender, salt=decode("DGv6ra1nlYgDCS1FRnbzlw"))
    assert encode_binary(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
        "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
        "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


@pytest.mark.parametrize("endpoint", ["http://push.example.com/x", "https://localhost/x", "https://127.0.0.1/x",
    "https://169.254.169.254/x", "https://[::1]/x", "https://a:b@push.example.com/x", "https://push.example.com/x#f",
    "https://push.example.com:444/x", "https://push.example.com/\nheader", "https://host.internal/x"])
def test_push_rejects_unsafe_endpoints(endpoint: str) -> None:
    with pytest.raises(ValueError):
        endpoint_url(endpoint)


def test_push_checks_all_dns_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chat.web_push.socket.getaddrinfo", lambda *args, **kwargs:
                        [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="Nonpublic"):
        public_address("push.example.com")
