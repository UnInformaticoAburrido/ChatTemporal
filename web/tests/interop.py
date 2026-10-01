"""Interop con datos sintéticos por stdin; nunca toca servicios ni archivos de claves."""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from chat_client.crypto import EncryptedMessage, KeyPair, decrypt_text, encrypt_text  # noqa: E402


def decode_binary(value: str, **_limits: int) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_binary(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


value = json.load(sys.stdin)
recipient = KeyPair(decode_binary(value["private"], minimum=32, maximum=32))
message = value["message"]
assert decrypt_text(EncryptedMessage(decode_binary(message["ciphertext"], maximum=4096),
    decode_binary(message["crypto_meta"], minimum=56, maximum=56), 1), recipient) == value["clear"]
reply = encrypt_text(value["clear"], recipient, decode_binary(value["sender_public"], minimum=32, maximum=32))
print(json.dumps({"public": encode_binary(recipient.public_key), "protocol_version": 1,
                  "ciphertext": encode_binary(reply.ciphertext), "crypto_meta": encode_binary(reply.crypto_meta)}))
