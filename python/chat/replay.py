"""Relay ordenado en Redis, sin persistir ciphertext ni metadatos de mensajes."""

import json
from uuid import UUID

from chat.config import Settings
from chat.errors import APIError
from chat.identity import Principal
from chat.identity_redis import IdentityRedis, redis_connection
from chat.messaging import authenticate, permitted
from chat.persistence import transaction
from chat.realtime_redis import RealtimeRedis
from chat.replay_dto import ReplayBegin, ReplayFrame, ReplayItem
from chat.ws_protocol import frame

REPLAY_SCRIPT = """
local raw = redis.call('GET',KEYS[1])
local data
if ARGV[1] == 'begin' then
    if raw then return 409 end
    data = cjson.decode(ARGV[2])
else
    if not raw then return 410 end
    data = cjson.decode(raw)
    local incoming = cjson.decode(ARGV[2])
    if data.sender ~= incoming.sender or data.connection ~= incoming.connection
        or data.conversation ~= incoming.conversation then return 404 end
    if data.finished then return 410 end
    if tonumber(ARGV[3]) ~= data.next then return 422 end
end
if redis.call('EXISTS','connection:'..data.connection) == 0
    or redis.call('EXISTS','connection:'..data.target) == 0 then
    redis.call('DEL',KEYS[1])
    return 503
end
if ARGV[1] == 'item' then
    if data.next >= 4294967295 then return 422 end
    data.next = data.next + 1
end
if ARGV[1] == 'end' then data.finished = true end
local ttl = raw and redis.call('PTTL',KEYS[1]) or 900000
if ttl <= 0 then return 410 end
redis.call('SET',KEYS[1],cjson.encode(data),'PX',ttl)
redis.call('PUBLISH','events:user:'..data.recipient,
    cjson.encode({event=cjson.decode(ARGV[4]),connection=data.target}))
return 200
"""


class Replay:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def relay(self, principal: Principal, connection: UUID, message: ReplayFrame) -> None:
        async with transaction() as unit:
            await authenticate(unit, principal)
            row = await permitted(unit, principal.user.id, message.conversation_id)
            if row.status != "active":
                raise APIError("CONVERSATION_NOT_ACTIVE", 409, "Active conversation required.")
            meta = {"sender": str(principal.user.id), "connection": str(connection),
                    "conversation": str(message.conversation_id), "next": 0}
            kind = message.type.rsplit(".", 1)[-1]
            sequence = 0
            if isinstance(message, ReplayBegin):
                peer = await (await unit.connection.execute("""SELECT user_id FROM conversation_members
                    WHERE conversation_id=%s AND user_id<>%s AND membership_status='accepted'""",
                    (row.id, principal.user.id))).fetchone()
                if peer is None:
                    raise APIError("RECIPIENT_DISCONNECTED", 409, "Recipient unavailable.")
                connections = await RealtimeRedis(self.settings).connections(str(peer[0]))
                if not connections:
                    raise APIError("RECIPIENT_DISCONNECTED", 409, "Recipient unavailable.")
                meta.update(recipient=str(peer[0]), target=connections[0])
            elif isinstance(message, ReplayItem):
                sequence = message.payload.sequence
                await IdentityRedis().limit("replay", f"{principal.user.id}:{message.payload.replay_id}",
                                            self.settings.rate_limits.replay)
            else:
                sequence = message.payload.item_count
            event = frame(message.type, row.id, message.payload.model_dump(mode="json"), message.request_id)
            async with redis_connection() as redis:
                result = await redis.eval(REPLAY_SCRIPT, 1, f"recovery:replay:{message.payload.replay_id}",
                                          kind, json.dumps(meta), sequence, json.dumps(event))
            codes = {404: "REPLAY_NOT_FOUND", 409: "REPLAY_CONFLICT", 410: "REPLAY_EXPIRED",
                     422: "REPLAY_SEQUENCE_INVALID", 503: "TEMPORARY_UNAVAILABLE"}
            if result != 200:
                raise APIError(codes.get(result, "TEMPORARY_UNAVAILABLE"), result, "Replay unavailable or out of order.")
