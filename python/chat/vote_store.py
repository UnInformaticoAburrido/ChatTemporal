"""N6: censo, ballots y resolución bajo el lock de la conversación."""

from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from chat.conversation_dto import VoteSnapshot
from chat.errors import APIError
from chat.ws_protocol import frame

VoteEvents = list[tuple[UUID, dict[str, Any]]]


def majority_absolute(yes_votes: int, eligible_members: int) -> Literal["approved", "rejected"]:
    return "approved" if yes_votes * 2 > eligible_members else "rejected"


class VoteStore:
    def __init__(self, connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        self.connection = connection

    async def lock(self, identifier: UUID, viewer: UUID | None = None) -> UUID:
        # Mismo orden que send/accept/leave: conversación → voto. El worker no
        # bloquea users. Una lectura inicial nunca concede acceso por sí sola.
        row = await (await self.connection.execute("""SELECT c.id FROM conversations c
            JOIN votes v ON v.conversation_id=c.id WHERE v.id=%s FOR UPDATE OF c""",
            (identifier,))).fetchone()
        if row is None:
            raise APIError("VOTE_NOT_FOUND", 404, "Vote not found.")
        if viewer is not None:
            allowed = await (await self.connection.execute("""SELECT 1 FROM vote_eligible_members e
                JOIN conversation_members m ON m.user_id=e.user_id AND m.conversation_id=%s
                WHERE e.vote_id=%s AND e.user_id=%s AND m.membership_status<>'left'""",
                (row[0], identifier, viewer))).fetchone()
            if allowed is None:
                raise APIError("VOTE_NOT_FOUND", 404, "Vote not found.")
        await self.connection.execute("SELECT id FROM votes WHERE id=%s FOR UPDATE", (identifier,))
        valid = await (await self.connection.execute("""SELECT 1 FROM votes v WHERE id=%s
            AND vote_type='majority_absolute' AND subject='retain_grace_messages'
            AND eligible_members=(SELECT count(*) FROM vote_eligible_members WHERE vote_id=v.id)
            AND NOT EXISTS (SELECT 1 FROM vote_ballots b WHERE b.vote_id=v.id AND NOT EXISTS
                (SELECT 1 FROM vote_eligible_members e WHERE e.vote_id=b.vote_id AND e.user_id=b.user_id))""",
            (identifier,))).fetchone()
        if valid is None:
            raise APIError("VOTE_UNSUPPORTED", 409, "Vote policy or electorate unavailable.")
        assert isinstance(row[0], UUID)
        return row[0]

    async def snapshot(self, identifier: UUID, viewer: UUID | None = None) -> VoteSnapshot:
        async with self.connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("""SELECT v.id,v.conversation_id,v.subject,v.vote_type AS policy,
                v.eligible_members,v.status,v.expires_at,
                (SELECT count(*) FROM vote_ballots b WHERE b.vote_id=v.id AND b.choice) AS yes_votes,
                CASE WHEN v.status IN ('approved','rejected') THEN v.eligible_members -
                    (SELECT count(*) FROM vote_ballots b WHERE b.vote_id=v.id AND b.choice)
                ELSE (SELECT count(*) FROM vote_ballots b WHERE b.vote_id=v.id AND NOT b.choice) END AS no_votes,
                (SELECT choice FROM vote_ballots b WHERE b.vote_id=v.id AND b.user_id=%s) AS my_vote
                FROM votes v WHERE v.id=%s""", (viewer, identifier))
            row = await cursor.fetchone()
            if row is None:
                raise APIError("VOTE_NOT_FOUND", 404, "Vote not found.")
            return VoteSnapshot.model_validate(row)

    async def resolve(self, identifier: UUID) -> bool:
        """El llamante tiene lock; estado y borrado hacen commit juntos."""
        due = await (await self.connection.execute("""SELECT 1 FROM votes WHERE id=%s
            AND status='open' AND expires_at<=clock_timestamp()""", (identifier,))).fetchone()
        if due is None:
            return False
        snapshot = await self.snapshot(identifier)
        result = majority_absolute(snapshot.yes_votes, snapshot.eligible_members)
        await self.connection.execute("""UPDATE votes SET status=%s,closed_at=expires_at
            WHERE id=%s""", (result, identifier))
        if result == "rejected":
            # El modo actual puede haber cambiado; solo existe contenido stored.
            # Mantener events/deliveries/fingerprint y el contador de gracia.
            await self.connection.execute("""DELETE FROM messages m USING message_events e
                WHERE m.id=e.id AND e.conversation_id=%s AND m.is_grace_message""",
                (snapshot.conversation_id,))
        return True

    async def ballot(self, identifier: UUID, viewer: UUID, choice: bool) -> bool:
        # El instante de inserción decide, incluso si el plazo venció entre la
        # resolución previa y esta sentencia. Nunca usar now() de la transacción.
        row = await (await self.connection.execute("""WITH moment AS (SELECT clock_timestamp() ts)
            INSERT INTO vote_ballots(vote_id,user_id,choice,voted_at)
            SELECT v.id,%s,%s,ts FROM votes v CROSS JOIN moment
            WHERE v.id=%s AND v.status='open' AND v.expires_at>ts RETURNING vote_id""",
            (viewer, choice, identifier))).fetchone()
        return row is not None

    async def events(self, identifier: UUID, kind: str = "vote.updated") -> VoteEvents:
        rows = await (await self.connection.execute("""SELECT e.user_id FROM vote_eligible_members e
            JOIN votes v ON v.id=e.vote_id
            JOIN conversation_members m ON m.conversation_id=v.conversation_id AND m.user_id=e.user_id
            JOIN users u ON u.id=e.user_id AND u.is_active
            WHERE e.vote_id=%s AND m.membership_status<>'left' ORDER BY e.user_id""",
            (identifier,))).fetchall()
        events: VoteEvents = []
        for row in rows:
            assert isinstance(row[0], UUID)
            snapshot = await self.snapshot(identifier, row[0])
            events.append((row[0], frame(kind, snapshot.conversation_id, snapshot.model_dump(mode="json"))))
        return events
