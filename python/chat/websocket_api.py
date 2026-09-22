"""Sockets independientes por proceso; Redis Pub/Sub enlaza las instancias."""

import asyncio
import contextlib
import json
import time
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError

from chat.config import Settings
from chat.conversations import verified
from chat.errors import APIError, unavailable
from chat.identity import Principal
from chat.identity_redis import IdentityRedis, redis_connection
from chat.identity_store import IdentityStore
from chat.messaging import Messaging, failed
from chat.persistence import transaction
from chat.protocol import decode_binary
from chat.realtime_redis import RealtimeRedis
from chat.reconciliation import reconcile_once
from chat.resource_dto import MessageSend
from chat.ws_protocol import error_frame, frame, parse_frame


def install_websocket(app: FastAPI, settings: Settings) -> None:
    service = Messaging(settings)
    presence = RealtimeRedis(settings)

    @app.websocket(settings.ws_path)
    async def websocket(socket: WebSocket) -> None:
        connection, principal = uuid4(), None
        tasks: list[asyncio.Task[None]] = []
        accepted = False
        close_code = 1000
        send_lock = asyncio.Lock()

        async def send(event: dict[str, Any]) -> None:
            async with send_lock:
                async with asyncio.timeout(5):
                    await socket.send_json(event)

        try:
            if app.state.draining:
                await socket.close(code=1001)
                return
            origin = socket.headers.get("origin")
            if origin is not None and origin not in settings.allowed_origins:
                await socket.close(code=1008)
                return
            if list(socket.query_params.keys()) != ["ticket"] or len(socket.query_params.getlist("ticket")) != 1:
                await socket.close(code=4401)
                return
            try:
                ticket = socket.query_params["ticket"]
                ticket_bytes = decode_binary(ticket, minimum=32, maximum=32)
            except ValueError:
                await socket.close(code=4401)
                return
            # Igual que token_hash en N2: SHA256 de los 32 bytes, no del Base64.
            async with redis_connection() as redis:
                raw = await redis.getdel(f"ws:ticket:{sha256(ticket_bytes).hexdigest()}")
                if raw is None:
                    await socket.close(code=4401)
                    return
                identity = json.loads(raw)
                user_id, sid = UUID(identity["user_id"]), UUID(identity["sid"])
                async with redis.pubsub() as pubsub:
                    await pubsub.subscribe(f"events:user:{user_id}", "auth:session_revoked")
                    # Consumir confirmaciones de suscripción antes de anunciar presencia.
                    subscriptions = 0
                    async with asyncio.timeout(5):
                        while subscriptions < 2:
                            event = await pubsub.get_message(timeout=1)
                            if event and event["type"] == "subscribe":
                                subscriptions += 1
                    async with transaction() as unit:
                        user = await IdentityStore(unit.connection).authenticated(user_id, sid)
                        verified(user)
                    principal = Principal(user, sid)
                    await socket.accept()
                    accepted = True
                    await presence.presence(user_id, sid, connection)
                    ready = frame("session.ready", None, {"user_id": str(user_id), "sid": str(sid),
                                  "server_time": "", "heartbeat_interval_seconds": settings.ws_ping_interval_seconds})
                    ready["payload"]["server_time"] = ready["timestamp"]
                    await send(ready)
                    # Un receptor que conecta durante el plazo aún puede responder
                    # a ofertas emitidas cuando estaba offline. No se reenvía payload.
                    for attempt in await presence.active(recipient=user_id):
                        if attempt.phase == "offer" and attempt.deadline > time.time():
                            await send(frame("message.offer", UUID(attempt.conversation),
                                             {"message_id": attempt.message}, UUID(attempt.request)))

                    async def receive() -> None:
                        assert principal
                        strikes = 0
                        rate_strikes = 0
                        while True:
                            raw_frame = await socket.receive()
                            if raw_frame["type"] == "websocket.disconnect":
                                return
                            if raw_frame.get("bytes") is not None:
                                await socket.close(code=1003)
                                return
                            message = None
                            try:
                                message = parse_frame(raw_frame.get("text", ""), settings)
                                if not await presence.connected(str(connection), str(user_id)):
                                    raise unavailable()
                                await IdentityRedis().limit("ws_frames", str(sid), settings.rate_limits.ws_frames)
                                if isinstance(message, MessageSend):
                                    result = await service.send(principal, connection, message)
                                    if result:
                                        await send(result)
                                elif message.type == "message.offer":
                                    await service.offer(principal, message)
                                elif message.type == "message.ready":
                                    await service.ready(principal, connection, message)
                                else:
                                    await service.ack(principal, connection, message)
                                rate_strikes = 0
                            except APIError as error:
                                if isinstance(message, MessageSend) and error.code in ("CONVERSATION_CLOSED", "VOTE_OPEN"):
                                    await send(failed(message.payload.message_id, message.conversation_id,
                                                      error.code, message.request_id))
                                else:
                                    await send(error_frame(error, message.conversation_id if message else None,
                                                           message.request_id if message else None))
                                if error.status == 401 or error.code == "EMAIL_NOT_VERIFIED":
                                    await socket.close(code=4401)
                                    return
                                if error.status == 413:
                                    await socket.close(code=1009)
                                    return
                                if error.status == 503:
                                    raise
                                if error.status == 429:
                                    rate_strikes += 1
                                    if rate_strikes >= 3:
                                        await socket.close(code=4429)
                                        return
                                elif error.status in (400, 403, 404, 422):
                                    strikes += 1
                                    if strikes >= 3:
                                        await socket.close(code=1008)
                                        return

                    async def events() -> None:
                        while True:
                            event = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                            if not event:
                                continue
                            value = json.loads(event["data"])
                            if event["channel"] == b"auth:session_revoked":
                                if value["sid"] == str(sid):
                                    await socket.close(code=4401)
                                    return
                                continue
                            if value["connection"] not in (None, str(connection)):
                                continue
                            async with transaction() as unit:
                                user = await IdentityStore(unit.connection).authenticated(user_id, sid)
                                verified(user)
                            outgoing = value["event"]
                            if outgoing["conversation_id"]:
                                async with transaction() as unit:
                                    membership = await (await unit.connection.execute("""SELECT membership_status
                                        FROM conversation_members WHERE conversation_id=%s AND user_id=%s""",
                                        (outgoing["conversation_id"], user_id))).fetchone()
                                if (membership is None or (membership[0] == "left"
                                                           and outgoing["type"] != "conversation.closed")):
                                    continue
                            # El listener no reenvía ciphertext ephemeral después de
                            # DEL/timeout, incluso si Pub/Sub lo había encolado antes.
                            if outgoing["type"] == "message.new" and value["connection"]:
                                attempt = await presence.get(outgoing["payload"]["message_id"])
                                if attempt is None or attempt.deadline <= time.time() or not await presence.has_payload(attempt):
                                    continue
                            await send(outgoing)

                    async def monitor() -> None:
                        renewed = time.monotonic()
                        while True:
                            await asyncio.sleep(1)
                            if app.state.draining:
                                await socket.close(code=1001)
                                return
                            # Redis perdido, sesión revocada o email cambiado: cerrar
                            # también sockets ociosos aunque se haya perdido Pub/Sub.
                            if not await presence.connected(str(connection), str(user_id)):
                                raise unavailable()
                            async with transaction() as unit:
                                user = await IdentityStore(unit.connection).authenticated(user_id, sid)
                                verified(user)
                            if time.monotonic()-renewed >= settings.ws_ping_interval_seconds:
                                await presence.presence(user_id, sid, connection, renew=True)
                                renewed = time.monotonic()

                    tasks = [asyncio.create_task(receive()), asyncio.create_task(events()), asyncio.create_task(monitor())]
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
        except APIError as error:
            close_code = 4401 if error.status in (401, 403) else 1011
        except (RedisError, psycopg.OperationalError, TimeoutError):
            close_code = 1011
            if accepted:
                with contextlib.suppress(Exception):
                    await send(error_frame(unavailable()))
        except WebSocketDisconnect:
            pass
        except Exception:
            # No registrar query/ticket ni excepción de dependencias con credenciales.
            close_code = 1011
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await socket.close(code=close_code)
            if principal:
                with contextlib.suppress(Exception):
                    await presence.disconnect(principal.user.id, connection)
                    await reconcile_once(settings, disconnected=connection)
