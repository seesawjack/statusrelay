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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        # No sort_keys: area order in the cached resolution is meaningful.
        tmp.write_bytes(self._encode(json.dumps(data, ensure_ascii=False, indent=2) + "\n"))
        os.replace(tmp, self.path)
