import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from chat.config import Settings, validate_secrets
from chat.errors import APIError
from chat.identity_crypto import Tokens
from chat_client.bootstrap import BootstrapIssuer


def test_main_app_issuer_compatible_unique_and_independent(local_settings: Settings) -> None:
    key = Path(os.environ["BOOTSTRAP_PUBLIC_KEYS_DIR"]).parent / "bootstrap_local_private.pem"
    issuer = BootstrapIssuer(key, "main-local-1", 60)
    user = uuid4()
    one, two = issuer.issue(user), issuer.issue(user)
    a, b = Tokens(local_settings).verify(one, bootstrap=True), Tokens(local_settings).verify(two, bootstrap=True)
    assert a.user_id == user and a.session_id is None and a.jti != b.jti
    with pytest.raises(APIError, match="TOKEN_INVALID"):
        Tokens(local_settings).verify(one)
    assert str(key) not in repr(issuer) and one not in repr(issuer)
    with pytest.raises(ValueError, match="UUID"):
        issuer.issue(str(user))  # type: ignore[arg-type]


@pytest.mark.parametrize("ttl", [0, 301, True, 1.5])
def test_main_app_issuer_rejects_nonprotocol_ttl(tmp_path: Path, ttl: int) -> None:
    with pytest.raises(ValueError, match="configuration"):
        BootstrapIssuer(tmp_path / "key.pem", "main-1", ttl)


def test_main_app_issuer_no_secret_in_failures(tmp_path: Path) -> None:
    key = tmp_path / "key.pem"
    issuer = BootstrapIssuer(key, "main-1")
    for material in (None, b"private secret", b"x" * 4097):
        if material is not None:
            key.write_bytes(material)
        with pytest.raises(ValueError, match="^Bootstrap signing unavailable$"):
            issuer.issue(UUID("12345678-1234-4234-8234-123456789abc"))
    for kid in ("../main", "", "main/key", "main\\key"):
        with pytest.raises(ValueError, match="configuration"):
            BootstrapIssuer(key, kid)


@pytest.mark.parametrize("origins", [["*"], ["https://main.example.test/path"], ["https://main.example.test/"],
                                   ["https://u:p@main.example.test"], ["https://main.example.test?x=1"],
                                   ["https://main.example.test#x"], ["http://main.example.test"],
                                   ["https://main.example.test"] * 2, ["https://main.example.test:443"]])
def test_bootstrap_origins_reject_ambiguous_trust(local_settings: Settings, origins: list[str]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({**local_settings.model_dump(), "bootstrap_web_origins": origins})


def test_bootstrap_origins_default_disabled_loopback_only_development(local_settings: Settings,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    assert local_settings.bootstrap_web_origins == []
    changed = Settings.model_validate({**local_settings.model_dump(),
                                      "bootstrap_web_origins": ["https://main.example.test", "http://127.0.0.1:4174"]})
    validate_secrets(changed)
    production = changed.model_copy(update={"allowed_origins": ["https://chat.example.test"],
                                            "vapid_subject": "mailto:ops@example.test", "email_from": "chat@example.test"})
    smtp = Path(os.environ["SMTP_URL_FILE"])
    smtp.write_text("smtps://mail.example.test")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="Bootstrap web requiere HTTPS"):
        validate_secrets(production)
