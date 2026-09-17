"""Cursor sin firma: identifica orden/recurso, nunca concede acceso (§23.4)."""

import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import Request

from chat.config import Settings
from chat.errors import APIError
from chat.persistence import Position
from chat.protocol import decode_binary, encode_binary

Kind = Literal["conversations", "messages"]
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# DEC-46: versión, tipo, scope UUID, microsegundos UTC firmados, UUID desempate.
# 42 bytes big-endian, 56 caracteres Base64URL. Sin user_id ni permisos.
LAYOUT = struct.Struct(">BB16sq16s")
KINDS: dict[Kind, int] = {"conversations": 1, "messages": 2}


def encode_cursor(position: Position, kind: Kind, scope: UUID | None = None) -> str:
    delta = position.timestamp.astimezone(UTC) - EPOCH
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return encode_binary(LAYOUT.pack(1, KINDS[kind], (scope or UUID(int=0)).bytes, micros, position.id.bytes))


def decode_cursor(value: str, kind: Kind, scope: UUID | None = None) -> Position:
    try:
        version, tag, resource, micros, identifier = LAYOUT.unpack(
            decode_binary(value, minimum=LAYOUT.size, maximum=LAYOUT.size),
        )
        if version != 1 or tag != KINDS[kind] or resource != (scope or UUID(int=0)).bytes:
            raise ValueError()
        return Position(EPOCH + timedelta(microseconds=micros), UUID(bytes=identifier))
    except (ValueError, OverflowError, struct.error):
        raise APIError("INVALID_CURSOR", 400, "Invalid pagination cursor.") from None


@dataclass(frozen=True)
class Pagination:
    limit: int
    cursor: str | None


def pagination(request: Request, settings: Settings) -> Pagination:
    values: dict[str, str] = {}
    for name, value in request.query_params.multi_items():
        if name not in {"limit", "cursor"}:
            raise APIError("UNKNOWN_FIELD", 422, "Unknown query field.")
        if name in values:
            raise APIError("VALIDATION_ERROR", 422, "Duplicate query field.")
        values[name] = value
    limit = settings.pagination_default_limit
    if "limit" in values:
        raw = values["limit"]
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 3:
            raise APIError("VALIDATION_ERROR", 422, "Invalid page limit.")
        limit = int(raw)
    if not 1 <= limit <= settings.pagination_max_limit:
        raise APIError("VALIDATION_ERROR", 422, "Invalid page limit.")
    return Pagination(limit, values.get("cursor"))
