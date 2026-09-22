from typing import Annotated

from fastapi import APIRouter, Depends, Request

from chat.auth_dependency import Authenticated
from chat.config import Settings
from chat.identity import Identity, Principal
from chat.messaging import Messaging
from chat.pagination import pagination
from chat.protocol import CanonicalUUID
from chat.resource_dto import ConversationSummary, Page, PublicKey, PublicKeyInput, StoredMessage
from chat.resources import Resources
from chat.ws_protocol import DeliveryStatus


def resource_router(identity: Identity, settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.api_prefix)
    service = Resources()
    PrincipalDependency = Annotated[Principal, Depends(Authenticated(identity, settings))]

    @router.get("/messages/{message_id}/status")
    async def delivery_status(message_id: CanonicalUUID, principal: PrincipalDependency) -> DeliveryStatus:
        return await Messaging(settings).status(principal, message_id)

    @router.put("/users/me/keys")
    async def put_key(data: PublicKeyInput, principal: PrincipalDependency) -> PublicKey:
        return await service.replace_key(principal, data)

    @router.post("/users/me/keys/rotate")
    async def rotate_key(data: PublicKeyInput, principal: PrincipalDependency) -> PublicKey:
        return await service.replace_key(principal, data, rotate=True)

    @router.get("/users/{user_id}/keys")
    async def get_key(user_id: CanonicalUUID, principal: PrincipalDependency) -> PublicKey:
        return await service.key(principal, user_id)

    @router.get("/conversations")
    async def conversations(request: Request, principal: PrincipalDependency) -> Page[ConversationSummary]:
        return await service.conversations(principal, pagination(request, settings))

    @router.get("/conversations/{conversation_id}")
    async def conversation(conversation_id: CanonicalUUID, principal: PrincipalDependency) -> ConversationSummary:
        return await service.conversation(principal, conversation_id)

    @router.get("/conversations/{conversation_id}/messages")
    async def history(conversation_id: CanonicalUUID, request: Request,
                      principal: PrincipalDependency) -> Page[StoredMessage]:
        return await service.history(principal, conversation_id, pagination(request, settings))

    return router
