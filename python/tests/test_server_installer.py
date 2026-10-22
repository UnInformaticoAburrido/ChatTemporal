"""Ensayar generación Bash sin root/red, con credenciales sintéticas privadas."""

import importlib.util
import json
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml
from psycopg.conninfo import conninfo_to_dict

from chat.config import Settings, validate_secrets

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/servidor/instalar.sh"
spec = importlib.util.spec_from_file_location("server_backup_job", ROOT / "operations/backup_job.py")
assert spec and spec.loader
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


def command(target: Path, smtp: Path, *extra: str) -> list[str]:
    return ["bash", str(SCRIPT), "--prepare-only", "--source", str(ROOT), "--target", str(target),
            "--domain", "chat.example.org", "--acme-email", "ops@example.org", "--email-from", "chat@example.org",
            "--smtp-url-file", str(smtp), *extra]


@pytest.fixture
def prepared(tmp_path: Path) -> Path:
    smtp = tmp_path / "smtp_url"
    smtp.write_text("smtps://test%40example.org:synthetic%22%5Csecret@mail.example.org:465")
    smtp.chmod(0o600)
    destination = tmp_path / "deployment"
    result = subprocess.run(command(destination, smtp), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, "La preparación Bash debe terminar sin root/red/Docker"
    assert "synthetic" not in result.stdout + result.stderr
    return destination


def test_bash_installer_generates_production_secrets_and_keeps_private_keys_off_mounts(prepared: Path, monkeypatch) -> None:
    import tomllib

    settings = Settings.model_validate(tomllib.loads((prepared / "server-config/settings.toml").read_text()))
    for name, file in {
        "DATABASE_URL_FILE": "database_url", "REDIS_URL_FILE": "redis_url",
        "CHAT_JWT_PRIVATE_KEY_FILE": "chat_jwt_private.pem", "CHAT_JWT_PUBLIC_KEYS_DIR": "chat-public",
        "BOOTSTRAP_PUBLIC_KEYS_DIR": "bootstrap-public", "VAPID_PRIVATE_KEY_FILE": "vapid_private.pem",
        "INVITATION_HMAC_SECRET_FILE": "invitation_hmac", "SMTP_URL_FILE": "smtp_url",
    }.items():
        monkeypatch.setenv(name, str(prepared / "secrets" / file))
    monkeypatch.setenv("APP_ENV", "production")
    validate_secrets(settings)
    assert settings.allowed_origins == ["https://chat.example.org"]
    assert settings.bootstrap_web_origins == []
    assert len((prepared / "server-operator/backup.key").read_bytes()) == 32
    assert stat.S_IMODE((prepared / "secrets/smtp_url").stat().st_mode) == 0o600
    assert stat.S_IMODE((prepared / "server-operator").stat().st_mode) == 0o700
    assert not (prepared / "secrets/bootstrap_local_private.pem").exists()
    assert not (prepared / "python/config/settings.local.toml").exists()
    assert not (prepared / "python/tests").exists()
    assert not (prepared / ".git").exists()
    config = yaml.safe_load((prepared / "docker-compose.server.yml").read_text())
    assert "bootstrap_issuer_private" not in str(config)
    backup = config["services"]["backup"]
    assert backup["profiles"] == ["operations"] and backup["read_only"] is True
    assert backup["networks"] == ["backend"] and "ports" not in backup
    assert "docker.sock" not in str(backup)
    assert backup["build"]["target"] == "backup" and backup["tmpfs"]
    alert = yaml.safe_load((prepared / "server-config/alertmanager.yml").read_text())
    assert alert["global"]["smtp_force_implicit_tls"] is True
    assert "smtp_auth_password" not in alert["global"]
    assert alert["global"]["smtp_auth_username"] == "test@example.org"
    assert "synthetic" not in (prepared / "server-config/alertmanager.yml").read_text()
    assert (prepared / "secrets/alertmanager_smtp_password").read_text() == 'synthetic"\\secret'
    units = prepared / "server-config/systemd"
    stack = (units / "chat-stack.service").read_text()
    assert "Requires=docker.service" in stack
    assert f"ExecStop={shutil.which('docker') or '/usr/bin/docker'} compose stop" in stack and "down" not in stack
    for action in ("backup", "restore-drill"):
        service = (units / f"chat-{action}.service").read_text()
        assert "Requires=chat-stack.service" in service and f"WorkingDirectory={prepared}" in service
        assert "compose run --rm --no-deps backup" in service and ".venv" not in service
        assert (units / f"chat-{action}.timer").is_file()


def test_installer_refuses_existing_directory_without_touching_keys(prepared: Path, tmp_path: Path) -> None:
    before = (prepared / "server-operator/backup.key").read_bytes()
    smtp = tmp_path / "smtp-url"
    smtp.write_text("smtp://mail.example.org:587")
    result = subprocess.run(command(prepared, smtp), capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert (prepared / "server-operator/backup.key").read_bytes() == before


@pytest.mark.parametrize("smtp_url", ["http://mail.example.org", "smtp://pending.invalid:587",
                                     "smtp://mail.example.org:0", "smtps://mail.example.org:65536",
                                     "smtp://u:%00@mail.example.org:587"])
def test_installer_rejects_invalid_provider_before_generating_files(tmp_path: Path, smtp_url: str) -> None:
    smtp = tmp_path / "smtp"
    smtp.write_text(smtp_url)
    target = tmp_path / "invalid"
    result = subprocess.run(command(target, smtp), capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and not target.exists()
    assert not list(tmp_path.glob("invalid.tmp.*"))


def test_installer_does_not_evaluate_smtp_credentials(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    password = f"test$(touch {marker})`touch {marker}`"
    smtp = tmp_path / "smtp"
    smtp.write_text(f"smtp://user:{quote(password, safe='')}@mail.example.org:587")
    target = tmp_path / "literal"
    result = subprocess.run(command(target, smtp), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert not marker.exists()
    assert (target / "secrets/alertmanager_smtp_password").read_text() == password
    assert password not in result.stdout + result.stderr


def test_installer_cleans_partial_preparation_and_rejects_bootstrap_private_key(tmp_path: Path) -> None:
    smtp, private = tmp_path / "smtp", tmp_path / "private.pem"
    smtp.write_text("smtp://mail.example.org:587")
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
                   check=True, capture_output=True)
    target = tmp_path / "partial"
    result = subprocess.run(command(target, smtp, "--bootstrap-public-key", str(private)),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and not target.exists()
    assert not list(tmp_path.glob("partial.tmp.*"))
    assert "PRIVATE KEY" not in result.stdout + result.stderr


def test_installer_accepts_real_main_app_public_key_without_copying_its_private_key(tmp_path: Path) -> None:
    smtp, private, public = tmp_path / "smtp", tmp_path / "main-private.pem", tmp_path / "main-public.pem"
    smtp.write_text("smtp://mail.example.org:587")
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
                   check=True, capture_output=True)
    target = tmp_path / "external"
    result = subprocess.run(command(target, smtp, "--bootstrap-public-key", str(public),
                                   "--bootstrap-kid", "main-real-1", "--main-origin", "https://app.example.org"),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert (target / "secrets/bootstrap-public/main-real-1.pem").read_bytes() == public.read_bytes()
    assert not (target / "server-operator/bootstrap_issuer_private.pem").exists()
    assert 'bootstrap_web_origins = ["https://app.example.org"]' in (target / "server-config/settings.toml").read_text()


def test_installer_compose_replaces_configuration_and_exposes_only_caddy(prepared: Path) -> None:
    if not shutil.which("docker"):
        pytest.skip("Docker CLI necesario solo para parser Compose, no daemon")
    result = subprocess.run(["docker", "compose", "config", "--format", "json"], cwd=prepared,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, "Compose debe validar la configuración generada"
    config = json.loads(result.stdout)
    assert config["name"] == "chat-server"
    for name, service in config["services"].items():
        if name != "caddy":
            assert not service.get("ports")
    for name in ("python", "worker", "migrate"):
        settings = [v for v in config["services"][name]["volumes"] if v["target"] == "/etc/chat/settings.toml"]
        assert len(settings) == 1 and settings[0]["source"].endswith("server-config/settings.toml")
    assert config["services"]["python"]["environment"]["APP_ENV"] == "production"


def test_container_backup_uses_internal_dsn_without_docker_socket(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "database_url"
    file.write_text("postgresql://chat:synthetic@postgresql:5432/chat")
    monkeypatch.setenv("DATABASE_URL_FILE", str(file))
    monkeypatch.setenv("CHAT_BACKUP_DIRECT_DSN", "1")
    monkeypatch.setattr(job.subprocess, "check_output", lambda *a, **kw: pytest.fail("No debe ejecutar Docker"))
    config = conninfo_to_dict(job.database_url())
    assert config["host"] == "postgresql" and config["port"] == "5432" and config["dbname"] == "chat"


def test_host_backup_still_discovers_compose_address(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "database_url"
    file.write_text("postgresql://chat:synthetic@postgresql:5432/chat")
    monkeypatch.setenv("DATABASE_URL_FILE", str(file))
    monkeypatch.delenv("CHAT_BACKUP_DIRECT_DSN", raising=False)
    calls = []

    def output(args, **kw):
        calls.append(args)
        return "container" if args[1] == "compose" else json.dumps({"chat_backend": {"IPAddress": "172.20.0.8"}})

    monkeypatch.setattr(job.subprocess, "check_output", output)
    assert conninfo_to_dict(job.database_url())["host"] == "172.20.0.8"
    assert len(calls) == 2
