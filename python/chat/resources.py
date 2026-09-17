"""N3: servicios de claves y lecturas; las mutaciones de conversación quedan en N4."""

from uuid import UUID

from chat.errors import APIError
from chat.identity import Principal
from chat.identity_dto import UserPublic
from chat.identity_store import IdentityStore
from chat.pagination import Pagination, decode_cursor, encode_cursor
from chat.persistence import Position, transaction
from chat.protocol import encode_binary
from chat.resource_dto import ConversationSummary, Page, PublicKey, PublicKeyInput, StoredMessage
from chat.resource_store import ConversationRecord, ResourceStore


def summary(row: ConversationRecord) -> ConversationSummary:
    return ConversationSummary(
        id=row.id, mode=row.mode, status=row.status, role=row.role,
        grace_message_limit=row.grace_message_limit, grace_messages_used=row.grace_messages_used,
        created_at=row.created_at, accepted_at=row.accepted_at, mode_changed_at=row.mode_changed_at,
        closed_at=row.closed_at,
        peer=UserPublic(id=row.peer_id, nick=row.peer_nick) if row.peer_id and row.peer_nick else None,
    )


class Resources:
    # DEC-52: exponer ahora las lecturas normativas permite comprobar el cursor
    # con permisos reales; crear/aceptar/cerrar y enviar siguen en N4/N5.
    async def replace_key(self, principal: Principal, data: PublicKeyInput, *, rotate: bool = False) -> PublicKey:
        async with transaction() as unit:
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid, lock=True)
            record = await ResourceStore(unit.connection).replace_key(
                principal.user.id, data.public_key, data.protocol_version, rotate=rotate,
            )
            if record is None:
                raise APIError("USER_NOT_FOUND", 404, "User key not found.")
        return PublicKey.model_validate(record, from_attributes=True)

    async def key(self, principal: Principal, owner: UUID) -> PublicKey:
        async with transaction() as unit:
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid)
            record = await ResourceStore(unit.connection).key(principal.user.id, owner)
            if record is None:
                # DEC-48: misma respuesta sin relación, sin clave o sin usuario;
                # reutilizar el código existente evita un oráculo de existencia.
                raise APIError("USER_NOT_FOUND", 404, "User key not found.")
        return PublicKey.model_validate(record, from_attributes=True)

    async def conversation(self, principal: Principal, conversation: UUID) -> ConversationSummary:
        async with transaction() as unit:
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid)
            record = await ResourceStore(unit.connection).conversation(principal.user.id, conversation)
            if record is None:
                raise APIError("CONVERSATION_NOT_FOUND", 404, "Conversation not found.")
        return summary(record)

    async def conversations(self, principal: Principal, page: Pagination) -> Page[ConversationSummary]:
        before = decode_cursor(page.cursor, "conversations") if page.cursor is not None else None
        async with transaction() as unit:
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid)
            rows = await ResourceStore(unit.connection).conversations(principal.user.id, page.limit, before)
        visible = rows[:page.limit]
        next_cursor = None
        if len(rows) > page.limit:
            last = visible[-1]
            next_cursor = encode_cursor(Position(last.updated_at, last.id), "conversations")
        return Page[ConversationSummary](items=[summary(row) for row in visible], next_cursor=next_cursor)

    async def history(self, principal: Principal, conversation: UUID, page: Pagination) -> Page[StoredMessage]:
        before = decode_cursor(page.cursor, "messages", conversation) if page.cursor is not None else None
        async with transaction() as unit:
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid)
            record = await ResourceStore(unit.connection).conversation(principal.user.id, conversation)
            if record is None:
                raise APIError("CONVERSATION_NOT_FOUND", 404, "Conversation not found.")
            if record.mode != "stored":
                raise APIError("HISTORY_NOT_STORED", 409, "Conversation has no stored history.")
            rows = await unit.messages.history(conversation, principal.user.id, limit=page.limit,
                                               before=before, include_next=True)
        visible = rows[:page.limit]
        next_cursor = None
        if len(rows) > page.limit:
            last = visible[-1]
            next_cursor = encode_cursor(Position(last.sent_at, last.id), "messages", conversation)
        return Page[StoredMessage](items=[StoredMessage(
            message_id=row.id, sender_role=row.sender_role, sent_at=row.sent_at,
            protocol_version=row.protocol_version, ciphertext=encode_binary(row.ciphertext),
            crypto_meta=encode_binary(row.crypto_meta), is_grace_message=row.is_grace_message,
            content_expires_at=row.content_expires_at,
        ) for row in visible], next_cursor=next_cursor)
