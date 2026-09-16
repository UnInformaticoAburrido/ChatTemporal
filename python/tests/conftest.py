import importlib.util
import shutil
from pathlib import Path

import pytest

from chat.config import Settings, load_settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def local_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    spec = importlib.util.spec_from_file_location("init_local", ROOT / "scripts/init_local.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "python/config").mkdir(parents=True)
    shutil.copy(ROOT / "python/config/settings.toml", tmp_path / "python/config/settings.toml")
    module.initialize(tmp_path)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CHAT_SETTINGS_FILE", str(tmp_path / "python/config/settings.local.toml"))
    for key, filename in {
        "DATABASE_URL_FILE": "database_url", "REDIS_URL_FILE": "redis_url",
        "CHAT_JWT_PRIVATE_KEY_FILE": "chat_jwt_private.pem",
        "CHAT_JWT_PUBLIC_KEYS_DIR": "chat-public", "BOOTSTRAP_PUBLIC_KEYS_DIR": "bootstrap-public",
        "INVITATION_HMAC_SECRET_FILE": "invitation_hmac", "VAPID_PRIVATE_KEY_FILE": "vapid_private.pem",
        "SMTP_URL_FILE": "smtp_url",
    }.items():
        monkeypatch.setenv(key, str(tmp_path / "secrets" / filename))
    return load_settings()
