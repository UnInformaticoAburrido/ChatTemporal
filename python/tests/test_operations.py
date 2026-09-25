import io
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient

from chat.app import create_app
from chat.backup import decrypt, encrypt, pg_environment, retain
from chat.config import Settings


def test_backup_encryption_stream_authenticates_all_before_use() -> None:
    key = os.urandom(32)
    content = os.urandom(2 * 1024 * 1024 + 17)
    output = io.BytesIO()
    encrypt(iter([content[:100], content[100:]]), output, key)
    sealed = output.getvalue()
    assert content[:100] not in sealed
    restored = io.BytesIO()
    decrypt(io.BytesIO(sealed), restored, key)
    assert restored.getvalue() == content
    for position in [12, 50, len(sealed) - 1]:
        corrupt = bytearray(sealed)
        corrupt[position] ^= 1
        with pytest.raises(InvalidTag):
            decrypt(io.BytesIO(corrupt), io.BytesIO(), key)
    with pytest.raises(InvalidTag):
        decrypt(io.BytesIO(sealed), io.BytesIO(), os.urandom(32))


def test_backup_retention_keeps_seven_days_and_four_weekly(tmp_path: Path) -> None:
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    for hours in range(0, 24 * 60, 6):
        date = now - timedelta(hours=hours)
        (tmp_path / (date.strftime("%Y%m%dT%H%M%S%fZ") + ".chatbak")).touch()
    (tmp_path / "operator-owned.txt").touch()
    retain(tmp_path, now)
    stamps = [datetime.strptime(p.stem, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
              for p in tmp_path.glob("*.chatbak")]
    assert len([s for s in stamps if s >= now - timedelta(days=7)]) == 29
    assert len({s.isocalendar()[:2] for s in stamps}) == 4
    assert len(stamps) <= 33
    assert (tmp_path / "operator-owned.txt").exists()


def test_pg_credentials_never_become_cli_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGSERVICE", "untrusted")
    env = pg_environment("postgresql://user:secret@localhost/chat?sslmode=require")
    assert env["PGPASSWORD"] == "secret" and env["PGSSLMODE"] == "require"
    assert "PGSERVICE" not in env


def test_metrics_do_not_label_user_supplied_paths(local_settings: Settings) -> None:
    from prometheus_client import generate_latest
    with TestClient(create_app(local_settings)) as api:
        api.get("/SECRET-IDENTIFIER?ticket=SECRET-TICKET")
    text = generate_latest().decode()
    assert 'endpoint="unmatched"' in text
    assert "SECRET-IDENTIFIER" not in text and "SECRET-TICKET" not in text


def test_dependency_log_wrapper_discards_entire_untrusted_line() -> None:
    script = Path(__file__).resolve().parents[2] / "operations/safe-service-log.sh"
    result = subprocess.run(["sh", str(script), "postgresql", "sh", "-c",
                             "echo 'password SECRET ciphertext'; exit 7"], capture_output=True, text=True)
    assert result.returncode == 7
    assert '"event_type":"dependency_log"' in result.stdout
    assert "SECRET" not in result.stdout + result.stderr


def test_dependency_log_wrapper_preserves_child_status_after_sigterm() -> None:
    script = Path(__file__).resolve().parents[2] / "operations/safe-service-log.sh"
    child = "import signal,sys; signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); print('ready', flush=True); signal.pause()"
    with subprocess.Popen(["sh", str(script), "redis", sys.executable, "-c", child],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        try:
            assert process.stdout
            assert '"event_type":"dependency_log"' in process.stdout.readline()
            process.send_signal(signal.SIGTERM)
            _, errors = process.communicate(timeout=5)
            assert process.returncode == 0
            assert not errors
        finally:
            if process.poll() is None:
                process.kill()


def test_failed_dump_never_publishes_or_prunes_good_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chat import backup as module
    previous = tmp_path / '20200101T000000000000Z.chatbak'
    previous.write_bytes(b'good-encrypted-copy')
    def broken(*args):
        yield b'some durable data'
        raise RuntimeError('pg_dump failed')
    monkeypatch.setattr(module, 'dump_chunks', broken)
    with pytest.raises(RuntimeError):
        module.backup('unused', tmp_path, os.urandom(32), Path('/unused'))
    assert list(tmp_path.iterdir()) == [previous]
    assert previous.read_bytes() == b'good-encrypted-copy'


def test_production_logging_override_removes_size_only_options() -> None:
    import json
    root = Path(__file__).resolve().parents[2]
    result = subprocess.check_output(['docker', 'compose', '-f', str(root / 'docker-compose.yml'),
        '-f', str(root / 'docker-compose.production.yml'), '--profile', 'observability',
        'config', '--format', 'json'], cwd=root)
    services = json.loads(result)['services']
    for name, service in services.items():
        assert service['logging'] == {'driver': 'journald', 'options': {'tag': 'chat-' + name}}
        if name != 'caddy':
            assert not service.get('ports')
