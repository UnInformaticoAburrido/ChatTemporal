"""Pruebas puras de formatos internos; transacciones en test_persistence_integration."""

from datetime import datetime
from hashlib import sha256
from uuid import uuid4

import pytest

from chat.persistence import MessageWrite, Position, validate_limit


def test_fingerprint_uses_normative_binary_layout_and_hides_payload() -> None:
    message = MessageWrite(uuid4(), uuid4(), uuid4(), 1, b"m" * 56, b"secret-ciphertext")
    assert message.fingerprint == sha256(b"\0\0\0\1" + b"m" * 56 + b"secret-ciphertext").digest()
    assert "secret-ciphertext" not in repr(message)
    assert "mmmm" not in repr(message)


@pytest.mark.parametrize("version,meta", [(0, b"x" * 56), (2, b"x" * 56), (1, b"x" * 55)])
def test_invalid_crypto_structure(version: int, meta: bytes) -> None:
    with pytest.raises(ValueError):
        MessageWrite(uuid4(), uuid4(), uuid4(), version, meta, b"encrypted")


def test_position_rejects_ambiguous_local_time() -> None:
    with pytest.raises(ValueError):
        Position(datetime(2026, 1, 1), uuid4())


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_page_limit(limit: int) -> None:
    with pytest.raises(ValueError):
        validate_limit(limit)
