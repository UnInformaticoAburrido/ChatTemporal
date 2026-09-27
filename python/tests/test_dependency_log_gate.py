import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_dependency_logs.py"
spec = importlib.util.spec_from_file_location("dependency_log_gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def record(service: str) -> dict:
    return {"timestamp": "2026-09-27T10:00:00Z", "level": "INFO",
            "service": service, "event_type": "dependency_log"}


def test_gate_requires_both_dependencies() -> None:
    assert gate.validate([json.dumps(record(name)) for name in ("redis", "postgresql")]) == {
        "redis": 1, "postgresql": 1}
    for lines in ([], [json.dumps(record("redis"))]):
        with pytest.raises(ValueError):
            gate.validate(lines)


@pytest.mark.parametrize("extra", [
    {"message": "SECRET"}, {"timestamp": "SECRET"}, {"level": "SECRET"},
    {"event_type": "SECRET"}, {"service": "SECRET"},
])
def test_gate_rejects_content_in_any_field(extra: dict) -> None:
    with pytest.raises(ValueError):
        gate.validate([json.dumps(record("redis")), json.dumps({**record("postgresql"), **extra})])


def test_gate_failure_does_not_echo_logs_or_keep_stale_success(tmp_path: Path) -> None:
    source, report = tmp_path / "input.jsonl", tmp_path / "report.json"
    source.write_text("raw SECRET from database\n")
    report.write_text('{"passed": true}')
    result = subprocess.run([sys.executable, str(SCRIPT), str(source), str(report)],
                            capture_output=True, text=True)
    assert result.returncode == 1 and "SECRET" not in result.stdout + result.stderr
    assert not report.exists() and source.read_text() == "raw SECRET from database\n"
