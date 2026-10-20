"""PacketInspected telemetry: what each hop reports, and that it never leaks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from mpos.crypto.keys import generate_private_key
from mpos.crypto.onion import build_onion
from mpos.events import Event, PacketInspected, Subscription
from mpos.net.directory import Directory, NodeInfo
from mpos.net.node import NodeDaemon

NAMES = ["alice", "guard", "middle", "exit", "bob"]
ROUTE = ["guard", "middle", "exit", "bob"]
SECRET = b"TOP-SECRET-PAYLOAD::" * 400  # recognisable plaintext, 8 KB


async def _make(inspect: bool) -> tuple[dict[str, NodeDaemon], Subscription]:
    directory = Directory()
    nodes: dict[str, NodeDaemon] = {}
    first = NodeDaemon("x", generate_private_key(), directory)  # shared bus owner
    sub = first.events.subscribe()

    for name in NAMES:
        key = generate_private_key()
        node = NodeDaemon(name, key, directory, events=first.events, inspect=inspect)
        port = await node.start()
        directory.add(NodeInfo(name, "127.0.0.1", port, key.public_key()))
        nodes[name] = node
    return nodes, sub


@pytest.fixture
async def net() -> AsyncIterator[tuple[dict[str, NodeDaemon], Subscription]]:
    nodes, sub = await _make(inspect=True)
    yield nodes, sub
    for node in nodes.values():
        await node.stop()


def _route(nodes: dict[str, NodeDaemon]) -> list:  # type: ignore[type-arg]
    return [nodes["alice"].directory.get(n).hop for n in ROUTE]


async def _inspected(sub: Subscription, count: int) -> list[PacketInspected]:
    found: list[PacketInspected] = []
    async with asyncio.timeout(5):
        while len(found) < count:
            event: Event = await sub.get()
            if isinstance(event, PacketInspected):
                found.append(event)
    return found


async def test_each_hop_reports_only_its_own_view(
    net: tuple[dict[str, NodeDaemon], Subscription],
) -> None:
    nodes, sub = net
    await nodes["alice"].send_onion("guard", build_onion(_route(nodes), SECRET))

    events = {e.node_id: e for e in await _inspected(sub, 4)}

    # Each hop sees its link predecessor and decrypts exactly one routing header
    assert [events[n].prev_hop for n in ROUTE] == ["alice", "guard", "middle", "exit"]
    assert events["guard"].routing_header == {"cmd": "relay", "next": "middle"}
    assert events["middle"].routing_header == {"cmd": "relay", "next": "exit"}
    assert events["exit"].routing_header == {"cmd": "relay", "next": "bob"}
    assert events["bob"].routing_header["cmd"] == "deliver"
    assert [events[n].outcome for n in ROUTE[:3]] == [
        "relayed to middle",
        "relayed to exit",
        "relayed to bob",
    ]

    # Size alone reveals position (the documented leak): 4, 3, 2, 1 layers
    assert [events[n].layers_remaining for n in ROUTE] == [4, 3, 2, 1]

    for event in events.values():
        assert event.gcm_tag_status == "VERIFIED"
        assert event.frame_type == "ONION"
        assert len(bytes.fromhex(event.raw_header_hex)) == 17
        assert bytes.fromhex(event.raw_header_hex)[:2] == b"MP"
        assert len(bytes.fromhex(event.layer_prefix_hex)) == 45
        assert len(bytes.fromhex(event.ciphertext_snippet_hex)) == 32
        assert len(bytes.fromhex(event.gcm_tag_hex)) == 16


async def test_no_event_contains_any_plaintext(
    net: tuple[dict[str, NodeDaemon], Subscription],
) -> None:
    nodes, sub = net
    await nodes["alice"].send_onion("guard", build_onion(_route(nodes), SECRET))

    for event in await _inspected(sub, 4):
        dump = repr(event).encode() + b"".join(
            bytes.fromhex(h)
            for h in (
                event.raw_header_hex,
                event.layer_prefix_hex,
                event.ciphertext_snippet_hex,
                event.gcm_tag_hex,
            )
        )
        # Not the marker, not even an 8-byte slice of the payload
        for start in range(0, 80, 4):
            assert SECRET[start : start + 8] not in dump
            assert SECRET[start : start + 8].hex().encode() not in dump


async def test_tampered_layer_reports_invalid_tag(
    net: tuple[dict[str, NodeDaemon], Subscription],
) -> None:
    nodes, sub = net
    onion = bytearray(build_onion(_route(nodes), b"x"))
    onion[300] ^= 1

    await nodes["alice"].send_onion("guard", bytes(onion))
    (event,) = await _inspected(sub, 1)

    assert event.node_id == "guard"
    assert event.gcm_tag_status == "FAILED (InvalidTag)"
    assert event.routing_header == {}
    assert event.outcome == "dropped: layer rejected"


async def test_inspection_is_off_by_default() -> None:
    nodes, sub = await _make(inspect=False)
    try:
        await nodes["alice"].send_onion("guard", build_onion(_route(nodes), b"quiet"))
        async with asyncio.timeout(5):
            await nodes["bob"].delivered.get()
        await asyncio.sleep(0.05)
    finally:
        for node in nodes.values():
            await node.stop()

    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    assert not any(isinstance(e, PacketInspected) for e in events)
