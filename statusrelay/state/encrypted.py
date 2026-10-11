"""Fernet-encrypted state, safe to store in a public branch."""

from cryptography.fernet import Fernet, InvalidToken

from statusrelay.state.json_file import JsonFileStore


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


class EncryptedFileStore(JsonFileStore):
    def __init__(self, path, key: str):
        super().__init__(path)
        if not isinstance(key, str):
            raise ValueError("state key must be a str")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("state key is not a valid Fernet key") from exc

    def _decode(self, raw: bytes) -> str:
        try:
            return self._fernet.decrypt(raw).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("cannot decrypt state") from exc

    def _encode(self, text: str) -> bytes:
        return self._fernet.encrypt(text.encode("utf-8"))
