"""Convert upstream long IDs (hex AES-128-CBC ciphertext) to short IDs."""

import re

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Constants shipped in the upstream public web client. They encode IDs;
# they are not an access-control secret. We only ever decrypt.
_KEY = b"ILOVEFETIXFETIX!"
_IV = b"!@#$FETIXEVENTiv"

LONG_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_EVENT_SHORT_RE = re.compile(r"^e\d{9}$")
_SESSION_SHORT_RE = re.compile(r"^s\d{9}$")


class IdDecodeError(ValueError):
    """A long ID could not be decoded into the expected short ID."""


def decode_long_id(long_id: str) -> str:
    if not LONG_ID_RE.fullmatch(long_id):
        raise IdDecodeError(f"not a 32-hex long id: {long_id!r}")
    decryptor = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).decryptor()
    padded = decryptor.update(bytes.fromhex(long_id)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        plain = unpadder.update(padded) + unpadder.finalize()
        return plain.decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise IdDecodeError(f"cannot decode long id {long_id!r}") from exc


def _typed(long_id: str, pattern: re.Pattern[str], kind: str) -> str:
    short = decode_long_id(long_id)
    if not pattern.match(short):
        raise IdDecodeError(f"long id {long_id!r} decoded to {short!r}, not a {kind} id")
    return short


def event_short_id(long_id: str) -> str:
    return _typed(long_id, _EVENT_SHORT_RE, "event")


def session_short_id(long_id: str) -> str:
    return _typed(long_id, _SESSION_SHORT_RE, "session")
