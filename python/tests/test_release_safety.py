"""N9: arranque cerrado ante fallos y entradas de protocolo no confiables."""

import json
import random
import sys
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from chat import bootstrap
from chat.config import Settings
from chat.errors import APIError
from chat.identity_crypto import Tokens
from chat.protocol import encode_binary
from chat.ws_protocol import frame, parse_frame


@pytest.mark.parametrize("gate", ["load_settings", "validate_secrets", "wait_for_dependencies", "check_schema"])
def test_startup_failure_never_executes_api_or_logs_exception(gate: str, local_settings: Settings,
                                                            monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["bootstrap", "api"])
    monkeypatch.setattr(bootstrap, "load_settings", Mock(return_value=local_settings))
    monkeypatch.setattr(bootstrap, "validate_secrets", Mock())
    monkeypatch.setattr(bootstrap, "wait_for_dependencies", AsyncMock())
    monkeypatch.setattr(bootstrap, "check_schema", Mock())
    getattr(bootstrap, gate).side_effect = RuntimeError("postgresql://SECRET-DATABASE-PASSWORD")
    execute = Mock()
    monkeypatch.setattr(bootstrap.os, "execv", execute)
    assert bootstrap.main() == 1
    execute.assert_not_called()
    logs = capsys.readouterr().out
    assert json.loads(logs)["error_code"] == "CONFIG_OR_DEPENDENCY"
    assert "SECRET-DATABASE-PASSWORD" not in logs


def test_failed_migration_never_reports_success(local_settings: Settings, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["bootstrap", "migrate"])
    monkeypatch.setattr(bootstrap, "load_settings", Mock(return_value=local_settings))
    monkeypatch.setattr(bootstrap, "validate_secrets", Mock())
    monkeypatch.setattr(bootstrap, "wait_for_dependencies", AsyncMock())
    monkeypatch.setattr(bootstrap.command, "upgrade", Mock(side_effect=RuntimeError("SECRET-SQL")))
    assert bootstrap.main() == 1
    logs = capsys.readouterr().out
    assert "startup_failed" in logs and "migration_complete" not in logs and "SECRET-SQL" not in logs


def test_jwt_deterministic_fuzz_and_every_signature_byte(local_settings: Settings) -> None:
    tokens = Tokens(local_settings)
    rng = random.Random(2903)
    for size in range(256):
        invalid = ".".join(encode_binary(rng.randbytes(size)) for _ in range(3))
        with pytest.raises(APIError, match="TOKEN_INVALID"):
            tokens.verify(invalid)
    valid = tokens.access(uuid4(), uuid4())
    header, payload, signature = valid.split(".")
    from chat.protocol import decode_binary
    raw = decode_binary(signature, minimum=64, maximum=64)
    for offset in range(len(raw)):
        tampered = bytearray(raw)
        tampered[offset] ^= 1
        with pytest.raises(APIError, match="TOKEN_INVALID"):
            tokens.verify(header + "." + payload + "." + encode_binary(bytes(tampered)))


def test_ws_envelope_deterministic_fuzz(local_settings: Settings) -> None:
    rng = random.Random(2904)
    for size in range(256):
        untrusted = encode_binary(rng.randbytes(size))
        value = frame("message.ack", uuid4(), {"message_id": str(uuid4())})
        value["payload"]["unexpected"] = untrusted
        with pytest.raises(APIError) as error:
            parse_frame(json.dumps(value), local_settings)
        assert error.value.code == "UNKNOWN_FIELD"
        with pytest.raises(APIError):
            parse_frame(json.dumps([untrusted, {"type": untrusted}]), local_settings)
