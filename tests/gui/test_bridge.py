from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from mpos.events import Event, NodeStatus
from mpos.gui.bridge import EngineThread, QtBridge
from tests.gui.qt_helpers import wait_until


@pytest.fixture
def bridge(qapp: QApplication) -> Iterator[QtBridge]:
    thread = EngineThread()
    thread.start()
    bridge = QtBridge(thread)
    yield bridge
    bridge.shutdown()
    assert not thread.is_running


def test_events_arrive_in_gui_thread_in_order(bridge: QtBridge) -> None:
    received: list[tuple[int, Event]] = []
    bridge.events.connect(
        lambda batch: received.extend((threading.get_ident(), e) for e in batch)
    )

    async def publish() -> None:
        for i in range(100):
            bridge.engine.bus.publish(NodeStatus(node_id=f"n{i}", running=True))

    bridge.call(publish())
    wait_until(lambda: len(received) >= 100)

    gui_thread = threading.get_ident()
    assert {tid for tid, _ in received} == {gui_thread}
    assert [e.node_id for _, e in received[:100]] == [f"n{i}" for i in range(100)]  # type: ignore[attr-defined]


def test_command_result_and_errors_return_to_gui_thread(bridge: QtBridge) -> None:
    done: list[tuple[int, Any]] = []

    def on_done(future: concurrent.futures.Future[Any]) -> None:
        done.append((threading.get_ident(), future.exception() or future.result()))

    async def engine_thread_name() -> str:
        return threading.current_thread().name

    async def fail() -> None:
        raise ValueError("boom")

    bridge.call(engine_thread_name(), on_done)
    bridge.call(fail(), on_done)
    wait_until(lambda: len(done) == 2)

    gui_thread = threading.get_ident()
    assert done[0] == (gui_thread, "mpos-engine")
    assert done[1][0] == gui_thread
    assert isinstance(done[1][1], ValueError)


def test_engine_loop_never_runs_on_gui_thread(bridge: QtBridge) -> None:
    future = bridge.call(_loop_thread())
    assert future.result(timeout=2) != threading.get_ident()


async def _loop_thread() -> int:
    return threading.get_ident()
