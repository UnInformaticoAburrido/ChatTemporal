"""El runner E2E no puede heredar recursos ni endpoints de una instalación de Chat."""

import importlib.util
from pathlib import Path

import pytest
import yaml

spec = importlib.util.spec_from_file_location("web_e2e_runner",
    Path(__file__).resolve().parents[2] / "scripts/test_web_containers.py")
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_web_e2e_uses_only_fresh_volatile_dependencies_and_pinned_images(tmp_path: Path) -> None:
    config = runner.configuration(tmp_path)
    base = yaml.safe_load((runner.ROOT / "docker-compose.yml").read_text())
    assert set(config) == {"services"}
    assert set(config["services"]) == {"postgresql", "redis"}
    for name, service in config["services"].items():
        assert service["image"] == base["services"][name]["image"]
        assert service["ports"] == [f"127.0.0.1::{5432 if name == 'postgresql' else 6379}"]
        assert service["tmpfs"]
        for mount in service["volumes"]:
            source = Path(mount.rsplit(":", 2)[0])
            assert source.is_relative_to(tmp_path) or source.is_relative_to(runner.ROOT / "BD")
            assert mount.endswith(":ro")
        assert "depends_on" not in service and "container_name" not in service


@pytest.mark.parametrize("published", ["0.0.0.0:40000", "[::]:40000", "127.0.0.1:0",
                                     "127.0.0.1:65536", "external.example:40000"])
def test_web_e2e_rejects_nonloopback_or_invalid_dependency_ports(monkeypatch, published: str) -> None:
    monkeypatch.setattr(runner, "execute", lambda _: published)
    with pytest.raises((RuntimeError, ValueError)):
        runner.port(["docker", "compose"], "postgresql", 5432)
