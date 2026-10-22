import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("startup_isolation",
    Path(__file__).resolve().parents[2] / "scripts/test_startup_containers.py")
assert spec and spec.loader
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def test_startup_trial_accepts_only_compose_dependency_addresses() -> None:
    assert trial.isolated_url("postgresql://chat:synthetic@postgresql:5432/chat", "postgresql", "postgresql", 5432)
    assert trial.isolated_url("redis://:synthetic@redis:6379/0", "redis", "redis", 6379)


@pytest.mark.parametrize("dsn", [
    "postgresql://chat@external.example/chat", "postgresql://chat@localhost/chat",
    "postgresql://chat@postgresql:5433/chat", "postgresql://chat@postgresql/chat?host=external.example",
    "postgresql://chat@postgresql/chat#fragment", "postgresql://chat@postgresql:invalid/chat",
    "postgresql:///chat?host=/var/run/postgresql", "https://postgresql/chat",
])
def test_startup_trial_rejects_external_or_overridden_database_address(dsn: str) -> None:
    assert not trial.isolated_url(dsn, "postgresql", "postgresql", 5432)
