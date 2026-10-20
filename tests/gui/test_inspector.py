"""Packet Inspector: formatting, panel behaviour and map interaction."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpos.events import CircuitBuilt, NodeStatus, PacketInspected, TransferStarted
from mpos.gui.panels.base import Colors, NodeSummary
from mpos.gui.panels.inspector import (
    HISTORY_PER_NODE,
    InspectorPanel,
    frame_dump,
    hexdump,
    position_label,
    visibility,
)
from mpos.gui.panels.routing_map import RoutingMapPanel
from tests.gui.qt_helpers import pump

NODES = [
    NodeSummary(name, "127.0.0.1", 9100 + i, ("relay", "receiver"))
    for i, name in enumerate(["alice", "bob", "r0", "r1", "r2", "r3", "r4"])
]


@pytest.fixture(autouse=True)
def _app(qapp: QApplication) -> None:
    return None


def _packet(
    node: str = "r1",
    *,
    prev: str = "r0",
    nxt: str = "r2",
    layers: int | None = 3,
    status: str = "VERIFIED",
    ts: float = 1000.0,
) -> PacketInspected:
    routing = {} if status != "VERIFIED" else {"cmd": "relay", "next": nxt}
    if status == "VERIFIED" and layers == 1:
        routing = {"cmd": "deliver", "payload": "4096 B handed to the transfer layer"}
    return PacketInspected(
        ts=ts,
        node_id=node,
        prev_hop=prev,
        circuit_id=5,
        layers_remaining=layers,
        frame_type="ONION",
        frame_size=4477,
        raw_header_hex=("4d50020300000000050000000000001179"),
        layer_prefix_hex="02" + "aa" * 32 + "bb" * 12,
        ciphertext_snippet_hex="cc" * 32,
        ciphertext_size=4416,
        gcm_tag_hex="dd" * 16,
        gcm_tag_status=status,
        routing_header=routing,
        outcome=f"relayed to {nxt}" if status == "VERIFIED" else "dropped",
    )


# ============================================================
# FORMATTING
# ============================================================


def test_hexdump_layout() -> None:
    lines = hexdump(b"MP" + bytes(range(20)), offset=0x11)

    assert lines[0].startswith("000011  4d 50 00 01")
    assert lines[0].endswith("|MP..............|")
    assert lines[1].startswith("000021  0e 0f 10 11")
    assert len(lines) == 2


@pytest.mark.parametrize(
    ("layers", "expected"),
    [
        (4, "Guard — 4 layers left"),
        (3, "Middle — 3 layers left"),
        (2, "Exit — 2 layers left"),
        (1, "Final hop (receiver)"),
        (None, "Unknown (size fits no layer count)"),
    ],
)
def test_position_inferred_from_layers(layers: int | None, expected: str) -> None:
    assert position_label(_packet(layers=layers)) == expected


def test_frame_dump_sections_and_offsets() -> None:
    dump = frame_dump(_packet())

    assert "Frame header · 17 B" in dump
    assert "Layer prefix · 45 B" in dump
    assert "Ciphertext · 32 of 4,416 B shown" in dump
    assert "… 4,384 more bytes …" in dump
    assert "AES-GCM tag · 16 B" in dump
    assert "\n000011  02 aa aa" in dump  # prefix starts after the 17-byte header
    assert "\n00003e  cc cc" in dump  # ciphertext after header + prefix


def test_visibility_is_honest_about_guard_middle_and_exit() -> None:
    senders, receivers = {"alice"}, {"bob"}

    guard_seen, guard_hidden = visibility(
        _packet(prev="alice", nxt="r1", layers=4), senders, receivers
    )
    assert any("alice is a sender" in line for line in guard_seen)
    assert "Who the sender is" not in guard_hidden
    assert "Who the receiver is" in guard_hidden

    middle_seen, middle_hidden = visibility(_packet(), senders, receivers)
    assert not any("⚠" in line for line in middle_seen)
    assert {"Who the sender is", "Who the receiver is"} <= set(middle_hidden)

    exit_seen, exit_hidden = visibility(
        _packet(prev="r1", nxt="bob", layers=2), senders, receivers
    )
    assert any("bob is a receiver" in line for line in exit_seen)
    assert "Who the receiver is" not in exit_hidden

    failed_seen, _ = visibility(_packet(status="FAILED (InvalidTag)"), set(), set())
    assert any("failed authentication" in line for line in failed_seen)


# ============================================================
# PANEL
# ============================================================


def test_panel_shows_latest_packet_and_badge() -> None:
    panel = InspectorPanel()
    panel.show()
    panel.handle_events(
        [_packet(ts=1.0), _packet(ts=2.0, nxt="r3"), _packet("r4", ts=3.0)]
    )

    panel.inspect("r1")

    assert panel.shown is not None and panel.shown.ts == 2.0
    assert panel.info["Position"].text().startswith("Middle — 3 layers left")
    assert "VERIFIED" in panel.badge.text()
    assert Colors.ACKED.name() in panel.badge.styleSheet()
    assert panel.packets.count() == 2
    assert panel.routing_tree.topLevelItem(1).text(1) == "r3"  # type: ignore[union-attr]

    panel.handle_events([_packet(ts=4.0, status="FAILED (InvalidTag)")])
    panel.refresh()  # following live: jumps to the failed packet

    assert panel.shown is not None and panel.shown.ts == 4.0
    assert "FAILED (InvalidTag)" in panel.badge.text()
    assert Colors.FAILED.name() in panel.badge.styleSheet()
    assert panel.routing_tree.topLevelItem(0).text(1) == "decryption failed"  # type: ignore[union-attr]


def test_choosing_an_older_packet_stops_following() -> None:
    panel = InspectorPanel()
    panel.show()
    panel.handle_events([_packet(ts=float(i)) for i in range(3)])
    panel.inspect("r1")

    panel.packets.setCurrentRow(2)  # oldest
    assert not panel.follow.isChecked()
    assert panel.shown is not None and panel.shown.ts == 0.0

    panel.handle_events([_packet(ts=9.0)])
    panel.refresh()
    assert panel.shown is not None and panel.shown.ts == 0.0  # stays put


def test_history_is_bounded_per_node() -> None:
    panel = InspectorPanel()
    panel.handle_events([_packet(ts=float(i)) for i in range(HISTORY_PER_NODE + 30)])

    history = panel.history("r1")
    assert len(history) == HISTORY_PER_NODE
    assert history[0].ts == 30.0


def test_senders_and_receivers_are_learned_from_events() -> None:
    panel = InspectorPanel()
    panel.show()
    panel.handle_events(
        [
            TransferStarted(
                node_id="alice",
                transfer_id="t",
                role="send",
                peer_id="bob",
                filename="f",
                size=1,
                segments=1,
                k=4,
                n=6,
            ),
            _packet("r0", prev="alice", layers=4),
        ]
    )
    panel.inspect("r0")

    assert "alice is a sender" in panel.visible_label.text()


# ============================================================
# MAP INTERACTION
# ============================================================


@pytest.fixture
def routing() -> RoutingMapPanel:
    # Nodes load while the map is hidden, then it is shown at full size:
    # the order that left the map stuck at ~0.04x zoom (regression).
    panel = RoutingMapPanel()
    panel.resize(1500, 1100)
    panel.set_directory(NODES)
    panel.show()
    panel.handle_events([NodeStatus(node_id=n.node_id, running=True) for n in NODES])
    pump(50)
    panel.refresh()
    return panel


def _double_click(panel: RoutingMapPanel, point: QPoint) -> None:
    QTest.mouseDClick(
        panel.view.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point,
    )


def test_map_refits_after_loading_while_hidden(routing: RoutingMapPanel) -> None:
    """Regression: a fit done while hidden left the map at ~0.04x."""
    assert routing.view.transform().m11() > 0.5


def test_double_clicking_each_node_inspects_exactly_that_node(
    routing: RoutingMapPanel,
) -> None:
    """Regression: at the stuck zoom, labels captured other nodes' clicks."""
    activated: list[str] = []
    routing.inspect_requested.connect(activated.append)

    for node in NODES:
        _double_click(
            routing, routing.view.mapFromScene(routing.position(node.node_id))
        )

    assert activated == [n.node_id for n in NODES]


def test_double_click_on_circuit_picks_nearest_node_and_empty_space_nothing(
    routing: RoutingMapPanel,
) -> None:
    routing.handle_events(
        [
            CircuitBuilt(
                node_id="alice",
                transfer_id="t",
                path=("r0", "r1", "r2"),
                dest="bob",
                kind="SHARD",
            )
        ]
    )
    routing.refresh()
    activated: list[str] = []
    routing.inspect_requested.connect(activated.append)

    # A point on the r1 -> r2 segment, a quarter of the way from r1
    r1, r2 = routing.position("r1"), routing.position("r2")
    near_r1 = r1 + (r2 - r1) * 0.25
    _double_click(routing, routing.view.mapFromScene(near_r1))
    _double_click(routing, QPoint(5, 5))  # empty corner

    assert activated == ["r1"]


def test_inspected_node_gets_highlight(routing: RoutingMapPanel) -> None:
    routing.set_inspected("r3")
    routing.refresh()
    item = routing.node_item("r3")

    assert item is not None
    assert item.pen().style() == Qt.PenStyle.DashLine  # type: ignore[attr-defined]
