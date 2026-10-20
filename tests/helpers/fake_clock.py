"""An injectable clock so congestion-control tests never sleep (Phase 9b)."""

from __future__ import annotations


class FakeClock:
    """Callable returning the current fake time in seconds."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("time only moves forward")
        self.now += seconds
        return self.now
