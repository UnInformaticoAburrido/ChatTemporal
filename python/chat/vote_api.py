"""Contratos REST de votaciones (§25.5)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from chat.auth_dependency import Authenticated
from chat.config import Settings
from chat.conversation_dto import VoteSnapshot
from chat.identity import Identity, Principal
from chat.identity_dto import StrictDTO
from chat.protocol import CanonicalUUID
from chat.voting import Voting


class Ballot(StrictDTO):
    choice: bool


def vote_router(identity: Identity, settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.api_prefix)
    service = Voting()
    Member = Annotated[Principal, Depends(Authenticated(identity, settings))]

    @router.get("/votes/{vote_id}")
    async def vote(vote_id: CanonicalUUID, principal: Member) -> VoteSnapshot:
        return await service.get(principal, vote_id)

    @router.post("/votes/{vote_id}/ballots")
    async def ballot(vote_id: CanonicalUUID, data: Ballot, principal: Member) -> VoteSnapshot:
        return await service.cast(principal, vote_id, data.choice)

    return router
