"""Cifrado local interoperable con §5. Sin almacenamiento ni llamadas de red."""

import base64
import secrets
from dataclasses import dataclass, field
from uuid import UUID

from nacl import bindings


@dataclass(frozen=True)
class KeyPair:
    private_key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.private_key) != 32:
            raise ValueError("Private key must contain 32 bytes")

    @property
    def public_key(self) -> bytes:
        return bindings.crypto_scalarmult_base(self.private_key)

    @classmethod
    def generate(cls) -> "KeyPair":
        _, private = bindings.crypto_box_keypair()
        return cls(private)


@dataclass(frozen=True)
class EncryptedMessage:
    ciphertext: bytes = field(repr=False)
    crypto_meta: bytes = field(repr=False)
    protocol_version: int = 1

    def payload(self, message_id: UUID) -> dict[str, str | int]:
        if message_id.version != 4:
            raise ValueError("message_id must be UUIDv4")
        return {"message_id": str(message_id), "protocol_version": self.protocol_version,
                "ciphertext": base64.urlsafe_b64encode(self.ciphertext).decode("ascii").rstrip("="),
                "crypto_meta": base64.urlsafe_b64encode(self.crypto_meta).decode("ascii").rstrip("=")}


def encrypt_text(text: str, sender: KeyPair, recipient_public_key: bytes, *, max_characters: int = 256) -> EncryptedMessage:
    # DEC-50: caracteres = puntos de código Unicode (len de str), no bytes UTF-8
    # ni unidades UTF-16. El texto no se normaliza ni se envía al servidor.
    if type(text) is not str or type(max_characters) is not int or max_characters < 1:
        raise ValueError("Invalid text or character limit")
    if len(text) > max_characters:
        raise ValueError("Text exceeds the configured character limit")
    nonce = secrets.token_bytes(24)
    ciphertext = bindings.crypto_box_easy(text.encode("utf-8"), nonce, recipient_public_key, sender.private_key)
    return EncryptedMessage(ciphertext, nonce + sender.public_key)


def decrypt_text(message: EncryptedMessage, recipient: KeyPair) -> str:
    if type(message.protocol_version) is not int or message.protocol_version != 1 or len(message.crypto_meta) != 56:
        raise ValueError("Unsupported encrypted message")
    plaintext = bindings.crypto_box_open_easy(message.ciphertext, message.crypto_meta[:24],
                                               message.crypto_meta[24:], recipient.private_key)
    return plaintext.decode("utf-8")
