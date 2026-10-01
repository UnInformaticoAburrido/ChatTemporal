"""N7: blob opaco con una carga y deadline absoluto, sin secretos QR."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field
from redis.exceptions import RedisError

from chat.config import Settings
from chat.errors import APIError, unavailable
from chat.identity import Principal
from chat.identity_dto import StrictDTO
from chat.identity_redis import redis_connection
from chat.identity_store import IdentityStore
from chat.persistence import transaction
from chat.protocol import CanonicalUUID, UTCDateTime, decode_binary


class TransferCreated(StrictDTO):
    transfer_id: CanonicalUUID
    expires_at: UTCDateTime
    max_blob_bytes: int


class TransferBlob(StrictDTO):
    encrypted_blob: str = Field(repr=False)
    expires_at: UTCDateTime


TRANSFER_SCRIPT = """
local raw = redis.call('GET',KEYS[1])
if not raw then return {410} end
local meta = cjson.decode(raw)
if meta.owner ~= ARGV[1] or meta.target ~= ARGV[2] then return {404} end
local ttl = redis.call('PTTL',KEYS[1])
if ttl <= 0 then return {410} end
if ARGV[3] == 'put' then
    if redis.call('EXISTS',KEYS[2]) == 1 then return {409} end
    redis.call('SET',KEYS[2],ARGV[4],'PX',ttl)
    return {204}
elseif ARGV[3] == 'delete' then
    redis.call('DEL',KEYS[1],KEYS[2])
    return {204}
end
local blob = redis.call('GET',KEYS[2])
if not blob then return {202} end
return {200,blob,tostring(meta.expires)}
"""


class Transfers:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def create(self, principal: Principal) -> TransferCreated:
        identifier = uuid4()
        async with transaction() as unit:
            await IdentityStore(unit.connection).authenticated(principal.user.id, principal.sid, lock=True)
            try:
                async with redis_connection() as redis:
                    seconds, micros = await redis.time()
                    deadline = seconds + micros / 1_000_000 + 86400
                    meta = json.dumps({"owner": str(principal.user.id), "target": str(principal.sid), "expires": deadline})
                    await redis.set(f"key_transfer_meta:{identifier}", meta, pxat=int(deadline * 1000), nx=True)
            except (RedisError, OSError):
                raise unavailable() from None
        return TransferCreated(transfer_id=identifier, expires_at=datetime.fromtimestamp(deadline, UTC),
                               max_blob_bytes=self.settings.max_key_transfer_blob_bytes)

    async def access(self, principal: Principal, identifier: UUID, action: str,
                     blob: str = "") -> TransferBlob | None:
        if action == "put":
            if len(blob) > (self.settings.max_key_transfer_blob_bytes * 8 + 5) // 6:
                raise APIError("PAYLOAD_TOO_LARGE", 413, "Encrypted blob too large.")
            try:
                raw = decode_binary(blob, minimum=1, maximum=65536)
            except ValueError:
                raise APIError("VALIDATION_ERROR", 422, "Invalid encrypted blob.") from None
            if len(raw) > self.settings.max_key_transfer_blob_bytes:
                raise APIError("PAYLOAD_TOO_LARGE", 413, "Encrypted blob too large.")
        async with transaction() as unit:
            store = IdentityStore(unit.connection)
            await store.authenticated(principal.user.id, principal.sid, lock=True, transfer_upload=action == "put")
            target = principal.sid
            if action == "put":
                grant = await (await unit.connection.execute("""SELECT target_sid FROM transfer_upload_grants
                    WHERE user_id=%s AND source_sid=%s AND expires_at>clock_timestamp()""",
                    (principal.user.id, principal.sid))).fetchone()
                if grant:
                    assert isinstance(grant[0], UUID)
                    target = grant[0]
            try:
                async with redis_connection() as redis:
                    result = await redis.eval(TRANSFER_SCRIPT, 2, f"key_transfer_meta:{identifier}",
                                              f"key_transfer:{identifier}", str(principal.user.id), str(target), action, blob)
            except (RedisError, OSError):
                raise unavailable() from None
            if result[0] == 404:
                raise APIError("KEY_TRANSFER_NOT_FOUND", 404, "Transfer not found.")
            if result[0] == 410:
                raise APIError("KEY_TRANSFER_EXPIRED", 410, "Transfer expired or removed.")
            if result[0] == 409:
                raise APIError("KEY_TRANSFER_CONFLICT", 409, "Transfer already uploaded.")
            if result[0] == 202:
                raise APIError("KEY_TRANSFER_NOT_READY", 409, "Transfer not yet uploaded.")
            if action == "delete":
                await unit.connection.execute("DELETE FROM transfer_upload_grants WHERE user_id=%s AND target_sid=%s",
                                              (principal.user.id, principal.sid))
            if action == "get":
                return TransferBlob(encrypted_blob=result[1].decode(), expires_at=datetime.fromtimestamp(float(result[2]), UTC))
        return None
