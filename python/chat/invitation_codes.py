"""§7: tokens de 62 bytes autenticados; nunca contienen identidad de usuario."""

import hmac
import struct
from dataclasses import dataclass
from uuid import UUID

from chat.errors import APIError
from chat.persistence import Mode
from chat.protocol import decode_binary, encode_binary

LAYOUT = struct.Struct("!B16sIQB")


@dataclass(frozen=True)
class InvitationToken:
    id: UUID
    generation: int
    created_at: int
    mode: Mode


def encode_code(token: InvitationToken, secret: bytes) -> str:
    if not 1 <= token.generation <= 0xFFFFFFFF or token.mode not in ("ephemeral", "stored"):
        raise ValueError("Invalid invitation fields")
    payload = LAYOUT.pack(1, token.id.bytes, token.generation, token.created_at, int(token.mode == "stored"))
    return encode_binary(payload + hmac.digest(secret, payload, "sha256"))


def decode_code(code: str, secret: bytes) -> InvitationToken:
    try:
        raw = decode_binary(code, minimum=62, maximum=62)
        payload, signature = raw[:30], raw[30:]
        if not hmac.compare_digest(signature, hmac.digest(secret, payload, "sha256")):
            raise ValueError("Invalid signature")
        version, identifier, generation, created_at, mode = LAYOUT.unpack(payload)
        if version != 1 or generation == 0 or mode not in (0, 1):
            raise ValueError("Invalid invitation fields")
        return InvitationToken(UUID(bytes=identifier), generation, created_at, "stored" if mode else "ephemeral")
    except (ValueError, struct.error):
        raise APIError("INVITATION_INVALID", 400, "Invalid invitation code.") from None
