"""Endpoints N4. Publicación de eventos WS y cancelación de offers corresponden a N5."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from chat.auth_dependency import Authenticated
from chat.config import Settings
from chat.conversation_dto import AcceptedConversation, InvitationCodes, Redeem, RedeemedConversation
from chat.conversations import ConversationService
from chat.identity import Identity, Principal
from chat.identity_redis import client_ip
from chat.protocol import CanonicalUUID
from chat.resource_dto import ConversationSummary


def conversation_router(identity: Identity, settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.api_prefix)
    service = ConversationService()
    Member = Annotated[Principal, Depends(Authenticated(identity, settings))]

    @router.get("/invitations/me")
    async def codes(principal: Member) -> InvitationCodes:
        return await service.codes(principal)

    @router.post("/invitations/regenerate", status_code=201)
    async def regenerate(principal: Member) -> InvitationCodes:
        return await service.codes(principal, regenerate=True)

    @router.post("/invitations/redeem", status_code=201)
    async def redeem(data: Redeem, request: Request, principal: Member) -> RedeemedConversation:
        await identity.redis.limit("redeem_user", str(principal.user.id), settings.rate_limits.redeem_user)
        await identity.redis.limit("redeem_ip", await client_ip(request, settings), settings.rate_limits.redeem_ip)
        return await service.redeem(principal, data.code)

    @router.post("/conversations/{conversation_id}/accept")
    async def accept(conversation_id: CanonicalUUID, principal: Member) -> AcceptedConversation:
        result = await service.change(principal, conversation_id, "accept")
        assert isinstance(result, AcceptedConversation)
        return result

    @router.post("/conversations/{conversation_id}/upgrade")
    async def upgrade(conversation_id: CanonicalUUID, principal: Member) -> ConversationSummary:
        result = await service.change(principal, conversation_id, "upgrade")
        assert isinstance(result, ConversationSummary)
        return result

    @router.delete("/conversations/{conversation_id}", status_code=204)
    async def close(conversation_id: CanonicalUUID, principal: Member) -> Response:
        await service.change(principal, conversation_id, "close")
        return Response(status_code=204)

    @router.post("/conversations/{conversation_id}/leave", status_code=204)
    async def leave(conversation_id: CanonicalUUID, principal: Member) -> Response:
        await service.change(principal, conversation_id, "leave")
        return Response(status_code=204)

    return router
