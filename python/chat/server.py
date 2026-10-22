import asyncio
import contextlib
import socket
import time
from types import FrameType

import uvicorn
from fastapi import FastAPI

from chat.app import create_app
from chat.config import load_settings


class DrainingServer(uvicorn.Server):
    """Uvicorn cierra WS antes de esperar tareas; dar el margen antes de shutdown."""

    def __init__(self, config: uvicorn.Config, application: FastAPI, drain_seconds: float = 15) -> None:
        super().__init__(config)
        self.application = application
        self.drain_seconds = drain_seconds

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if not self.application.state.draining:
            self.application.state.draining = True
            self.application.state.drain_deadline = time.monotonic() + self.drain_seconds
        super().handle_exit(sig, frame)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        if not self.application.state.draining:
            self.application.state.draining = True
            self.application.state.drain_deadline = time.monotonic() + self.drain_seconds
        # Mantener conexiones aceptadas: ACK/ready/send de offers ya existentes.
        while ((self.application.state.sockets or self.server_state.tasks) and not self.force_exit
               and time.monotonic() < self.application.state.drain_deadline):
            await asyncio.sleep(0.05)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(2):
                await asyncio.gather(*(ws.close(code=1001) for ws in list(self.application.state.sockets)),
                                     return_exceptions=True)
        await super().shutdown(sockets)


def main() -> None:
    settings = load_settings()
    application = create_app(settings)
    server = DrainingServer(uvicorn.Config(
        application, host="0.0.0.0", port=8000, workers=1, access_log=False,
        log_config=None, log_level="critical", timeout_graceful_shutdown=2,
        ws="websockets", ws_max_size=settings.max_envelope_bytes,
        ws_ping_interval=settings.ws_ping_interval_seconds,
        ws_ping_timeout=settings.ws_dead_after_seconds, proxy_headers=False,
    ), application)
    server.run()


if __name__ == "__main__":
    main()
