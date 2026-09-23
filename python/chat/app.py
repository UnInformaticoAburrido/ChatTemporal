import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from redis.asyncio import Redis

from chat.config import Settings, load_settings, read_secret, validate_secrets
from chat.conversation_api import conversation_router
from chat.dependencies import dependencies_ready
from chat.http_contracts import BodyLimit, install_handlers
from chat.identity import Identity
from chat.identity_api import identity_router
from chat.logging import event
from chat.mailer import Mailer
from chat.protocol import uuid4_value
from chat.push_api import push_router
from chat.recovery_api import recovery_router
from chat.resource_api import resource_router
from chat.vote_api import vote_router
from chat.websocket_api import install_websocket


def create_app(settings: Settings | None = None, *, mailer: Mailer | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        validate_secrets(settings)
        yield
        app.state.draining = True

    application = FastAPI(
        title="Chat · recuperación y Web Push", version="0.7.0",
        docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
    )
    application.state.draining = False
    application.add_middleware(BodyLimit, limit=settings.max_http_body_bytes)
    application.add_middleware(
        CORSMiddleware, allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    registry = CollectorRegistry()
    ready_metric = Gauge("chat_ready", "Disponibilidad de dependencias", registry=registry)
    cleanup_metric = Gauge("chat_cleanup_age_seconds", "Edad de última purga correcta", registry=registry)

    @application.middleware("http")
    async def correlate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        try:
            request_id = str(uuid4_value(request.headers.get("X-Request-ID", "")))
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse(status_code=500, content={"error": {
                "code": "INTERNAL_ERROR", "message": "Internal error.",
                "request_id": request_id, "details": {},
            }})
            # DEC-49: esta respuesta nace fuera del middleware CORS interior;
            # conservar su misma lista explícita también ante un fallo inesperado.
            origin = request.headers.get("Origin")
            if origin in settings.allowed_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
                response.headers["Vary"] = "Origin"
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith(settings.api_prefix + "/"):
            response.headers["Cache-Control"] = "no-store"
        # DEC-08: nombre de ruta, nunca path arbitrario, query, header ni body.
        route = request.scope.get("route")
        event("http", request_id=request_id, endpoint=getattr(route, "name", "unmatched"),
              status=response.status_code)
        return response

    @application.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready")
    async def ready() -> JSONResponse:
        ok = not application.state.draining and await dependencies_ready(settings)
        ready_metric.set(int(ok))
        return JSONResponse({"status": "ok" if ok else "unavailable"}, status_code=200 if ok else 503)

    @application.get("/metrics")
    async def metrics() -> Response:
        ready_metric.set(int(not application.state.draining and await dependencies_ready(settings)))
        try:
            async with Redis.from_url(read_secret("REDIS_URL"), socket_timeout=2) as redis:
                stamp = await redis.get("maintenance:last_success")
                cleanup_metric.set(max(0, time.time() - float(stamp)) if stamp else 1e9)
        except Exception:
            cleanup_metric.set(1e9)
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    install_handlers(application)
    identity = Identity(settings, mailer=mailer)
    application.include_router(identity_router(identity, settings))
    application.include_router(resource_router(identity, settings))
    application.include_router(conversation_router(identity, settings))
    application.include_router(vote_router(identity, settings))
    application.include_router(recovery_router(identity, settings))
    application.include_router(push_router(identity, settings))
    install_websocket(application, settings)
    return application
