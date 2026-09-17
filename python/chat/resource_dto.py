"""Contratos N3 para claves, lecturas y payload cifrado sin descifrarlo."""

import json
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from chat.config import Settings
from chat.errors import APIError
from chat.identity_dto import StrictDTO, UserPublic
from chat.protocol import (
    UUID4,
    CanonicalUUID,
    ProtocolVersion,
    UTCDateTime,
    decode_binary,
    invalid_json_constant,
    unique_json_object,
)


class PublicKeyInput(StrictDTO):
    public_key: str = Field(repr=False)
    protocol_version: ProtocolVersion

    @field_validator("public_key")
    @classmethod
    def check_key(cls, value: str) -> str:
        decode_binary(value, minimum=32, maximum=32)
        return value


class PublicKey(PublicKeyInput):
    updated_at: UTCDateTime


class Page[T](StrictDTO):
    items: list[T]
    next_cursor: str | None


class ConversationSummary(StrictDTO):
    id: CanonicalUUID
    mode: Literal["stored", "ephemeral"]
    status: Literal["pending", "active", "closed"]
    role: Literal["host", "guest"]
    grace_message_limit: int
    grace_messages_used: int
    created_at: UTCDateTime
    accepted_at: UTCDateTime | None
    mode_changed_at: UTCDateTime | None
    closed_at: UTCDateTime | None
    peer: UserPublic | None


class StoredMessage(StrictDTO):
    message_id: CanonicalUUID
    sender_role: Literal["host", "guest"]
    sent_at: UTCDateTime
    protocol_version: ProtocolVersion
    ciphertext: str = Field(repr=False)
    crypto_meta: str = Field(repr=False)
    is_grace_message: bool
    content_expires_at: UTCDateTime


class MessagePayload(StrictDTO):
    message_id: UUID4
    protocol_version: ProtocolVersion
    ciphertext: str = Field(repr=False)
    crypto_meta: str = Field(repr=False)

    @field_validator("crypto_meta")
    @classmethod
    def check_meta(cls, value: str) -> str:
        decode_binary(value, minimum=56, maximum=56)
        return value


class MessageSend(StrictDTO):
    type: Literal["message.send"]
    request_id: UUID4
    conversation_id: CanonicalUUID
    timestamp: UTCDateTime
    payload: MessagePayload


def parse_message_send(frame: str, settings: Settings) -> MessageSend:
    """Preparado para N5: validar frame/bytes, sin aceptar aún ningún envío WS."""
    try:
        if len(frame.encode("utf-8")) > settings.max_envelope_bytes:
            raise APIError("PAYLOAD_TOO_LARGE", 413, "Message frame too large.")
        message = MessageSend.model_validate(json.loads(
            frame, object_pairs_hook=unique_json_object, parse_constant=invalid_json_constant,
        ))
        # DEC-47: límite independiente en bytes; nunca contar caracteres del
        # texto ni comparar con max_message_length en el servidor.
        raw = decode_binary(message.payload.ciphertext, minimum=16, maximum=settings.max_envelope_bytes)
        if len(raw) > settings.max_ciphertext_bytes:
            raise APIError("PAYLOAD_TOO_LARGE", 413, "Encrypted payload too large.")
        return message
    except ValidationError as exc:
        kinds = {item["type"] for item in exc.errors()}
        code = ("UNKNOWN_FIELD" if "extra_forbidden" in kinds else "UNSUPPORTED_PROTOCOL_VERSION"
                if "unsupported_protocol_version" in kinds else "VALIDATION_ERROR")
        raise APIError(code, 422, "Invalid message frame.") from None
    except (ValueError, UnicodeError, RecursionError):
        raise APIError("VALIDATION_ERROR", 422, "Invalid message frame.") from None
