"""Relay health monitor: heartbeats plus onion probes (CLAUDE.md §3.3.2).

Every ``interval`` seconds each node checks every relay in two ways:

    heartbeat  PING/PONG on the link. Detects dead, killed and partitioned
               relays. A blackhole still answers, so pings cannot see it.
    probe      a tiny onion sent me -> relay -> me. A relay that forwards
               onions returns it; a blackhole swallows it. The probe's
               random nonce is inside a layer only this node can open, so
               it cannot be faked.

After ``misses`` consecutive heartbeat failures a relay is *down*; after
``misses`` consecutive lost probes (while it still answers pings) it is
*suspect*. One success clears either. Down and suspect relays get large
but soft routing penalties: they are avoided while alternatives exist.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from typing import Final

from mpos.crypto.onion import build_onion
from mpos.errors import HopUnreachable
from mpos.events import NodeHealth
from mpos.net.messages import Message, MessageType, encode_message
from mpos.net.node import NodeDaemon
from mpos.net.paths import RELAY_ROLE

log = logging.getLogger(__name__)

DOWN_PENALTY: Final = 100  # dwarfs per-transfer penalties, but is not a ban
SUSPECT_PENALTY: Final = 50


class HealthMonitor:
    def __init__(
        self,
        node: NodeDaemon,
        *,
        interval: float = 2.0,
        misses: int = 3,
        ping_limit: float = 1.0,
        probes: bool = True,
    ) -> None:
        self._node = node
        self.interval = interval
        self.misses = misses
        self.ping_limit = ping_limit
        self.probes = probes
        self.rtt_ms: dict[str, float] = {}
        self._ping_failures: Counter[str] = Counter()
        self._probe_failures: Counter[str] = Counter()
        self._down: set[str] = set()
        self._suspect: set[str] = set()
        self._probe_waiters: dict[str, asyncio.Future[None]] = {}
        self._task: asyncio.Task[None] | None = None

    @property
    def down(self) -> set[str]:
        """Relays that stopped answering heartbeats."""
        return set(self._down)

    @property
    def suspect(self) -> set[str]:
        """Relays that answer heartbeats but lose onion probes (blackholes)."""
        return set(self._suspect)

    def penalty(self) -> Counter[str]:
        """Routing penalty for relays currently down or suspect."""
        penalty = Counter({peer: SUSPECT_PENALTY for peer in self._suspect})
        penalty.update({peer: DOWN_PENALTY for peer in self._down})
        return penalty

    # ========================================================
    # LIFECYCLE
    # ========================================================

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(self.interval)

    # ========================================================
    # CHECKS
    # ========================================================

    async def check_once(self) -> None:
        """Ping and probe every relay once, concurrently; update verdicts."""
        peers = [
            info.node_id
            for info in self._node.directory
            if RELAY_ROLE in info.roles and info.node_id != self._node.node_id
        ]
        pings = asyncio.gather(
            *(self._node.ping(peer, self.ping_limit) for peer in peers),
            return_exceptions=True,
        )
        probes = asyncio.gather(*(self._probe(peer) for peer in peers))
        ping_results, probe_results = await asyncio.gather(pings, probes)

        for peer, ping, probe_ok in zip(
            peers, ping_results, probe_results, strict=True
        ):
            if isinstance(ping, float):
                self._heartbeat_ok(peer, ping * 1000)
                self._probe_result(peer, probe_ok)
            else:
                self._heartbeat_missed(peer)  # a dead relay is not "suspect"

    async def _probe(self, peer: str) -> bool:
        """Send me -> peer -> me; True if it came back in time."""
        if not self.probes:
            return True

        nonce = os.urandom(16).hex()
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._probe_waiters[nonce] = waiter
        try:
            directory = self._node.directory
            route = [directory.get(peer).hop, directory.get(self._node.node_id).hop]
            payload = encode_message(Message(MessageType.PROBE, {"nonce": nonce}))
            async with asyncio.timeout(self.ping_limit):
                await self._node.send_onion(peer, build_onion(route, payload))
                await waiter
            return True
        except (HopUnreachable, TimeoutError, KeyError):
            return False
        finally:
            self._probe_waiters.pop(nonce, None)

    def probe_returned(self, nonce: str) -> bool:
        """Called by the transfer layer when a PROBE arrives back here."""
        waiter = self._probe_waiters.pop(nonce, None)
        if waiter is None or waiter.done():
            return False
        waiter.set_result(None)
        return True

    # ========================================================
    # VERDICTS
    # ========================================================

    def _heartbeat_ok(self, peer: str, rtt_ms: float) -> None:
        self.rtt_ms[peer] = rtt_ms
        self._ping_failures[peer] = 0
        if peer in self._down:
            self._down.discard(peer)
            log.info("[%s] %s is back up", self._node.node_id, peer)
            self._publish(peer, True, "heartbeat", rtt_ms)

    def _heartbeat_missed(self, peer: str) -> None:
        self.rtt_ms.pop(peer, None)
        self._ping_failures[peer] += 1
        if self._ping_failures[peer] >= self.misses and peer not in self._down:
            self._down.add(peer)
            log.warning(
                "[%s] %s is down (%d missed heartbeats)",
                self._node.node_id,
                peer,
                self._ping_failures[peer],
            )
            self._publish(peer, False, "heartbeat", None)

    def _probe_result(self, peer: str, ok: bool) -> None:
        if ok:
            self._probe_failures[peer] = 0
            if peer in self._suspect:
                self._suspect.discard(peer)
                log.info("[%s] %s forwards onions again", self._node.node_id, peer)
                self._publish(peer, True, "probe", self.rtt_ms.get(peer))
            return

        self._probe_failures[peer] += 1
        if self._probe_failures[peer] >= self.misses and peer not in self._suspect:
            self._suspect.add(peer)
            log.warning(
                "[%s] %s answers heartbeats but lost %d probes: suspected blackhole",
                self._node.node_id,
                peer,
                self._probe_failures[peer],
            )
            self._publish(peer, False, "probe", None)

    def _publish(self, peer: str, up: bool, reason: str, rtt_ms: float | None) -> None:
        self._node.events.publish(
            NodeHealth(
                node_id=self._node.node_id,
                peer_id=peer,
                up=up,
                reason=reason,
                rtt_ms=rtt_ms,
            )
        )
