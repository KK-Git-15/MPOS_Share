"""Waiting helpers for GUI tests.

Never use QTest.qWait in these tests: it holds the GIL while sleeping, which
starves the engine thread (a 0.7 s transfer took 500 s). These helpers
sleep with time.sleep, which releases it.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtWidgets import QApplication


def pump(ms: int) -> None:
    """Process Qt events for ``ms`` milliseconds."""
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def wait_until(predicate: Callable[[], bool], timeout_ms: int = 5000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        assert time.monotonic() < deadline, "condition not met in time"
        pump(10)
