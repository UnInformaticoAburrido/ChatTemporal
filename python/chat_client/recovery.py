"""Bundle y contenido QR locales. Ningún secreto se incluye en el PUT al servidor."""

import base64
import json
import secrets
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from nacl import bindings

from chat_client.crypto import KeyPair, encrypt_text


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decoded(value: str) -> bytes:
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if encoded(raw) != value:
        raise ValueError("Noncanonical Base64URL")
    return raw


@dataclass(frozen=True)
class EncryptedBundle:
    transfer_id: UUID
    encrypted_blob: bytes = field(repr=False)
    secret: bytes = field(repr=False)

    def upload(self) -> dict[str, str]:
        return {"encrypted_blob": encoded(self.encrypted_blob)}

    def qr_payload(self) -> str:
        return json.dumps({"transfer_id": str(self.transfer_id), "secret": encoded(self.secret), "protocol_version": 1})


def encrypt_bundle(transfer_id: UUID, bundle: bytes) -> EncryptedBundle:
    # XChaCha20-Poly1305; vincular UUID y versión mediante datos autenticados.
    if not bundle or len(bundle) + 24 + 16 > 65536:
        raise ValueError("Bundle exceeds transfer limit")
    secret, nonce = secrets.token_bytes(32), secrets.token_bytes(24)
    ciphertext = bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(
        bundle, b"chat-key-transfer-v1\0" + transfer_id.bytes, nonce, secret)
    return EncryptedBundle(transfer_id, nonce + ciphertext, secret)


def decrypt_bundle(transfer_id: UUID, encrypted_blob: str, qr_payload: str) -> bytes:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate QR field")
            result[key] = value
        return result

    if len(qr_payload) > 1024:
        raise ValueError("Invalid QR")
    qr = json.loads(qr_payload, object_pairs_hook=unique)
    if (not isinstance(qr, dict) or set(qr) != {"transfer_id", "secret", "protocol_version"}
            or qr["transfer_id"] != str(transfer_id) or type(qr["protocol_version"]) is not int
            or qr["protocol_version"] != 1 or not isinstance(qr["secret"], str)):
        raise ValueError("Invalid QR")
    secret, blob = decoded(qr["secret"]), decoded(encrypted_blob)
    if len(secret) != 32 or not 40 < len(blob) <= 65536:
        raise ValueError("Invalid bundle")
    return bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(
        blob[24:], b"chat-key-transfer-v1\0" + transfer_id.bytes, blob[:24], secret)


@dataclass(frozen=True)
class HistoricalMessage:
    text: str = field(repr=False)
    original_message_id: UUID | None = None
    original_timestamp: datetime | None = None


def replay_history(conversation: UUID, replay_id: UUID, messages: Iterable[HistoricalMessage],
                   sender: KeyPair, recipient_public_key: bytes, *, max_characters: int = 256,
                   ) -> Iterator[dict[str, object]]:
    """Recifrar una copia local en orden; el llamante transmite frames por WS."""
    if replay_id.version != 4:
        raise ValueError("replay_id must be UUIDv4")

    def envelope(kind: str, payload: dict[str, object]) -> dict[str, object]:
        return {"type": "recovery.replay." + kind, "request_id": str(uuid4()), "conversation_id": str(conversation),
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "payload": payload}

    yield envelope("begin", {"replay_id": str(replay_id)})
    count = 0
    for entry in messages:
        if count >= 0xFFFFFFFF:
            raise ValueError("Replay exceeds uint32 item count")
        if entry.original_timestamp is not None and entry.original_timestamp.utcoffset() is None:
            raise ValueError("Original timestamp must include timezone")
        encrypted = encrypt_text(entry.text, sender, recipient_public_key, max_characters=max_characters)
        yield envelope("item", {"replay_id": str(replay_id), "sequence": count,
            "original_message_id": str(entry.original_message_id) if entry.original_message_id else None,
            "original_timestamp": entry.original_timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if entry.original_timestamp else None,
            "protocol_version": 1, "ciphertext": encoded(encrypted.ciphertext), "crypto_meta": encoded(encrypted.crypto_meta)})
        count += 1
    yield envelope("end", {"replay_id": str(replay_id), "item_count": count})
