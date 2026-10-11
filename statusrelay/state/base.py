"""State persistence interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

STATE_VERSION = 1
STATE_KEY_ENV = "STATUSRELAY_STATE_KEY"


def empty_state() -> dict:
    return {"version": STATE_VERSION, "watches": {}}


@dataclass
class LoadedState:
    data: dict
    reset_reason: str | None = None


class StateStore(ABC):
    @abstractmethod
    def load(self) -> LoadedState:
        """Return stored state, or a fresh one with `reset_reason` if unusable."""

    @abstractmethod
    def save(self, data: dict) -> None:
        """Persist state atomically."""
