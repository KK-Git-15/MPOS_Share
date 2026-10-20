"""Step 6d acceptance: 5 NodeDaemons on 127.0.0.1 forwarding real onions."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable

import pytest

from mpos.crypto.keys import generate_private_key
from mpos.crypto.onion import Command, Hop, _wrap_layer, build_onion
from mpos.errors import HopUnreachable
from mpos.net.directory import Directory, NodeInfo
from mpos.net.framing import Frame, FrameType, encode_frame
from mpos.net.link import open_link
from mpos.net.node import NodeDaemon

NAMES = ["sender", "guard", "middle", "exit", "receiver"]
ROUTE = ["guard", "middle", "exit", "receiver"]

Network = dict[str, NodeDaemon]


@pytest.fixture
async def net() -> AsyncIterator[Network]:
    directory = Directory()
    nodes: Network = {}

    for name in NAMES:
        key = generate_private_key()
        node = NodeDaemon(name, key, directory)
        port = await node.start()
        directory.add(NodeInfo(name, "127.0.0.1", port, key.public_key()))
        nodes[name] = node

    yield nodes

    for node in nodes.values():
        await node.stop()


def _route(net: Network, names: list[str] = ROUTE) -> list[Hop]:
    return [net["sender"].directory.get(name).hop for name in names]


async def _wait_until(predicate: Callable[[], bool], limit: float = 5) -> None:
    """Poll a stats counter; nodes expose no event for drops."""
    async with asyncio.timeout(limit):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def _delivered(node: NodeDaemon, limit: float = 5) -> bytes:
    async with asyncio.timeout(limit):
        return await node.delivered.get()


async def _raw_connect(node: NodeDaemon) -> asyncio.StreamWriter:
    _, writer = await asyncio.open_connection("127.0.0.1", node.port)
    return writer


# ============================================================
# HAPPY PATH
# ============================================================


async def test_onion_traverses_three_relays(net: Network) -> None:
    payload = os.urandom(200_000)

    await net["sender"].send_onion("guard", build_onion(_route(net), payload))

    assert await _delivered(net["receiver"]) == payload
    assert [net[n].stats.relayed for n in ["guard", "middle", "exit"]] == [1, 1, 1]
    assert net["receiver"].stats.delivered == 1
    assert net["receiver"].stats.relayed == 0


async def test_links_are_persistent_and_multiplexed(net: Network) -> None:
    payloads = [os.urandom(1000 + i) for i in range(50)]

    await asyncio.gather(
        *(
            net["sender"].send_onion("guard", build_onion(_route(net), p))
            for p in payloads
        )
    )
    received = [await _delivered(net["receiver"]) for _ in payloads]

    assert sorted(received) == sorted(payloads)

    # 50 concurrent onions shared exactly one outbound link per hop
    assert net["sender"].outbound_peers == {"guard"}
    assert net["guard"].outbound_peers == {"middle"}
    assert net["middle"].outbound_peers == {"exit"}
    assert net["exit"].outbound_peers == {"receiver"}
    assert net["receiver"].outbound_peers == set()


async def test_different_routes_share_nodes(net: Network) -> None:
    """Any node can play any role; the route lives in the onion."""
    onion = build_onion(_route(net, ["exit", "guard", "receiver", "middle"]), b"hi")

    await net["sender"].send_onion("exit", onion)

    assert await _delivered(net["middle"]) == b"hi"


# ============================================================
# FAILURES: DROP, COUNT, KEEP SERVING
# ============================================================


async def test_tampered_onion_dropped_at_guard(net: Network) -> None:
    onion = bytearray(build_onion(_route(net), b"x"))
    onion[300] ^= 1

    await net["sender"].send_onion("guard", bytes(onion))
    await _wait_until(lambda: net["guard"].stats.dropped_bad_layer == 1)

    assert net["guard"].stats.relayed == 0

    # Guard keeps serving after the drop
    await net["sender"].send_onion("guard", build_onion(_route(net), b"ok"))
    assert await _delivered(net["receiver"]) == b"ok"


async def test_tampered_inner_layer_dropped_at_middle(net: Network) -> None:
    inner = bytearray(build_onion(_route(net, ROUTE[1:]), b"x"))
    inner[200] ^= 1
    guard_hop = _route(net, ["guard"])[0]
    onion = _wrap_layer(guard_hop, Command.RELAY, "middle", bytes(inner))

    await net["sender"].send_onion("guard", onion)
    await _wait_until(lambda: net["middle"].stats.dropped_bad_layer == 1)

    assert net["guard"].stats.relayed == 1
    assert net["exit"].stats.relayed == 0
    assert net["receiver"].delivered.empty()


async def test_unknown_next_hop_dropped(net: Network) -> None:
    ghost = Hop("ghost", generate_private_key().public_key())
    onion = build_onion([*_route(net, ["guard"]), ghost], b"x")

    await net["sender"].send_onion("guard", onion)
    await _wait_until(lambda: net["guard"].stats.dropped_unroutable == 1)


async def test_next_hop_down_dropped(net: Network) -> None:
    await net["exit"].stop()

    await net["sender"].send_onion("guard", build_onion(_route(net), b"x"))
    await _wait_until(lambda: net["middle"].stats.dropped_unreachable == 1)

    assert net["guard"].stats.relayed == 1


async def test_send_to_unknown_or_dead_first_hop(net: Network) -> None:
    with pytest.raises(HopUnreachable, match="not in the directory"):
        await net["sender"].send_onion("ghost", b"x")

    await net["guard"].stop()
    with pytest.raises(HopUnreachable):
        await net["sender"].send_onion("guard", b"x")


@pytest.mark.parametrize(
    "garbage",
    [
        b"GET / HTTP/1.1\r\n\r\n",
        os.urandom(64),
        encode_frame(Frame(FrameType.ONION, b"no hello first")),
        encode_frame(Frame(FrameType.HELLO, b"{not json")),
        encode_frame(
            Frame(FrameType.HELLO, json.dumps({"node_id": "x", "version": 1}).encode())
        ),
    ],
)
async def test_bad_handshakes_do_not_crash_node(net: Network, garbage: bytes) -> None:
    writer = await _raw_connect(net["guard"])
    writer.write(garbage)
    await writer.drain()
    writer.close()

    await _wait_until(lambda: net["guard"].stats.protocol_errors >= 1)

    await net["sender"].send_onion("guard", build_onion(_route(net), b"still up"))
    assert await _delivered(net["receiver"]) == b"still up"


async def test_oversized_frame_after_handshake_drops_link(net: Network) -> None:
    guard = net["sender"].directory.get("guard")
    link = await open_link(
        guard.host, guard.port, local_id="tester", expected_peer_id="guard"
    )
    oversized = bytearray(encode_frame(Frame(FrameType.ONION, b"")))
    oversized[13:17] = (32 * 1024 * 1024).to_bytes(4, "big")
    link._writer.write(bytes(oversized))

    await _wait_until(lambda: net["guard"].stats.protocol_errors == 1)
    async with asyncio.timeout(5):
        assert await link.receive() is None  # guard closed the link

    await link.close()


# ============================================================
# HANDSHAKE
# ============================================================


async def test_dialing_wrong_identity_is_refused(net: Network) -> None:
    guard = net["sender"].directory.get("guard")

    with pytest.raises(HopUnreachable, match="peer is 'guard'"):
        await open_link(
            guard.host, guard.port, local_id="tester", expected_peer_id="middle"
        )


async def test_ping_pong(net: Network) -> None:
    guard = net["sender"].directory.get("guard")
    link = await open_link(
        guard.host, guard.port, local_id="tester", expected_peer_id="guard"
    )

    await link.send(Frame(FrameType.PING, b"t=1"))
    async with asyncio.timeout(5):
        reply = await link.receive()

    assert reply == Frame(FrameType.PONG, b"t=1")
    await link.close()
