import json
from uuid import uuid4

import pytest

from chat.errors import APIError
from chat.ws_protocol import frame, parse_frame


@pytest.mark.parametrize("kind", ["message.offer", "message.ready", "message.ack"])
def test_control_frames_and_exact_fields(local_settings, kind: str) -> None:
    value = frame(kind, uuid4(), {"message_id": str(uuid4())})
    parsed = parse_frame(json.dumps(value), local_settings)
    assert parsed.type == kind and str(parsed.payload.message_id) == value["payload"]["message_id"]
    for extra in ("ciphertext", "sender_id", "mode"):
        bad = {**value, "payload": {**value["payload"], extra: "untrusted"}}
        with pytest.raises(APIError) as error:
            parse_frame(json.dumps(bad), local_settings)
        assert error.value.code == "UNKNOWN_FIELD"


@pytest.mark.parametrize("text", ['[]', '{"type":1,"type":2}', 'NaN', '{', '"x"', 'null'])
def test_invalid_json_never_exposes_input(local_settings, text: str) -> None:
    with pytest.raises(APIError) as error:
        parse_frame(text, local_settings)
    assert error.value.code == "VALIDATION_ERROR"


def test_control_scope_uuid_and_frame_size(local_settings) -> None:
    value = frame("message.ack", None, {"message_id": str(uuid4())})
    with pytest.raises(APIError):
        parse_frame(json.dumps(value), local_settings)
    with pytest.raises(APIError) as error:
        parse_frame("x" * (local_settings.max_envelope_bytes+1), local_settings)
    assert error.value.code == "PAYLOAD_TOO_LARGE"
