"""Cola durable solo de metadatos; fallos Push nunca revierten message.send."""

import asyncio
import http.client
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg.rows import dict_row

from chat.config import Settings
from chat.logging import event
from chat.metrics import PUSH
from chat.persistence import UnitOfWork, transaction
from chat.realtime_redis import RealtimeRedis
from chat.web_push import send_push


async def enqueue_push(unit: UnitOfWork, user: UUID, conversation: UUID, message: UUID,
                       kind: str, expires_at: datetime) -> None:
    await unit.connection.execute("""INSERT INTO push_jobs(id,user_id,conversation_id,message_id,event_type,expires_at)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,message_id,event_type) DO NOTHING""",
        (uuid4(), user, conversation, message, kind, expires_at))


async def push_once(settings: Settings) -> int:
    processed = 0
    for _ in range(50):
        async with transaction() as unit:
            async with unit.connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""SELECT * FROM push_jobs WHERE finished_at IS NULL
                    AND next_attempt_at<=clock_timestamp() AND expires_at>clock_timestamp()
                    ORDER BY next_attempt_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""")
                job = await cursor.fetchone()
            if job is None:
                break
            allowed = await (await unit.connection.execute("""SELECT 1 FROM conversation_members
                WHERE conversation_id=%s AND user_id=%s AND membership_status<>'left'""",
                (job["conversation_id"], job["user_id"]))).fetchone()
            if allowed and job["event_type"] == "message.offer":
                redis = RealtimeRedis(settings)
                attempt = await redis.get(job["message_id"])
                allowed = (allowed if attempt and attempt.phase == "offer"
                           and not await redis.connections(str(job["user_id"])) else None)
                if allowed:
                    allowed = await (await unit.connection.execute("""SELECT 1 FROM conversations c WHERE c.id=%s
                        AND c.status<>'closed' AND c.mode='ephemeral' AND NOT EXISTS (SELECT 1 FROM votes v
                            WHERE v.conversation_id=c.id AND v.status='open' AND v.expires_at>clock_timestamp())""",
                        (job["conversation_id"],))).fetchone()
            pending = False
            if allowed:
                subscriptions = await (await unit.connection.execute("""SELECT s.id,s.endpoint,s.p256dh,s.auth_secret
                    FROM web_push_subscriptions s JOIN auth_sessions a ON a.id=s.session_id
                    WHERE s.user_id=%s AND s.revoked_at IS NULL AND a.revoked_at IS NULL
                    AND a.expires_at>clock_timestamp() AND NOT EXISTS (SELECT 1 FROM push_job_deliveries d
                        WHERE d.job_id=%s AND d.subscription_id=s.id) ORDER BY s.id""",
                    (job["user_id"], job["id"]))).fetchall()
                payload = json.dumps({"event_type": job["event_type"], "conversation_id": str(job["conversation_id"]),
                                      "message_id": str(job["message_id"])}).encode()
                for subscription in subscriptions:
                    ttl = int((job["expires_at"] - datetime.now(UTC)).total_seconds())
                    if ttl <= 0:
                        break
                    try:
                        status = await asyncio.to_thread(send_push, settings, str(subscription[1]),
                                                         str(subscription[2]), str(subscription[3]), payload, ttl)
                    except (OSError, ValueError, http.client.HTTPException):
                        status = 503
                    if not 200 <= status < 300:
                        PUSH.labels(str(status // 100) + "xx").inc()
                    if status in (404, 410):
                        await unit.connection.execute("""UPDATE web_push_subscriptions SET revoked_at=clock_timestamp()
                            WHERE id=%s AND p256dh=%s AND auth_secret=%s""", (subscription[0], subscription[2], subscription[3]))
                    elif not 200 <= status < 300:
                        pending = True
                        event("push_failed", service="worker", level="WARNING", status=status)
                        continue
                    await unit.connection.execute("""INSERT INTO push_job_deliveries(job_id,subscription_id)
                        VALUES (%s,%s) ON CONFLICT DO NOTHING""", (job["id"], subscription[0]))
            if pending and job["attempts"] < 4:
                await unit.connection.execute("""UPDATE push_jobs SET attempts=attempts+1,
                    next_attempt_at=clock_timestamp()+(%s * interval '1 second') WHERE id=%s""",
                    (min(300, 2 ** (job["attempts"] + 1)), job["id"]))
            else:
                await unit.connection.execute("UPDATE push_jobs SET finished_at=clock_timestamp() WHERE id=%s", (job["id"],))
            processed += 1
    return processed
