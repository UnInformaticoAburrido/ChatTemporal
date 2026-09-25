"""Frames de recuperación §26.4, con el mismo cifrado opaco que message.send."""

from typing import Literal

from pydantic import Field, field_validator

from chat.identity_dto import StrictDTO
from chat.protocol import UUID4, CanonicalUUID, ProtocolVersion, UTCDateTime, decode_binary


class ReplayReference(StrictDTO):
    replay_id: UUID4


class ReplayPayload(ReplayReference):
    sequence: int = Field(ge=0, le=0xFFFFFFFF)
    original_message_id: CanonicalUUID | None
    original_timestamp: UTCDateTime | None
    protocol_version: ProtocolVersion
    ciphertext: str = Field(repr=False)
    crypto_meta: str = Field(repr=False)

    @field_validator("crypto_meta")
    @classmethod
    def check_meta(cls, value: str) -> str:
        decode_binary(value, minimum=56, maximum=56)
        return value


class ReplayFinished(ReplayReference):
    item_count: int = Field(ge=0, le=0xFFFFFFFF)


class ReplayBegin(StrictDTO):
    type: Literal["recovery.replay.begin"]
    request_id: UUID4
    conversation_id: CanonicalUUID
    timestamp: UTCDateTime
    payload: ReplayReference


class ReplayItem(StrictDTO):
    type: Literal["recovery.replay.item"]
    request_id: UUID4
    conversation_id: CanonicalUUID
    timestamp: UTCDateTime
    payload: ReplayPayload


class ReplayEnd(StrictDTO):
    type: Literal["recovery.replay.end"]
    request_id: UUID4
    conversation_id: CanonicalUUID
    timestamp: UTCDateTime
    payload: ReplayFinished


ReplayFrame = ReplayBegin | ReplayItem | ReplayEnd
