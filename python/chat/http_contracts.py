"""Parte de N3 necesaria para publicar N2 respetando §23 desde el primer endpoint."""

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chat.errors import APIError


def error_response(request_id: str, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "request_id": request_id, "details": {},
    }})


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError) -> JSONResponse:
        return error_response(request.state.request_id, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        types = {item["type"] for item in exc.errors()}
        status = 400 if "json_invalid" in types else 422
        # DEC-43: §23 no asigna código al error general de validación. Se añade
        # VALIDATION_ERROR; UNKNOWN_FIELD mantiene el código prescrito para extras.
        code = "UNKNOWN_FIELD" if "extra_forbidden" in types else "VALIDATION_ERROR"
        return error_response(request.state.request_id, status, code, "Invalid request.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        # DEC-43: los errores de routing también respetan el envelope, sin path.
        return error_response(request.state.request_id, exc.status_code, "REQUEST_REJECTED", "Request rejected.")

    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(request: Request, exc: psycopg.OperationalError) -> JSONResponse:
        return error_response(request.state.request_id, 503, "TEMPORARY_UNAVAILABLE", "Service unavailable.")

    @app.exception_handler(psycopg.errors.UniqueViolation)
    async def duplicate(request: Request, exc: psycopg.errors.UniqueViolation) -> JSONResponse:
        codes = {"users_nick_key": "NICK_TAKEN", "users_email_key": "EMAIL_TAKEN"}
        code = codes.get(exc.diag.constraint_name or "")
        return error_response(request.state.request_id, 409 if code else 500, code or "INTERNAL_ERROR",
                              "Resource already exists." if code else "Internal error.")


class BodyLimit:
    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                response = error_response(scope.get("state", {}).get("request_id", ""), 413,
                                          "PAYLOAD_TOO_LARGE", "Request body too large.")
                await response(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
