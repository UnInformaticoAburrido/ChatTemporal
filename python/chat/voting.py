"""N6: votaciones REST y cierre automático; notificaciones siempre tras commit."""

from uuid import UUID

from redis.exceptions import RedisError

from chat.conversation_dto import VoteSnapshot
from chat.errors import APIError, unavailable
from chat.identity import Principal
from chat.identity_store import IdentityStore
from chat.logging import event
from chat.persistence import transaction
from chat.realtime_redis import publish
from chat.vote_store import VoteEvents, VoteStore


async def publish_votes(events: VoteEvents) -> None:
    failed = False
    for user, payload in events:
        try:
            await publish(user, payload)
        except (RedisError, OSError):
            failed = True
    if failed:
        raise unavailable()


class Voting:
    async def get(self, principal: Principal, identifier: UUID) -> VoteSnapshot:
        return await self._access(principal, identifier)

    async def cast(self, principal: Principal, identifier: UUID, choice: bool) -> VoteSnapshot:
        return await self._access(principal, identifier, choice)

    async def _access(self, principal: Principal, identifier: UUID,
                      choice: bool | None = None) -> VoteSnapshot:
        events: VoteEvents = []
        error = None
        async with transaction() as unit:
            await unit.connection.execute("SELECT id FROM users WHERE id=%s FOR NO KEY UPDATE",
                                          (principal.user.id,))
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid)
            store = VoteStore(unit.connection)
            await store.lock(identifier, principal.user.id)
            changed = await store.resolve(identifier)
            snapshot = await store.snapshot(identifier, principal.user.id)
            if choice is not None:
                if snapshot.my_vote is not None:
                    if snapshot.my_vote != choice:
                        error = APIError("VOTE_CONFLICT", 409, "Ballot cannot be changed.")
                elif not await store.ballot(identifier, principal.user.id, choice):
                    changed = await store.resolve(identifier) or changed
                    error = APIError("VOTE_EXPIRED", 410, "Voting has ended.")
                else:
                    changed = True
                snapshot = await store.snapshot(identifier, principal.user.id)
            if changed:
                events = await store.events(identifier)
        # Un POST tardío también confirma el cierre y la purga antes del error.
        await publish_votes(events)
        if error is not None:
            raise error
        return snapshot


async def resolve_votes_once() -> int:
    """Lote acotado; cada voto confirma independientemente de fallos de Pub/Sub."""
    async with transaction() as unit:
        rows = await (await unit.connection.execute("""SELECT id FROM votes
            WHERE status='open' AND expires_at<=clock_timestamp()
            ORDER BY expires_at,id LIMIT 1000""")).fetchall()
    closed = 0
    for row in rows:
        assert isinstance(row[0], UUID)
        events: VoteEvents = []
        try:
            async with transaction() as unit:
                store = VoteStore(unit.connection)
                await store.lock(row[0])
                if await store.resolve(row[0]):
                    events = await store.events(row[0])
                    closed += 1
        except APIError as error:
            if error.code == "VOTE_NOT_FOUND":
                continue  # La conversación pudo eliminarse entre consulta y lock.
            event("vote_resolution_failed", service="worker", level="ERROR", error_code=error.code)
            continue
        try:
            await publish_votes(events)
        except APIError:
            event("vote_publish_failed", service="worker", level="ERROR", error_code="TEMPORARY_UNAVAILABLE")
    return closed
