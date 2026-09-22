from types import FrameType

import uvicorn

from chat.app import create_app
from chat.config import load_settings


def main() -> None:
    settings = load_settings()
    application = create_app(settings)

    class Server(uvicorn.Server):
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            # §30.2: readiness false al recibir SIGTERM, antes del drain del servidor.
            application.state.draining = True
            super().handle_exit(sig, frame)

    server = Server(uvicorn.Config(
        application, host="0.0.0.0", port=8000, workers=1, access_log=False,
        log_config=None, log_level="critical", timeout_graceful_shutdown=15,
        ws="websockets", ws_max_size=settings.max_envelope_bytes,
        ws_ping_interval=settings.ws_ping_interval_seconds,
        ws_ping_timeout=settings.ws_dead_after_seconds, proxy_headers=False,
    ))
    # Pub/Sub entre instancias probado en N5; dimensionar procesos tras carga N9.
    server.run()


if __name__ == "__main__":
    main()
