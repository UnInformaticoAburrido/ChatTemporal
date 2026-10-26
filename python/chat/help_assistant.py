"""Servicio de ayuda aislado: solo catálogo público y pregunta explícita."""

import asyncio
import ipaddress
import json
import os
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

CATALOG = Path(os.environ.get(
    "NEXO_HELP_CATALOG", str(Path(__file__).resolve().parents[2] / "docs/GUIA_USUARIO_LLM.md")
))
MODEL = os.environ.get("NEXO_HELP_MODEL", "").strip()
OLLAMA_URL = os.environ.get("NEXO_HELP_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
GATE = threading.BoundedSemaphore(1)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def local_endpoint(url: str) -> str:
    parts = urlsplit(url)
    allowed = parts.hostname in {"localhost", "host.docker.internal", "ollama"}
    try:
        address = ipaddress.ip_address(parts.hostname or "")
        allowed = address.is_loopback or address.is_private
    except ValueError:
        pass
    if (not allowed or parts.scheme != "http" or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in {"", "/"}):
        raise ValueError("Se requiere un servicio local Ollama")
    return url.rstrip("/") + "/api/chat"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise ValueError("Redirección no permitida")


def generate(question: str) -> str:
    payload = {"model": MODEL, "stream": False, "messages": [
        {"role": "system", "content": CATALOG.read_text(encoding="utf-8")},
        {"role": "user", "content": question},
    ], "options": {"num_predict": 700, "temperature": 0.2}}
    request = urllib.request.Request(local_endpoint(OLLAMA_URL),
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=90) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("Respuesta demasiado grande")
    result = json.loads(raw)
    answer = result["message"]["content"]
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 12000:
        raise ValueError("Respuesta inválida")
    return answer.strip()


def reply(status_code: int, **content: object) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=content, headers={"Cache-Control": "no-store"})


@app.get("/health/live")
def health() -> JSONResponse:
    return reply(200, status="ok")


@app.post("/help-api/question")
async def question(request: Request) -> JSONResponse:
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return reply(415, error="Escribe la consulta como texto.")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            return reply(413, error="Acorta la pregunta a 1000 caracteres.")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or set(body) != {"question"}:
            raise ValueError
        value = body["question"]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000:
            raise ValueError
    except (ValueError, TypeError):
        return reply(400, error="Escribe una pregunta de entre 1 y 1000 caracteres.")
    if not MODEL:
        return reply(503, error="El asistente local aún no está conectado. Consulta las preguntas frecuentes.")
    if not GATE.acquire(blocking=False):
        return reply(429, error="El asistente está atendiendo otra consulta. Inténtalo en unos momentos.")

    def work() -> JSONResponse:
        try:
            return reply(200, answer=generate(value.strip()))
        except Exception:
            # No registrar preguntas, respuestas ni errores del proveedor.
            return reply(503, error="El asistente no ha podido responder. Prueba más tarde o consulta la ayuda.")
        finally:
            GATE.release()

    return await asyncio.to_thread(work)
