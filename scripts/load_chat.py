"""Carga online N9 con cuentas dedicadas; no crea usuarios ni modifica claves."""

import argparse
import asyncio
import contextlib
import json
import math
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from websockets.asyncio.client import connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from chat.protocol import decode_binary, encode_binary, unique_json_object  # noqa: E402
from chat.ws_protocol import frame  # noqa: E402
from chat_client.crypto import EncryptedMessage, KeyPair, decrypt_text, encrypt_text  # noqa: E402


class LoadFailure(Exception):
    """Solo códigos controlados, nunca respuestas/URLs/excepciones crudas."""


@dataclass(frozen=True)
class Pair:
    conversation: UUID
    mode: str
    sender_token: str = field(repr=False)
    recipient_token: str = field(repr=False)
    sender: KeyPair = field(repr=False)
    recipient: KeyPair = field(repr=False)


def read_pairs(path: Path) -> list[Pair]:
    if path.stat().st_mode & 0o077:
        raise ValueError("El archivo de cuentas debe tener permisos 0600")
    data = json.loads(path.read_text(), object_pairs_hook=unique_json_object)
    if not isinstance(data, list) or not data:
        raise ValueError("Se requiere una lista de parejas dedicadas")
    result = []
    fields = {"conversation_id", "mode", "sender_token", "recipient_token",
              "sender_private_key", "recipient_private_key"}
    for item in data:
        if not isinstance(item, dict) or set(item) != fields or item["mode"] not in ("stored", "ephemeral"):
            raise ValueError("Formato de pareja inválido")
        if any(not isinstance(item[key], str) or not item[key] for key in fields):
            raise ValueError("Los campos deben ser cadenas no vacías")
        identifier = UUID(item["conversation_id"])
        if str(identifier) != item["conversation_id"]:
            raise ValueError("UUID no canónico")
        result.append(Pair(identifier, item["mode"], item["sender_token"], item["recipient_token"],
                           KeyPair(decode_binary(item["sender_private_key"], minimum=32, maximum=32)),
                           KeyPair(decode_binary(item["recipient_private_key"], minimum=32, maximum=32))))
    if len({pair.conversation for pair in result}) != len(result):
        raise ValueError("No reutilizar conversaciones entre parejas")
    return result


