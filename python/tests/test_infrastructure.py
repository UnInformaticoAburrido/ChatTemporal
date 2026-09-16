import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chat import app as app_module
from chat import dependencies
from chat.config import Settings, validate_secrets
from chat.logging import event


def test_generated_secret_formats_and_independent_pairs(local_settings: Settings) -> None:
    validate_secrets(local_settings)
    assert Path(os.environ["CHAT_JWT_PRIVATE_KEY_FILE"]).stat().st_mode & 0o777 == 0o600


def test_local_examples_cannot_start_production(local_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError):
        validate_secrets(local_settings)


def test_bootstrap_cannot_reuse_chat_key(local_settings: Settings) -> None:
    public = Path(os.environ["CHAT_JWT_PUBLIC_KEYS_DIR"]) / "chat-local-1.pem"
    bootstrap = Path(os.environ["BOOTSTRAP_PUBLIC_KEYS_DIR"]) / "main-local-1.pem"
    bootstrap.write_bytes(public.read_bytes())
    with pytest.raises(ValueError, match="independiente"):
        validate_secrets(local_settings)


def test_wrong_vapid_pair_rejected(local_settings: Settings) -> None:
    changed = local_settings.model_copy(update={"vapid_public_key": "incorrecta"})
    with pytest.raises(ValueError, match="VAPID"):
        validate_secrets(changed)


@pytest.mark.parametrize("field,value", [
    ("allowed_origins", ["*"]), ("allowed_origins", ["https://example.com/path"]),
    ("cleanup_interval_seconds", 61), ("access_token_ttl_seconds", 60),
    ("api_prefix", "/api/v2"), ("max_envelope_bytes", 0),
])
def test_normative_settings_rejected(local_settings: Settings, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**local_settings.model_dump(), field: value})


def test_missing_secret_rejected_before_start(local_settings: Settings) -> None:
    Path(os.environ["INVITATION_HMAC_SECRET_FILE"]).unlink()
    with pytest.raises(FileNotFoundError):
        validate_secrets(local_settings)


def test_live_never_queries_dependencies(local_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr(app_module, "dependencies_ready", probe)
    with TestClient(app_module.create_app(local_settings)) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]
    probe.assert_not_called()


@pytest.mark.parametrize("available,expected", [(True, 200), (False, 503)])
def test_readiness_tracks_dependencies(local_settings: Settings, monkeypatch: pytest.MonkeyPatch,
                                      available: bool, expected: int) -> None:
    monkeypatch.setattr(app_module, "dependencies_ready", AsyncMock(return_value=available))
    with TestClient(app_module.create_app(local_settings)) as client:
        assert client.get("/health/ready").status_code == expected


def test_draining_stops_readiness(local_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "dependencies_ready", AsyncMock(return_value=True))
    application = app_module.create_app(local_settings)
    with TestClient(application) as client:
        application.state.draining = True
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200


def test_api_docs_disabled(local_settings: Settings) -> None:
    with TestClient(app_module.create_app(local_settings)) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404


def test_logs_drop_secret_fields(capsys: pytest.CaptureFixture[str]) -> None:
    event("test", ciphertext="TOP-SECRET", access_token="TOP-SECRET", endpoint="health")
    output = capsys.readouterr().out
    assert "TOP-SECRET" not in output
    assert json.loads(output)["endpoint"] == "health"


def test_http_logs_do_not_contain_query_or_arbitrary_path(local_settings: Settings,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    with TestClient(app_module.create_app(local_settings)) as client:
        client.get("/TOP-SECRET?ticket=SECRET-TICKET")
    output = capsys.readouterr().out
    assert "TOP-SECRET" not in output
    assert "SECRET-TICKET" not in output


def test_real_probe_fails_closed_on_error(local_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dependencies, "probe_database", AsyncMock(side_effect=OSError("SECRET-URL")))
    monkeypatch.setattr(dependencies, "probe_redis", AsyncMock(return_value=None))
    assert asyncio.run(dependencies.dependencies_ready(local_settings)) is False


def test_startup_wait_retries_until_ready(local_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = AsyncMock(side_effect=[False, False, True])
    monkeypatch.setattr(dependencies, "dependencies_ready", probe)
    monkeypatch.setattr(dependencies.asyncio, "sleep", AsyncMock())
    asyncio.run(dependencies.wait_for_dependencies(local_settings))
    assert probe.await_count == 3


def test_startup_wait_has_deadline(local_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    # Evita sustituir time.monotonic global del event loop: configuración de plazo cero solo en test.
    expired = local_settings.model_copy(update={"startup_timeout_seconds": 0})
    with pytest.raises(RuntimeError, match="plazo"):
        asyncio.run(dependencies.wait_for_dependencies(expired))
