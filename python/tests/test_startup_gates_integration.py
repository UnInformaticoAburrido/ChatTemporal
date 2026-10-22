"""El fallo SQL del ensayo Docker también se verifica contra PostgreSQL real."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_conversations_integration import n4 as n4

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1" or os.environ.get("APP_ENV") != "test",
    reason="Requiere PostgreSQL/Redis aislados y RUN_INTEGRATION=1",
)]


def test_failed_migration_rolls_back_and_roles_reject_pending_schema(n4: tuple, tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts/test_startup_containers.py"
    spec = importlib.util.spec_from_file_location("startup_trial", script)
    assert spec and spec.loader
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    fault, head = trial.fault_config(tmp_path)
    config = json.loads(fault.read_text())
    source = config["services"]["migrate"]["volumes"][0]["source"]
    env = {**os.environ, "MIGRATIONS_DIR": source}
    for role in ("migrate", "api", "worker"):
        result = subprocess.run([sys.executable, "-m", "chat.bootstrap", role], env=env,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 1
        events = [json.loads(line) for line in result.stdout.splitlines()]
        assert any(event.get("event_type") == "startup_failed" for event in events)
        assert "n9-sensitive-migration-canary" not in result.stdout + result.stderr
    trial.probe("rollback", head)