def validate_target(base: str, allow_local_http: bool) -> str:
    value = urlsplit(base)
    if (not value.hostname or value.username or value.password or value.query or value.fragment
            or value.path not in ("", "/")):
        raise ValueError("Indica únicamente el origen del servidor")
    if value.scheme != "https" and not (allow_local_http and value.scheme == "http"
                                        and value.hostname in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("HTTPS obligatorio; HTTP solo se permite explícitamente en loopback")
    return base.rstrip("/")


async def request(api: httpx.AsyncClient, method: str, path: str, status: int = 200) -> dict:
    response = await api.request(method, "/api/v1" + path)
    if response.status_code != status:
        raise LoadFailure(f"HTTP_{response.status_code}")
    return response.json()


async def received(socket, kind: str, message: str | None, timeout: float) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            value = json.loads(await socket.recv())
            if value["type"] in ("system.error", "message.failed"):
                raise LoadFailure("WS_REMOTE_ERROR")
            if value["type"] == kind:
                if message is not None and value["payload"].get("message_id") != message:
                    raise LoadFailure("UNEXPECTED_MESSAGE")
                return value["payload"]


async def history(api: httpx.AsyncClient, pair: Pair, expected: dict[str, str]) -> None:
    path = f"/conversations/{pair.conversation}/messages"
    if pair.mode == "ephemeral":
        body = await request(api, "GET", path, 409)
        if body.get("error", {}).get("code") != "HISTORY_NOT_STORED":
            raise LoadFailure("EPHEMERAL_HISTORY_CONTRACT")
        return
    cursor = None
    cursors: set[str] = set()
    seen: set[str] = set()
    while True:
        response = await api.get("/api/v1" + path, params={"limit": 100, **({"cursor": cursor} if cursor else {})})
        if response.status_code != 200:
            raise LoadFailure(f"HTTP_{response.status_code}")
        page = response.json()
        for item in page["items"]:
            identifier = item["message_id"]
            if identifier in seen or identifier not in expected:
                raise LoadFailure("HISTORY_DUPLICATE_OR_UNEXPECTED")
            clear = decrypt_text(EncryptedMessage(
                decode_binary(item["ciphertext"], maximum=4096),
                decode_binary(item["crypto_meta"], minimum=56, maximum=56), item["protocol_version"]), pair.recipient)
            if clear != expected[identifier]:
                raise LoadFailure("HISTORY_CORRUPTED")
            seen.add(identifier)
        cursor = page["next_cursor"]
        if cursor is None:
            break
        if cursor in cursors:
            raise LoadFailure("HISTORY_CURSOR_LOOP")
        cursors.add(cursor)
    if seen != set(expected):
        raise LoadFailure("HISTORY_MISSING")


async def benchmark(base: str, pairs: list[Pair], *, expected_peak: int, duration: float,
                    interval: float, timeout: float = 10, tls: ssl.SSLContext | None = None) -> dict:
    if (expected_peak < 1 or len(pairs) != expected_peak or not 0 < duration <= 3600
            or not 0 < interval <= 3600 or not 0 < timeout <= 120):
        raise ValueError("Se requiere una pareja por usuario del pico previsto y tiempos positivos acotados")
    tls = tls or ssl.create_default_context()
    stats = {"attempted": 0, "sent": 0, "confirmed": 0, "verified_pairs": 0, "peak_sockets": 0}
    latencies: list[float] = []
    users: set[str] = set()
    opened = 0
    start = 0.0
    barrier = asyncio.Barrier(len(pairs) + 1)
    finish = asyncio.Barrier(len(pairs))
    release = asyncio.Event()
    errors: set[str] = set()
    ws_url = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws/v1"

    async def worker(pair: Pair) -> None:
        nonlocal opened
        async with contextlib.AsyncExitStack() as stack:
            actors = []
            for token, keys in ((pair.sender_token, pair.sender), (pair.recipient_token, pair.recipient)):
                api = await stack.enter_async_context(httpx.AsyncClient(base_url=base, timeout=timeout,
                    verify=tls, trust_env=False, headers={"Authorization": "Bearer " + token}))
                me = await request(api, "GET", "/users/me")
                if not me["email_verified"] or me["id"] in users:
                    raise LoadFailure("ACCOUNTS_NOT_DISTINCT_OR_UNVERIFIED")
                users.add(me["id"])
                public = await request(api, "GET", f"/users/{me['id']}/keys")
                if public["public_key"] != encode_binary(keys.public_key):
                    raise LoadFailure("ACCOUNT_KEY_MISMATCH")
                conversation = await request(api, "GET", f"/conversations/{pair.conversation}")
                if conversation["status"] != "active" or conversation["mode"] != pair.mode:
                    raise LoadFailure("CONVERSATION_NOT_READY")
                actors.append(api)
            sender_api, recipient_api = actors
            # Conversación dedicada y sin historial previo, antes de generar tráfico.
            await history(recipient_api, pair, {})
            sockets = []
            for api in actors:
                ticket = await request(api, "POST", "/auth/ws-ticket", 201)
                kwargs = {"ssl": tls} if base.startswith("https://") else {}
                socket = await stack.enter_async_context(connect(ws_url + "?ticket=" + ticket["ticket"],
                    open_timeout=timeout, close_timeout=1, max_size=8192, proxy=None, **kwargs))
                await received(socket, "session.ready", None, timeout)
                sockets.append(socket)
            sender, recipient = sockets
            opened += 2
            stats["peak_sockets"] = max(stats["peak_sockets"], opened)
            try:
                await barrier.wait()
                await release.wait()
                expected: dict[str, str] = {}
                while time.monotonic() < start + duration:
                    tick = time.monotonic()
                    identifier = str(uuid4())
                    clear = "N9 synthetic " + identifier
                    encrypted = encrypt_text(clear, pair.sender, pair.recipient.public_key)
                    stats["attempted"] += 1
                    if pair.mode == "ephemeral":
                        await sender.send(json.dumps(frame("message.offer", pair.conversation, {"message_id": identifier})))
                        await received(recipient, "message.offer", identifier, timeout)
                        await recipient.send(json.dumps(frame("message.ready", pair.conversation, {"message_id": identifier})))
                        await received(sender, "message.ready", identifier, timeout)
                    await sender.send(json.dumps(frame("message.send", pair.conversation, encrypted.payload(UUID(identifier)))))
                    stats["sent"] += 1
                    incoming = await received(recipient, "message.new", identifier, timeout)
                    decrypted = decrypt_text(EncryptedMessage(
                        decode_binary(incoming["ciphertext"], maximum=4096),
                        decode_binary(incoming["crypto_meta"], minimum=56, maximum=56),
                        incoming["protocol_version"]), pair.recipient)
                    if decrypted != clear:
                        raise LoadFailure("PAYLOAD_CORRUPTED")
                    await recipient.send(json.dumps(frame("message.ack", pair.conversation, {"message_id": identifier})))
                    await received(sender, "message.delivered", identifier, timeout)
                    latencies.append(time.monotonic() - tick)
                    state = await request(sender_api, "GET", f"/messages/{identifier}/status")
                    if state["status"] != "delivered":
                        raise LoadFailure("DELIVERY_STATE_MISMATCH")
                    expected[identifier] = clear
                    stats["confirmed"] += 1
                    await asyncio.sleep(max(0, min(start + duration, tick + interval) - time.monotonic()))
                for socket in sockets:
                    pong = await socket.ping()
                    await asyncio.wait_for(pong, timeout)
                await history(recipient_api, pair, expected)
                stats["verified_pairs"] += 1
                await finish.wait()  # Mantener conexiones hasta terminar todas las parejas.
            finally:
                opened -= 2

    try:
        async with asyncio.timeout(duration + timeout * 20):
            async with asyncio.TaskGroup() as group:
                for pair in pairs:
                    group.create_task(worker(pair))
                await barrier.wait()
                start = time.monotonic()
                release.set()
    except Exception as error:
        def classify(value: Exception) -> None:
            if isinstance(value, ExceptionGroup):
                for child in value.exceptions:
                    classify(child)
            else:
                errors.add(str(value) if isinstance(value, LoadFailure) else type(value).__name__)
        classify(error)
    ordered = sorted(latencies)
    def percentile(value: float) -> float | None:
        return round(ordered[max(0, math.ceil(len(ordered) * value) - 1)] * 1000, 3) if ordered else None
    passed = (not errors and stats["peak_sockets"] == expected_peak * 2 and stats["verified_pairs"] == len(pairs)
              and 0 < stats["attempted"] == stats["sent"] == stats["confirmed"])
    return {"passed": passed, "profile": "online_closed_loop", "expected_peak_users": expected_peak,
            "target_sockets": expected_peak * 2, "duration_seconds": duration, "interval_seconds": interval,
            "modes": {mode: sum(pair.mode == mode for pair in pairs) for mode in ("stored", "ephemeral")},
            **stats, "latency_ms": {"p50": percentile(.5), "p95": percentile(.95), "max": percentile(1)},
            "errors": sorted(errors), "production_certified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--accounts-file", type=Path, required=True)
    parser.add_argument("--expected-peak", type=int, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--interval", type=float, required=True)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--allow-local-http", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.resolve() == args.accounts_file.resolve():
            raise ValueError("El informe no puede sobrescribir las cuentas")
        base = validate_target(args.base_url, args.allow_local_http)
        pairs = read_pairs(args.accounts_file)
        tls = ssl.create_default_context(cafile=args.ca_file)
        result = asyncio.run(benchmark(base, pairs, expected_peak=args.expected_peak, duration=args.duration,
                                        interval=args.interval, timeout=args.timeout, tls=tls))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"passed": result["passed"], "confirmed": result["confirmed"], "errors": result["errors"]}))
        return 0 if result["passed"] else 1
    except Exception:
        print('{"passed":false,"error":"LOAD_SETUP_FAILED"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
