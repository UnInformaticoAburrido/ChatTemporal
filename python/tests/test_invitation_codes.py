import hmac
import random
import struct
from uuid import UUID

import pytest

from chat.errors import APIError
from chat.invitation_codes import InvitationToken, decode_code, encode_code
from chat.protocol import decode_binary, encode_binary

SECRET = bytes(range(32))
IDENTIFIER = UUID("00112233-4455-4677-8899-aabbccddeeff")


@pytest.mark.parametrize("mode,wire_mode", [("ephemeral", 0), ("stored", 1)])
@pytest.mark.parametrize("generation", [1, 0xFFFFFFFF])
def test_exact_layout_and_roundtrip(mode: str, wire_mode: int, generation: int) -> None:
    token = InvitationToken(IDENTIFIER, generation, 1780000000, mode)
    encoded = encode_code(token, SECRET)
    payload = (b"\x01" + IDENTIFIER.bytes + generation.to_bytes(4, "big")
               + (1780000000).to_bytes(8, "big") + bytes([wire_mode]))
    assert len(encoded) == 83 and "=" not in encoded
    assert decode_binary(encoded, maximum=62) == payload + hmac.digest(SECRET, payload, "sha256")
    assert decode_code(encoded, SECRET) == token


def test_tampering_every_byte_wrong_secret_truncation_and_noncanonical_tokens() -> None:
    encoded = encode_code(InvitationToken(IDENTIFIER, 1, 1780000000, "stored"), SECRET)
    raw = decode_binary(encoded, maximum=62)
    invalid = [encoded + "=", encoded[:-1], " " + encoded, "ñ" * 83, "", "x" * 10000]
    invalid += [encode_binary(raw[:i] + bytes([raw[i] ^ 1]) + raw[i+1:]) for i in range(len(raw))]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    invalid.append(encoded[:-1] + alphabet[alphabet.index(encoded[-1]) + 1])
    for code in invalid:
        with pytest.raises(APIError) as error:
            decode_code(code, SECRET)
        assert error.value.code == "INVITATION_INVALID"
    with pytest.raises(APIError):
        decode_code(encoded, b"another secret")


@pytest.mark.parametrize("version,generation,mode", [(2, 1, 0), (1, 0, 0), (1, 1, 2)])
def test_signed_invalid_fields(version: int, generation: int, mode: int) -> None:
    payload = struct.pack("!B16sIQB", version, IDENTIFIER.bytes, generation, 1780000000, mode)
    with pytest.raises(APIError):
        decode_code(encode_binary(payload + hmac.digest(SECRET, payload, "sha256")), SECRET)


def test_deterministic_fuzz_rejects_random_unsigned_inputs() -> None:
    rng = random.Random(4)
    for _ in range(300):
        with pytest.raises(APIError):
            decode_code(encode_binary(rng.randbytes(rng.randrange(100))), SECRET)
