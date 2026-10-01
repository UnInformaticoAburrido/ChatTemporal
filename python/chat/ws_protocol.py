"""Envelope v1: JSON estricto, sin eventos de aceptación inventados."""

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import ValidationError

from chat.config import Settings
from chat.errors import APIError
from chat.identity_dto import StrictDTO
from chat.protocol import (
    UUID4,
    CanonicalUUID,
    UTCDateTime,
    decode_binary,
    invalid_json_constant,
    unique_json_object,
)
from chat.replay_dto import ReplayBegin, ReplayEnd, ReplayFrame, ReplayItem
from chat.resource_dto import MessageSend, parse_message_send


class MessageReference(StrictDTO):
    message_id: UUID4


class MessageControl(StrictDTO):
    type: Literal["message.offer", "message.ready", "message.ack"]
    request_id: UUID4
    conversation_id: CanonicalUUID
    timestamp: UTCDateTime
    payload: MessageReference


class DeliveryStatus(StrictDTO):
    message_id: CanonicalUUID
    status: Literal["pending", "delivered", "failed", "expired"]
    sent_at: UTCDateTime
    received_at: UTCDateTime | None
    expires_at: UTCDateTime


def parse_frame(text: str, settings: Settings) -> MessageControl | MessageSend | ReplayFrame:
    if len(text.encode("utf-8")) > settings.max_envelope_bytes:
        raise APIError("PAYLOAD_TOO_LARGE", 413, "Frame too large.")
    try:
        value = json.loads(text, object_pairs_hook=unique_json_object, parse_constant=invalid_json_constant)
        if not isinstance(value, dict):
            raise ValueError("Object required")
        if value.get("type") == "message.send":
            return parse_message_send(text, settings)
        if value.get("type") == "recovery.replay.begin":
            return ReplayBegin.model_validate(value)
        if value.get("type") == "recovery.replay.end":
            return ReplayEnd.model_validate(value)
        if value.get("type") == "recovery.replay.item":
            item = ReplayItem.model_validate(value)
            raw = decode_binary(item.payload.ciphertext, minimum=16, maximum=settings.max_envelope_bytes)
            if len(raw) > settings.max_ciphertext_bytes:
                raise APIError("PAYLOAD_TOO_LARGE", 413, "Encrypted payload too large.")
            return item
        return MessageControl.model_validate(value)
    except ValidationError as error:
        kinds = {e["type"] for e in error.errors()}
        code = ("UNKNOWN_FIELD" if "extra_forbidden" in kinds else "UNSUPPORTED_PROTOCOL_VERSION"
                if "unsupported_protocol_version" in kinds else "VALIDATION_ERROR")
        raise APIError(code, 422, "Invalid frame.") from None
    except (ValueError, RecursionError):
        raise APIError("VALIDATION_ERROR", 422, "Invalid frame.") from None


def frame(kind: str, conversation: UUID | None, payload: dict[str, Any],
          request_id: UUID | None = None) -> dict[str, Any]:
    return {"type": kind, "request_id": str(request_id or uuid4()),
            "conversation_id": str(conversation) if conversation else None,
            "timestamp": utc_text(datetime.now(UTC)), "payload": payload}


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def error_frame(error: APIError, conversation: UUID | None = None,
                request_id: UUID | None = None) -> dict[str, Any]:
    return frame("system.error", conversation, {"code": error.code, "message": error.message,
                 "retryable": error.status in (429, 503), "details": {}}, request_id)
