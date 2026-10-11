"""JSON file state store with atomic replace."""

import json
import os
from pathlib import Path

from statusrelay.state.base import STATE_VERSION, LoadedState, StateStore, empty_state


class JsonFileStore(StateStore):
    def __init__(self, path):
        self.path = Path(path)

    def _decode(self, raw: bytes) -> str:
        return raw.decode("utf-8")

    def _encode(self, text: str) -> bytes:
        return text.encode("utf-8")

    def load(self) -> LoadedState:
        if not self.path.exists():
            return LoadedState(empty_state())
        try:
            data = json.loads(self._decode(self.path.read_bytes()))
        except (OSError, ValueError) as exc:
            return LoadedState(empty_state(), f"state file unreadable ({type(exc).__name__}); starting fresh")
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION or not isinstance(data.get("watches"), dict):
            return LoadedState(empty_state(), "state version mismatch; starting fresh")
        return LoadedState(data)

    def save(self, data: dict) -> None:
        # No sort_keys: area order in the cached resolution is meaningful.
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        # Skip unchanged writes: encrypted output differs on every encryption,
        # which would otherwise make CI commit the state file on every run.
        if self._holds(text):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_bytes(self._encode(text))
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _holds(self, text: str) -> bool:
        try:
            return self._decode(self.path.read_bytes()) == text
        except Exception:
            return False
