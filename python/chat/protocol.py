"""Bytes, identificadores y tiempos estrictos del protocolo v1 (§23)."""

import base64
import binascii
import re
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BeforeValidator
from pydantic_core import PydanticCustomError


def encode_binary(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON field")
        result[name] = value
    return result


def invalid_json_constant(value: str) -> Any:
    raise ValueError("Non-finite JSON number")


def decode_binary(value: object, *, maximum: int, minimum: int = 0) -> bytes:
    if (not isinstance(value, str) or len(value) > (maximum * 8 + 5) // 6
            or re.fullmatch(r"[A-Za-z0-9_-]*", value, flags=re.ASCII) is None):
        raise ValueError("Invalid Base64URL")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid Base64URL") from None
    if not minimum <= len(decoded) <= maximum or encode_binary(decoded) != value:
        raise ValueError("Invalid Base64URL")
    return decoded


def canonical_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("Invalid UUID")
    result = UUID(value)
    if str(result) != value:
        raise ValueError("Invalid UUID")
    return result


def uuid4_value(value: object) -> UUID:
    result = canonical_uuid(value)
    if result.version != 4:
        raise ValueError("UUIDv4 required")
    return result


def protocol_version(value: object) -> int:
    if type(value) is not int:
        raise ValueError("Integer protocol_version required")
    if value != 1:
        raise PydanticCustomError("unsupported_protocol_version", "Unsupported protocol version")
    return value


def utc_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        # DEC-45: precisión de microsegundos, igual a TIMESTAMPTZ/cursores de BD.
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)",
                        value, flags=re.ASCII) is None:
            raise ValueError("UTC RFC3339 timestamp required")
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("UTC RFC3339 timestamp required")
    return value.astimezone(UTC)


CanonicalUUID = Annotated[UUID, BeforeValidator(canonical_uuid)]
UUID4 = Annotated[UUID, BeforeValidator(uuid4_value)]
ProtocolVersion = Annotated[int, BeforeValidator(protocol_version)]
UTCDateTime = Annotated[datetime, BeforeValidator(utc_timestamp)]
