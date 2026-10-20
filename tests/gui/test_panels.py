"""Panels driven by synthetic events (no engine, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

import mpos.gui.panels.routing_map as routing_map
from mpos.events import (
    CircuitBuilt,
    FaultInjected,
    HopFailed,
    HopForwarded,
    LinkDown,
    LinkUp,
    LogRecord,
    NodeHealth,
    NodeStatus,
    PacketInspected,
    SegmentDecoded,
    ShardAcked,
    ShardRetried,
    ShardSent,
    TransferComplete,
    TransferProgress,
    TransferStarted,
)
from mpos.faults.injector import LinkFaults
from mpos.gui.panels.base import Colors, NodeSummary
from mpos.gui.panels.fault_lab import FaultLabPanel
from mpos.gui.panels.logs import MAX_RECORDS, LogsPanel
from mpos.gui.panels.nodes import NodesPanel
from mpos.gui.panels.routing_map import MAX_CIRCUITS, MAX_PACKETS, RoutingMapPanel
from mpos.gui.panels.transfers import CellState, TransfersPanel

NODES = [
    NodeSummary(name, "127.0.0.1", 9100 + i, ("relay", "receiver"))
    for i, name in enumerate(["alice", "bob", "r0", "r1", "r2"])
]
TID = "ab" * 16


@pytest.fixture(autouse=True)
def _app(qapp: QApplication) -> None:
    return None


# ============================================================
# NODES
# ============================================================


def test_nodes_panel_tracks_status_links_and_hops() -> None:
    panel = NodesPanel()
    panel.set_directory(NODES)
    panel.handle_events(
        [
            NodeStatus(node_id="r0", running=True),
            LinkUp(node_id="r0", peer_id="r1", outbound=True),
            LinkUp(node_id="r0", peer_id="alice", outbound=False),
            LinkDown(node_id="r0", peer_id="alice", outbound=False),
            HopForwarded(node_id="r0", prev_hop="alice", next_hop="r1"),
            HopForwarded(node_id="r0", prev_hop="alice", next_hop="r2"),
            HopFailed(node_id="r0", reason="bad layer"),
        ]
    )
    panel.refresh()

    row = [panel.table.item(2, c).text() for c in range(1, 8)]  # type: ignore[union-attr]
    assert row == ["r0", "127.0.0.1:9102", "relay, receiver", "ok", "1", "2", "1"]
    assert panel.table.item(2, 0).foreground().color() == Colors.RUNNING  # type: ignore[union-attr]
    assert panel.table.item(0, 0).foreground().color() == Colors.OFFLINE  # type: ignore[union-attr]
    assert panel.running == {"r0"}

    panel.handle_events([NodeStatus(node_id="r0", running=False)])
    panel.refresh()
    assert panel.table.item(2, 5).text() == "0"  # type: ignore[union-attr]


def test_nodes_panel_start_and_stop_selection() -> None:
    panel = NodesPanel()
    panel.set_directory(NODES)
    panel.handle_events([NodeStatus(node_id="r1", running=True)])
    panel.refresh()
    started: list[tuple[list[str], str]] = []
    stopped: list[list[str]] = []
    panel.start_requested.connect(lambda ids, bind: started.append((ids, bind)))
    panel.stop_requested.connect(stopped.append)

    panel.table.selectRow(2)  # r0, offline
    panel.table.selectionModel().select(
        panel.table.model().index(3, 0),
        panel.table.selectionModel().SelectionFlag.Select
        | panel.table.selectionModel().SelectionFlag.Rows,
    )  # + r1, running
    panel.bind_combo.setCurrentText("0.0.0.0")
    assert set(panel._selected()) == {"r0", "r1"}

    panel.start_requested.emit(panel._selected(running=False), "0.0.0.0")
    panel.stop_requested.emit(panel._selected(running=True))

    assert started == [(["r0"], "0.0.0.0")]
    assert stopped == [["r1"]]


# ============================================================
# TRANSFERS
# ============================================================


def _started(role: str = "send", node: str = "alice") -> TransferStarted:
    return TransferStarted(
        node_id=node,
        transfer_id=TID,
        role=role,
        peer_id="bob" if role == "send" else "alice",
        filename="video.bin",
        size=3 * 1024 * 1024,
        segments=3,
        k=4,
        n=6,
    )


def test_transfer_cell_states_never_regress() -> None:
    panel = TransfersPanel()
    panel.handle_events(
        [
            _started(),
            ShardSent(
                node_id="alice", transfer_id=TID, segment=0, shard=1, path=(), attempt=0
            ),
            ShardRetried(
                node_id="alice",
                transfer_id=TID,
                segment=0,
                shard=2,
                attempt=1,
                reason="x",
            ),
            ShardAcked(node_id="alice", transfer_id=TID, segment=0, shard=3),
            # A late re-send must not downgrade an acked shard
            ShardSent(
                node_id="alice", transfer_id=TID, segment=0, shard=3, path=(), attempt=1
            ),
            SegmentDecoded(node_id="alice", transfer_id=TID, segment=0),
            TransferProgress(
                node_id="alice", transfer_id=TID, segments_done=1, segments_total=3
            ),
        ]
    )
    (view,) = panel.views()

    assert view.cells == {
        (0, 1): CellState.SENT,
        (0, 2): CellState.RETRIED,
        (0, 3): CellState.ACKED,
    }
    assert view.segments_done == {0}
    assert view.done == 1


def test_transfer_rows_and_completion() -> None:
    panel = TransfersPanel()
    panel.handle_events([_started("send", "alice"), _started("receive", "bob")])
    panel.refresh()

    assert panel.table.rowCount() == 2
    assert panel._selected_view() is panel.views()[0]  # sender preferred

    panel.handle_events(
        [
            TransferComplete(
                node_id="alice", transfer_id=TID, role="send", ok=True, sha256="f" * 64
            ),
            TransferComplete(
                node_id="bob",
                transfer_id=TID,
                role="receive",
                ok=False,
                error="sha256 mismatch",
            ),
        ]
    )
    panel.refresh()

    assert panel.table.item(0, 6).text() == "verified ✓"  # type: ignore[union-attr]
    assert panel.table.item(1, 6).text() == "failed ✗"  # type: ignore[union-attr]
    assert panel.table.item(1, 6).toolTip() == "sha256 mismatch"  # type: ignore[union-attr]
    assert panel.views()[0].done == 3


def test_events_for_unknown_transfers_are_ignored() -> None:
    panel = TransfersPanel()
    panel.handle_events(
        [ShardAcked(node_id="x", transfer_id="nope", segment=0, shard=0)]
    )
    assert panel.views() == []


def test_send_form_validation_and_request(tmp_path: Path) -> None:
    panel = TransfersPanel()
    panel.set_directory(NODES)
    requests: list[tuple[object, ...]] = []
    panel.send_requested.connect(lambda *args: requests.append(args))

    panel._request_send()
    assert "Start a node" in panel.message.text()

    panel.handle_events([NodeStatus(node_id="alice", running=True)])
    panel.refresh()
    panel.to_combo.setCurrentText("alice")
    panel._request_send()
    assert "different from the sender" in panel.message.text()

    panel.to_combo.setCurrentText("bob")
    panel.file_edit.setText(str(tmp_path / "missing.bin"))
    panel._request_send()
    assert "existing file" in panel.message.text()

    source = tmp_path / "f.bin"
    source.write_bytes(b"x")
    panel.file_edit.setText(str(source))
    panel.k_spin.setValue(7)
    panel._request_send()
    assert "K cannot be larger" in panel.message.text()
    assert requests == []

    panel.k_spin.setValue(3)
    panel.n_spin.setValue(5)
    panel.segment_spin.setValue(64)
    panel._request_send()
    assert requests == [("alice", "bob", str(source), 3, 5, 64, "rtt")]

    panel.window_combo.setCurrentText("fixed")
    panel._request_send()
    assert requests[-1][-1] == "fixed"


# ============================================================
# LOGS
# ============================================================


def _log(i: int, level: str = "INFO", message: str = "") -> LogRecord:
    return LogRecord(
        level=level, logger="mpos.net.node", message=message or f"[r0] line {i}"
    )


def test_log_ring_buffer_keeps_newest(tmp_path: Path) -> None:
    panel = LogsPanel()
    panel.level_combo.setCurrentIndex(0)  # all levels
    panel.handle_events([_log(i) for i in range(MAX_RECORDS + 50)])
    panel.refresh()

    assert panel.model.rowCount() == MAX_RECORDS
    assert panel.model.record(0).message == "[r0] line 50"

    panel.handle_events([_log(-1, message="newest")])
    panel.refresh()
    assert panel.model.rowCount() == MAX_RECORDS
    assert panel.model.record(MAX_RECORDS - 1).message == "newest"


def test_log_filters_and_export(tmp_path: Path) -> None:
    panel = LogsPanel()
    panel.handle_events(
        [
            _log(1, "DEBUG", "[r0] noise"),
            _log(2, "INFO", "[r0] relayed alice -> r1"),
            _log(3, "WARNING", "[r1] dropped onion"),
            _log(4, "ERROR", "[bob] integrity failed"),
        ]
    )
    panel.refresh()
    assert panel.proxy.rowCount() == 3  # INFO and above by default

    panel.level_combo.setCurrentText("WARNING")
    assert panel.proxy.rowCount() == 2

    panel.level_combo.setCurrentIndex(0)
    panel.search.setText("R0")
    assert panel.proxy.rowCount() == 2  # case-insensitive, includes DEBUG

    out = tmp_path / "out.log"
    assert panel.export(out) == 2
    lines = out.read_text().splitlines()
    assert len(lines) == 2 and all("[r0]" in line for line in lines)


# ============================================================
# ROUTING MAP
# ============================================================


def test_map_caps_items_under_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(routing_map.time, "monotonic", lambda: clock[0])
    panel = RoutingMapPanel()
    panel.set_directory(NODES)

    panel.handle_events(
        [HopForwarded(node_id="r0", prev_hop="alice", next_hop="r1")] * 5000
        + [
            CircuitBuilt(
                node_id="alice",
                transfer_id=TID,
                path=("r0", "r1", "r2"),
                dest="bob",
                kind="SHARD",
            )
        ]
        * 200
    )
    panel.refresh()

    assert panel.visible_packets <= MAX_PACKETS
    assert panel.visible_circuits <= MAX_CIRCUITS

    clock[0] += 10  # everything expires
    panel.refresh()
    assert panel.visible_packets == panel.visible_circuits == 0


def test_map_ignores_unknown_nodes_and_flashes_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(routing_map.time, "monotonic", lambda: clock[0])
    panel = RoutingMapPanel()
    panel.set_directory(NODES)

    panel.handle_events(
        [
            HopForwarded(node_id="ghost", prev_hop="x", next_hop="y"),
            CircuitBuilt(
                node_id="alice", transfer_id=TID, path=("zz",), dest="bob", kind="SHARD"
            ),
            HopFailed(node_id="r1", reason="bad layer"),
        ]
    )
    panel.refresh()

    # Every hop above involves a node missing from the directory: nothing drawn
    assert panel.visible_packets == 0
    assert panel.visible_circuits == 0
    assert panel._node_items["r1"].brush().color() == Colors.FAILED

    clock[0] += 2
    panel.refresh()
    assert panel._node_items["r1"].brush().color() == Colors.OFFLINE


# ============================================================
# FAULT LAB
# ============================================================


def test_nodes_panel_shows_heartbeat_verdicts() -> None:
    panel = NodesPanel()
    panel.set_directory(NODES)
    panel.handle_events(
        [
            NodeHealth(node_id="alice", peer_id="r1", up=False),
            NodeHealth(node_id="bob", peer_id="r1", up=False),
        ]
    )
    panel.refresh()
    cell = panel.table.item(3, 4)
    # No node is running in this test, so the share is out of the observers
    assert cell is not None and cell.text() == "down (2/2 peers)"
    assert cell.foreground().color() == Colors.FAILED

    panel.handle_events([NodeStatus(node_id=n.node_id, running=True) for n in NODES])
    panel.handle_events(
        [NodeHealth(node_id="alice", peer_id="r2", up=False, reason="probe")]
    )
    panel.refresh()
    minority = panel.table.item(4, 4)
    assert minority is not None and minority.text() == "suspect (1/4 peers)"
    assert minority.foreground().color() == Colors.WARNING

    panel.handle_events(
        [
            NodeHealth(node_id="alice", peer_id="r1", up=True),
            NodeHealth(node_id="bob", peer_id="r1", up=True),
        ]
    )
    panel.refresh()
    assert panel.table.item(3, 4).text() == "ok"  # type: ignore[union-attr]


def test_fault_lab_controls_emit_commands() -> None:
    panel = FaultLabPanel()
    panel.set_directory(NODES)
    applied: list[tuple[object, ...]] = []
    nodes: list[tuple[str, object]] = []
    panel.apply_requested.connect(lambda *a: applied.append(a))
    panel.kill_requested.connect(lambda n: nodes.append(("kill", n)))
    panel.blackhole_requested.connect(
        lambda n, on: nodes.append(("blackhole", (n, on)))
    )
    panel.isolate_requested.connect(lambda n: nodes.append(("isolate", n)))

    assert panel.target_combo.count() == len(NODES) + 1
    assert panel.node_combo.count() == len(NODES)

    panel.target_combo.setCurrentText("r1")
    panel.drop.setValue(25)
    panel.delay.setValue(100)
    panel.jitter.setValue(20)
    panel.corrupt.setValue(5)
    panel.seed.setValue(9)
    panel._apply()
    target, faults, seed = applied[0]
    assert (target, seed) == ("r1", 9)
    assert faults == LinkFaults(drop=0.25, delay_ms=100, jitter_ms=20, corrupt=0.05)

    panel.node_combo.setCurrentText("r2")
    for button in panel.findChildren(QPushButton):
        if button.text() in ("Kill", "Blackhole on", "Isolate"):
            button.click()
    assert sorted(nodes, key=str) == sorted(
        [("kill", "r2"), ("blackhole", ("r2", True)), ("isolate", "r2")], key=str
    )


def test_fault_lab_counts_and_active_list() -> None:
    panel = FaultLabPanel()
    panel.handle_events(
        [FaultInjected(node_id="r0", peer_id="r1", kind="drop")] * 3
        + [FaultInjected(node_id="r0", peer_id="r1", kind="corrupt")]
    )
    panel.refresh()

    assert panel.counts == {"drop": 3, "corrupt": 1}
    assert "drop" in panel.counts_label.text()

    panel.show_active(["all links: drop 25%", "blackhole: r3"])
    assert "• blackhole: r3" in panel.active_label.text()
    panel.show_active([])
    assert panel.active_label.text() == "No faults active"


def test_guard_badges_halos_and_epochs() -> None:
    from mpos.events import GuardSelected, GuardStateChanged
    from mpos.gui.panels.guard_view import GuardView
    from mpos.gui.panels.routing_map import RoutingMapPanel

    first = [
        GuardSelected(owner="alice", node_id="r0", role="primary", epoch=1),
        GuardSelected(owner="alice", node_id="r1", role="backup", epoch=1),
    ]
    view = GuardView()
    assert view.update(first)
    assert view.role("r0") == ("primary", ["alice"])
    assert view.role("r2") is None

    # A newer epoch replaces alice's whole set
    view.update([GuardSelected(owner="alice", node_id="r2", role="primary", epoch=2)])
    assert view.role("r0") is None and view.role("r2") == ("primary", ["alice"])
    view.update([GuardStateChanged(owner="alice", node_id="r2", down=True)])
    assert view.is_down("r2")

    nodes = NodesPanel()
    nodes.set_directory(NODES)
    nodes.handle_events(
        [*first, GuardStateChanged(owner="alice", node_id="r1", down=True)]
    )
    nodes.refresh()
    guard_col = nodes.table.columnCount() - 1
    assert nodes.table.item(2, guard_col).text() == "P"  # type: ignore[union-attr]
    assert nodes.table.item(3, guard_col).text() == "B ✗"  # type: ignore[union-attr]
    assert nodes.table.item(4, guard_col).text() == ""  # type: ignore[union-attr]

    routing = RoutingMapPanel()
    routing.set_directory(NODES)
    routing.handle_events(first)
    routing.refresh()
    assert routing._halos["r0"].isVisible()
    assert routing._halos["r0"].pen().style() == Qt.PenStyle.SolidLine
    assert routing._halos["r1"].pen().style() == Qt.PenStyle.DotLine
    assert not routing._halos["r2"].isVisible()


def test_inspector_guard_honesty_line() -> None:
    from mpos.gui.panels.inspector import visibility

    event = PacketInspected(
        node_id="r0",
        prev_hop="alice",
        circuit_id=1,
        layers_remaining=4,
        frame_type="ONION",
        frame_size=4477,
        raw_header_hex="",
        layer_prefix_hex="",
        ciphertext_snippet_hex="",
        ciphertext_size=0,
        gcm_tag_hex="",
        gcm_tag_status="VERIFIED",
        routing_header={"cmd": "relay", "next": "r1"},
        outcome="relayed to r1",
    )
    seen, _ = visibility(event, {"alice"}, set())
    assert any("the guard still learns your address" in line for line in seen)


def test_cwnd_plot_follows_the_selected_transfer() -> None:
    from mpos.events import CwndChanged

    def cwnd(t: float, value: float, srtt: float | None) -> CwndChanged:
        return CwndChanged(
            ts=1000 + t,
            node_id="alice",
            transfer_id=TID,
            cwnd=value,
            inflight=int(value),
            inflight_bytes=1000,
            srtt_ms=srtt,
            base_rtt_ms=40.0,
            queue_est=0.5,
            rto_ms=1000.0,
            mode="slow_start",
        )

    panel = TransfersPanel()
    panel.resize(1000, 700)
    panel.handle_events(
        [_started(), cwnd(0, 6, None), cwnd(0.25, 12, 45.0), cwnd(0.5, 20, 48.0)]
    )
    panel.refresh()

    assert [p[1] for p in panel.plot.points(TID)] == [6, 12, 20]
    assert panel.plot.points(TID)[0][0] == 0.0  # times relative to the start
    assert panel.plot._shown == TID  # the sender row is auto-selected
    assert not panel.plot.grab().isNull()  # paints both lines without error
