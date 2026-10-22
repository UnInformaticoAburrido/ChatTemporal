"""N4: autorizaciones, privacidad y transiciones atómicas de conversaciones 1:1."""

import os
from pathlib import Path
from typing import Literal
from uuid import UUID

from chat.conversation_dto import AcceptedConversation, InvitationCodes, RedeemedConversation
from chat.conversation_store import ConversationStore
from chat.errors import APIError
from chat.identity import Principal
from chat.identity_store import IdentityStore
from chat.invitation_codes import decode_code, encode_code
from chat.persistence import User, transaction
from chat.resource_dto import ConversationSummary
from chat.resource_store import ResourceStore
from chat.resources import summary


def verified(user: User) -> None:
    if not user.email_verified:
        raise APIError("EMAIL_NOT_VERIFIED", 403, "Email verification required.")


def invitation_secret() -> bytes:
    # Archivo binario, sin strip/decodificación que alteren el secreto CSPRNG.
    return Path(os.environ["INVITATION_HMAC_SECRET_FILE"]).read_bytes()


class ConversationService:
    async def codes(self, principal: Principal, *, regenerate: bool = False) -> InvitationCodes:
        secret = invitation_secret()
        async with transaction() as unit:
            user = await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid, lock=True)
            verified(user)
            invitation = await ConversationStore(unit.connection).codes(user.id, regenerate=regenerate)
            return InvitationCodes(ephemeral_code=encode_code(invitation.token("ephemeral"), secret),
                                   stored_code=encode_code(invitation.token("stored"), secret),
                                   generation=invitation.generation, created_at=invitation.created_at)

    async def redeem(self, principal: Principal, code: str) -> RedeemedConversation:
        token = decode_code(code, invitation_secret())
        async with transaction() as unit:
            store = ConversationStore(unit.connection)
            invitation = await store.invitation(token.id)
            if invitation is None:
                raise APIError("INVITATION_REVOKED", 410, "Invitation unavailable.")
            host = invitation.creator_user_id
            if host == principal.user.id:
                raise APIError("INVITATION_INVALID", 400, "Cannot redeem own invitation.")
            # Orden UUID total: dos usuarios canjeándose mutuamente no invierten locks.
            await unit.connection.execute("SELECT id FROM users WHERE id=ANY(%s) ORDER BY id FOR UPDATE",
                                          ([host, principal.user.id],))
            identity = IdentityStore(unit.connection)
            guest = await identity.authenticated(principal.user.id, principal.sid)
            verified(guest)
            owner = await identity.user(host)
            invitation = await store.invitation(token.id, lock=True)
            if (owner is None or not owner.is_active or not owner.email_verified or invitation is None
                    or invitation.status != "active" or invitation.generation != token.generation
                    or int(invitation.created_at.timestamp()) != token.created_at):
                raise APIError("INVITATION_REVOKED", 410, "Invitation unavailable.")
            key = await ResourceStore(unit.connection).key(host, host)
            if key is None:
                raise APIError("HOST_KEY_UNAVAILABLE", 409, "Invitation key unavailable.")
            identifier = await store.create(invitation, guest.id, token.mode)
            conversation = summary(await store.visible(guest.id, identifier))
            return RedeemedConversation(**conversation.model_dump(), host_public_key=key.public_key)

    async def change(self, principal: Principal, identifier: UUID,
                     action: Literal["accept", "upgrade", "close", "leave"],
                     ) -> AcceptedConversation | ConversationSummary | None:
        async with transaction() as unit:
            # Compatible con KEY SHARE de las FK de mensajes/entregas. FOR UPDATE
            # aquí produciría un deadlock: usuario → conversación → FK usuario.
            # NO KEY UPDATE sigue excluyendo revocación/borrado/cambio de identidad.
            await unit.connection.execute("SELECT id FROM users WHERE id=%s FOR NO KEY UPDATE",
                                          (principal.user.id,))
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid)
            store = ConversationStore(unit.connection)
            membership = await store.lock(principal.user.id, identifier, allow_left=action == "leave")
            if action == "leave":
                if membership != "left":
                    await store.leave(identifier, principal.user.id)
                return None
            row = await store.visible(principal.user.id, identifier)
            if action == "close":
                await store.close(identifier, principal.user.id)
                return None
            if row.role != "host":
                raise APIError("NOT_CONVERSATION_HOST", 403, "Only the host can perform this action.")
            if row.status == "closed":
                raise APIError("CONVERSATION_CLOSED", 409, "Conversation closed.")
            if action == "accept":
                if row.status == "pending":
                    await store.accept(identifier)
                return AcceptedConversation(conversation=summary(await store.visible(principal.user.id, identifier)),
                                            vote=await store.vote(principal.user.id, identifier))
            if row.status != "active":
                raise APIError("CONVERSATION_NOT_ACTIVE", 409, "Active conversation required.")
            if row.mode == "ephemeral":
                await store.upgrade(identifier, principal.user.id)
            return summary(await store.visible(principal.user.id, identifier))
