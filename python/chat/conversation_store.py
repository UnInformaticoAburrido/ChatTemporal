"""Escrituras N4; el servicio mantiene usuarios → invitación/conversación bloqueados."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import class_row

from chat.conversation_dto import VoteSnapshot
from chat.errors import APIError
from chat.invitation_codes import InvitationToken
from chat.persistence import Mode
from chat.resource_store import ConversationRecord, ResourceStore
from chat.vote_store import VoteStore


@dataclass(frozen=True)
class Invitation:
    id: UUID
    creator_user_id: UUID
    generation: int
    status: str
    created_at: datetime

    def token(self, mode: Mode) -> InvitationToken:
        return InvitationToken(self.id, self.generation, int(self.created_at.timestamp()), mode)


INVITATION_COLUMNS = "id,creator_user_id,generation,status,created_at"


class ConversationStore:
    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def invitation(self, identifier: UUID, *, lock: bool = False) -> Invitation | None:
        async with self.connection.cursor(row_factory=class_row(Invitation)) as cursor:
            await cursor.execute(f"SELECT {INVITATION_COLUMNS} FROM invitations WHERE id=%s"
                                 + (" FOR UPDATE" if lock else ""), (identifier,))
            return await cursor.fetchone()

    async def codes(self, owner: UUID, *, regenerate: bool) -> Invitation:
        # El lock users serializa también el primer GET, cuando aún no existe fila.
        async with self.connection.cursor(row_factory=class_row(Invitation)) as cursor:
            await cursor.execute(f"SELECT {INVITATION_COLUMNS} FROM invitations "
                                 "WHERE creator_user_id=%s AND status='active' FOR UPDATE", (owner,))
            current = await cursor.fetchone()
            if current and not regenerate:
                return current
            previous = await (await self.connection.execute(
                "SELECT COALESCE(MAX(generation),0) FROM invitations WHERE creator_user_id=%s", (owner,),
            )).fetchone()
            assert previous and isinstance(previous[0], int)
            generation = previous[0] + 1
            if generation > 0xFFFFFFFF:
                raise APIError("INVITATION_GENERATION_EXHAUSTED", 409, "Invitation generation exhausted.")
            await self.connection.execute("""UPDATE invitations SET status='revoked',revoked_at=clock_timestamp()
                WHERE creator_user_id=%s AND status='active'""", (owner,))
            await cursor.execute(f"""INSERT INTO invitations(id,creator_user_id,generation,created_at)
                VALUES (%s,%s,%s,date_trunc('second',clock_timestamp())) RETURNING {INVITATION_COLUMNS}""",
                                 (uuid4(), owner, generation))
            created = await cursor.fetchone()
            assert created
            return created

    async def create(self, invitation: Invitation, guest: UUID, mode: Mode) -> UUID:
        identifier = uuid4()
        await self.connection.execute("""INSERT INTO conversations(id,mode,created_from_invitation_id,
            created_at,updated_at) SELECT %s,%s,%s,ts,ts FROM (SELECT clock_timestamp() ts) moment""",
                                      (identifier, mode, invitation.id))
        await self.connection.execute("""INSERT INTO conversation_members(conversation_id,user_id,role)
            VALUES (%s,%s,'host'),(%s,%s,'guest')""",
                                      (identifier, invitation.creator_user_id, identifier, guest))
        await self.connection.execute("""UPDATE invitations SET use_count=use_count+1,
            last_used_at=clock_timestamp() WHERE id=%s""", (invitation.id,))
        return identifier

    async def lock(self, viewer: UUID, identifier: UUID, *, allow_left: bool = False) -> str:
        # Bloquear solo conversations (no el lado nullable de los JOIN de lectura).
        row = await (await self.connection.execute("""SELECT me.membership_status FROM conversations c
            JOIN conversation_members me ON me.conversation_id=c.id
            WHERE c.id=%s AND me.user_id=%s FOR UPDATE OF c""", (identifier, viewer))).fetchone()
        if not row or (row[0] == "left" and not allow_left):
            raise APIError("CONVERSATION_NOT_FOUND", 404, "Conversation not found.")
        return str(row[0])

    async def visible(self, viewer: UUID, identifier: UUID) -> ConversationRecord:
        row = await ResourceStore(self.connection).conversation(viewer, identifier)
        if row is None:
            raise APIError("CONVERSATION_NOT_FOUND", 404, "Conversation not found.")
        return row

    async def accept(self, identifier: UUID) -> None:
        members = await (await self.connection.execute("""SELECT count(*) FROM conversation_members
            WHERE conversation_id=%s AND membership_status<>'left'""", (identifier,))).fetchone()
        if members != (2,):
            raise APIError("CONVERSATION_NOT_ACTIVE", 409, "Two participants required.")
        await self.connection.execute("""UPDATE conversations SET status='active',accepted_at=ts,updated_at=ts
            FROM (SELECT clock_timestamp() ts) moment WHERE id=%s""", (identifier,))
        await self.connection.execute("""UPDATE conversation_members SET membership_status='accepted',
            accepted_at=(SELECT accepted_at FROM conversations WHERE id=%s)
            WHERE conversation_id=%s AND membership_status<>'left'""", (identifier, identifier))
        vote_id = uuid4()
        # Una sola marca de reloj para la aceptación y el plazo exacto de 30 s.
        await self.connection.execute("""INSERT INTO votes(id,conversation_id,subject,eligible_members,
            created_at,expires_at) SELECT %s,c.id,'retain_grace_messages',
            (SELECT count(*) FROM conversation_members WHERE conversation_id=c.id AND membership_status='accepted'),
            c.accepted_at,c.accepted_at+interval '30 seconds' FROM conversations c WHERE c.id=%s""",
                                      (vote_id, identifier))
        await self.connection.execute("""INSERT INTO vote_eligible_members(vote_id,user_id)
            SELECT %s,user_id FROM conversation_members WHERE conversation_id=%s AND membership_status='accepted'""",
                                      (vote_id, identifier))

    async def vote(self, viewer: UUID, identifier: UUID) -> VoteSnapshot:
        row = await (await self.connection.execute("""SELECT id FROM votes
            WHERE conversation_id=%s AND subject='retain_grace_messages'
            ORDER BY created_at DESC,id DESC LIMIT 1""", (identifier,))).fetchone()
        if row is None:
            raise APIError("CONVERSATION_NOT_ACTIVE", 409, "Conversation acceptance unavailable.")
        assert isinstance(row[0], UUID)
        return await VoteStore(self.connection).snapshot(row[0], viewer)

    async def upgrade(self, identifier: UUID, actor: UUID) -> None:
        await self.connection.execute("""UPDATE conversations SET mode='stored',mode_changed_by=%s,
            mode_changed_at=ts,updated_at=ts FROM (SELECT clock_timestamp() ts) moment WHERE id=%s""",
                                      (actor, identifier))

    async def close(self, identifier: UUID, actor: UUID) -> None:
        await self.connection.execute("""UPDATE conversations SET status='closed',closed_by=%s,
            closed_at=ts,updated_at=ts FROM (SELECT clock_timestamp() ts) moment
            WHERE id=%s AND status<>'closed'""", (actor, identifier))

    async def leave(self, identifier: UUID, actor: UUID) -> None:
        await self.connection.execute("""UPDATE conversation_members SET membership_status='left',
            left_at=clock_timestamp() WHERE conversation_id=%s AND user_id=%s AND membership_status<>'left'""",
                                      (identifier, actor))
        await self.close(identifier, actor)
