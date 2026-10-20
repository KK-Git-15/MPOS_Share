"""Step 7c: a real transfer started and monitored from the GUI."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpos.engine import Engine
from mpos.gui.app import MainWindow
from mpos.gui.bridge import EngineThread, QtBridge
from mpos.net.transfer import TransferConfig
from tests.gui.qt_helpers import pump, wait_until
from tests.helpers import free_port_block

# A 10 ms timer firing more than this late means the window froze
MAX_GUI_STALL = 0.25


@pytest.fixture
def window(qapp: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    engine_thread = EngineThread(Engine(tmp_path / "data", inspect_packets=True))
    engine_thread.start()
    win = MainWindow(
        QtBridge(engine_thread),
        demo_dir=tmp_path / "cluster",
        demo_port=free_port_block(7),
        # Fail fast in the error-path test; healthy transfers never retry
        transfer_defaults=TransferConfig(
            ack_timeout=1.0, max_retries=1, retry_backoff=0.01
        ),
    )
    win.show()
    yield win
    win.close()
    assert not engine_thread.is_running, "closing the window must stop the engine"


class Heartbeat:
    """Records how late a 10 ms timer fires, i.e. how long the GUI stalls."""

    def __init__(self) -> None:
        self.gaps: list[float] = []
        self._last = time.perf_counter()
        self._timer = QTimer()
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._beat)

    def _beat(self) -> None:
        now = time.perf_counter()
        self.gaps.append(now - self._last)
        self._last = now

    def start(self) -> None:
        self._last = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()


def _start_demo(window: MainWindow) -> None:
    window.nodes.start_demo_requested.emit()  # the "Start demo cluster" button
    wait_until(lambda: len(window.nodes.running) == 7, 15_000)


def test_gui_transfer_end_to_end_stays_responsive(
    window: MainWindow, tmp_path: Path
) -> None:
    _start_demo(window)
    source = tmp_path / "video.bin"
    source.write_bytes(os.urandom(12 * 1024 * 1024 + 99))

    page = window.transfers
    window.nav.setCurrentRow(window.panels.index(page))
    pump(100)
    page.from_combo.setCurrentText("alice")
    page.to_combo.setCurrentText("bob")
    page.file_edit.setText(str(source))
    page.segment_spin.setValue(256)

    heartbeat = Heartbeat()
    heartbeat.start()
    page.send_button.click()
    # Watch it on the live map while it runs
    window.nav.setCurrentRow(window.panels.index(window.routing_map))
    wait_until(lambda: sum(v.ok is not None for v in page.views()) == 2, 60_000)
    heartbeat.stop()
    pump(150)

    # Delivered and verified on both ends
    send, receive = page.views()
    assert (send.role, send.status) == ("send", "verified ✓")
    assert (receive.role, receive.status) == ("receive", "verified ✓")
    assert send.detail == receive.detail  # same sha256
    received = tmp_path / "cluster" / "bob" / "inbox" / "video.bin"
    assert received.read_bytes() == source.read_bytes()
    assert "Delivered video.bin to bob" in page.message.text()

    # The grid saw every segment reach K acknowledged shards
    assert send.segments == 49
    for segment in range(send.segments):
        acked = sum(
            1
            for shard in range(send.n)
            if send.cells.get((segment, shard)) is not None
            and send.cells[(segment, shard)].name == "ACKED"
        )
        assert acked >= send.k
    assert send.segments_done == receive.segments_done == set(range(49))

    # Other panels followed along
    assert sum(int(window.nodes.table.item(r, 5).text()) for r in range(7)) > 0  # type: ignore[union-attr]
    assert any(
        "sha256 OK" in window.logs.model.record(r).message
        for r in range(window.logs.model.rowCount())
    )
    assert window.transfers_label.text() == "Active transfers: 0"

    # The window never froze while thousands of events streamed in
    assert len(heartbeat.gaps) > 10
    assert max(heartbeat.gaps) < MAX_GUI_STALL, f"GUI stalled {max(heartbeat.gaps)}s"


def test_errors_surface_in_the_window_not_as_exceptions(
    window: MainWindow, tmp_path: Path
) -> None:
    window.open_directory(tmp_path / "missing.json")
    wait_until(lambda: "Could not load" in window.statusBar().currentMessage())

    _start_demo(window)
    window.start_nodes(["ghost", "alice"], "127.0.0.1")
    wait_until(lambda: "ghost" in window.statusBar().currentMessage())
    assert "already running" in window.statusBar().currentMessage()

    # Receiver offline: the transfer fails cleanly and says why
    window.stop_nodes(["bob"])
    wait_until(lambda: "bob" not in window.nodes.running)
    source = tmp_path / "f.bin"
    source.write_bytes(b"hello")
    window.send_file("alice", "bob", str(source), 4, 6, 64)
    wait_until(lambda: "Transfer failed" in window.transfers.message.text(), 20_000)
    assert "manifest" in window.transfers.message.text()


def test_open_directory_populates_every_panel(
    window: MainWindow, tmp_path: Path
) -> None:
    _start_demo(window)
    nodes_json = tmp_path / "cluster" / "nodes.json"

    window.open_directory(nodes_json)
    wait_until(lambda: "Loaded 7 nodes" in window.statusBar().currentMessage())
    pump(100)

    assert window.nodes.table.rowCount() == 7
    assert window.transfers.to_combo.count() == 7
    assert window.fault_lab.node_combo.count() == 7
    assert len(window.routing_map._node_items) == 7


def test_double_click_relay_opens_inspector_with_verified_packet(
    window: MainWindow, tmp_path: Path
) -> None:
    _start_demo(window)
    pump(100)
    source = tmp_path / "f.bin"
    source.write_bytes(os.urandom(200_000))
    window.send_file("alice", "bob", str(source), 4, 6, 64)
    wait_until(lambda: "Delivered" in window.transfers.message.text(), 20_000)

    window.nav.setCurrentRow(window.panels.index(window.routing_map))
    pump(100)
    view = window.routing_map.view
    QTest.mouseDClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        view.mapFromScene(window.routing_map.position("r1")),
    )
    pump(100)

    assert window.inspector_dock.isVisible()
    assert window.inspector.node == "r1"
    shown = window.inspector.shown
    assert shown is not None and shown.node_id == "r1"
    assert shown.gcm_tag_status == "VERIFIED"
    assert shown.routing_header["cmd"] == "relay"
    assert "VERIFIED" in window.inspector.badge.text()
    # The relay's view never includes the file: no 16-byte slice of it
    dump = repr(shown)
    data = source.read_bytes()
    assert not any(data[i : i + 16].hex() in dump for i in range(0, 4096, 16))


def test_fault_lab_faults_hit_a_live_transfer_which_survives(
    window: MainWindow, tmp_path: Path
) -> None:
    _start_demo(window)
    pump(100)
    lab = window.fault_lab
    # The fixture fails fast (1 retry); surviving loss needs real retries
    window.transfer_defaults = TransferConfig(
        ack_timeout=0.4, retry_backoff=0.05, max_retries=12, complete_timeout=30
    )

    # 10% packet loss everywhere, and kill a relay, all from the Fault Lab
    lab.target_combo.setCurrentText("All nodes")
    lab.drop.setValue(10)
    lab.seed.setValue(7)
    lab._apply()
    wait_until(lambda: "drop 10%" in lab.active_label.text())
    lab.node_combo.setCurrentText("r0")
    lab.kill_requested.emit("r0")
    wait_until(lambda: "r0" not in window.nodes.running)

    source = tmp_path / "f.bin"
    source.write_bytes(os.urandom(200_000))
    window.send_file("alice", "bob", str(source), 4, 6, 64)
    wait_until(
        lambda: any(
            word in window.transfers.message.text() for word in ("Delivered", "failed")
        ),
        45_000,
    )
    assert "Delivered" in window.transfers.message.text(), (
        window.transfers.message.text()
    )

    received = tmp_path / "cluster" / "bob" / "inbox" / "f.bin"
    assert received.read_bytes() == source.read_bytes()
    pump(100)
    assert lab.counts["drop"] > 0

    lab.clear_requested.emit()
    wait_until(lambda: lab.active_label.text() == "No faults active")
    lab.revive_requested.emit("r0")
    wait_until(lambda: "r0" in window.nodes.running)
