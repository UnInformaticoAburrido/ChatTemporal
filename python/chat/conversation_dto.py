"""Contratos REST de invitaciones, transiciones y apertura de voto (§25.4)."""

from typing import Literal

from pydantic import Field

from chat.identity_dto import StrictDTO
from chat.protocol import CanonicalUUID, UTCDateTime
from chat.resource_dto import ConversationSummary


class InvitationCodes(StrictDTO):
    ephemeral_code: str = Field(repr=False)
    stored_code: str = Field(repr=False)
    generation: int
    created_at: UTCDateTime


class Redeem(StrictDTO):
    # La longitud/forma se valida en el codec para devolver INVITATION_INVALID (400).
    code: str = Field(repr=False)


class RedeemedConversation(ConversationSummary):
    host_public_key: str


class VoteSnapshot(StrictDTO):
    id: CanonicalUUID
    conversation_id: CanonicalUUID
    subject: str
    policy: str
    eligible_members: int
    yes_votes: int
    no_votes: int
    status: Literal["open", "approved", "rejected", "cancelled"]
    expires_at: UTCDateTime
    my_vote: bool | None


class AcceptedConversation(StrictDTO):
    conversation: ConversationSummary
    vote: VoteSnapshot
