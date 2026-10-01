"""El umbral no redondea al alza ni acepta informes incompletos."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = Path(__file__).resolve().parents[2] / "scripts/check_coverage.py"
    spec = importlib.util.spec_from_file_location("check_coverage", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    paths = {f"chat/{name}.py" for modules in module.GROUPS.values() for name in modules}
    paths.add("chat_client/crypto.py")
    for name in paths:
        source = Path(name)
        source.parent.mkdir(exist_ok=True)
        source.touch()
    report = {"files": {name: {"summary": {"covered_lines": 90, "num_statements": 100}} for name in paths}}
    return module, report


def test_coverage_accepts_exact_threshold(gate) -> None:
    module, report = gate
    assert module.check(report)


@pytest.mark.parametrize("domain", ["auth", "invitations", "delivery", "voting"])
def test_coverage_rejects_critical_domain_below_threshold(gate, domain: str) -> None:
    module, report = gate
    name = "chat/" + module.GROUPS[domain][0] + ".py"
    report["files"][name]["summary"]["covered_lines"] = 89
    assert not module.check(report)


def test_coverage_rejects_low_global_even_when_domains_pass(gate) -> None:
    module, report = gate
    report["files"]["chat_client/crypto.py"]["summary"] = {"covered_lines": 0, "num_statements": 10000}
    assert not module.check(report)


def test_coverage_rejects_omitted_source(gate) -> None:
    module, report = gate
    del report["files"]["chat_client/crypto.py"]
    with pytest.raises(ValueError, match="todos"):
        module.check(report)
