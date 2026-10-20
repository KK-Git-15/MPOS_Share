"""Relay path selection for multi-path shard delivery (CLAUDE.md §3.1.5).

Nodes are ranked by (penalty, usage, random): relays on failed paths carry
a penalty and are avoided, and relays already used for this segment are
avoided so a segment's shards spread over different nodes where possible.
Nothing is excluded outright except the endpoints, so a small network
never runs out of paths.

With entry guards (Phase 9a) the first hop is drawn from the node's
usable guards, spread over them by a separate first-hop usage count;
later hops prefer non-guards but never ban them.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Collection, Mapping
from typing import TYPE_CHECKING

from mpos.net.directory import Directory

if TYPE_CHECKING:
    from mpos.net.guards import GuardSet

RELAY_ROLE = "relay"


def choose_path(
    directory: Directory,
    *,
    hops: int,
    exclude: Collection[str],
    rng: random.Random,
    penalty: Mapping[str, int] | None = None,
    usage: Counter[str] | None = None,
    guards: GuardSet | None = None,
    first_hops: Counter[str] | None = None,
    on_fallback: Callable[[str], None] | None = None,
) -> list[str]:
    """Pick ``hops`` distinct relay ids; with ``guards`` the first is a guard.

    ``usage`` is updated with the chosen nodes, and ``first_hops`` with the
    first hop (it ranks guards, so one segment's shards spread evenly over
    them). If no guard is usable, ``on_fallback(reason)`` is called and an
    unrestricted path is returned: guards must never stall a transfer.

    Raises:
        ValueError: fewer than ``hops`` eligible relays.
    """
    if hops < 1:
        raise ValueError("hops must be >= 1")

    penalty = penalty or {}
    used = usage if usage is not None else Counter()

    candidates = [
        node.node_id
        for node in directory
        if RELAY_ROLE in node.roles and node.node_id not in exclude
    ]

    if len(candidates) < hops:
        raise ValueError(
            f"Need {hops} relays besides the endpoints, only {len(candidates)} known"
        )

    if guards is not None:
        eligible = set(candidates)
        usable = [g for g in guards.usable(frozenset(exclude)) if g in eligible]
        if usable:
            return _guarded_path(
                candidates,
                usable,
                set(guards.ids),
                hops=hops,
                rng=rng,
                penalty=penalty,
                used=used,
                usage=usage,
                first_hops=first_hops,
            )
        if on_fallback is not None:
            on_fallback("no usable guards: all down, suspect or excluded")

    candidates.sort(key=lambda n: (penalty.get(n, 0), used[n], rng.random()))
    chosen = candidates[:hops]
    rng.shuffle(chosen)

    if usage is not None:
        usage.update(chosen)

    return chosen


def _guarded_path(
    candidates: list[str],
    usable: list[str],
    guard_ids: set[str],
    *,
    hops: int,
    rng: random.Random,
    penalty: Mapping[str, int],
    used: Counter[str],
    usage: Counter[str] | None,
    first_hops: Counter[str] | None,
) -> list[str]:
    firsts = first_hops if first_hops is not None else used
    usable.sort(key=lambda n: (penalty.get(n, 0), firsts[n], rng.random()))
    first = usable[0]

    # Later hops: prefer non-guards (soft tie-break after penalty)
    rest = [n for n in candidates if n != first]
    rest.sort(key=lambda n: (penalty.get(n, 0), n in guard_ids, used[n], rng.random()))
    later = rest[: hops - 1]
    rng.shuffle(later)
    chosen = [first, *later]

    if usage is not None:
        usage.update(chosen)
    if first_hops is not None:
        first_hops[first] += 1
    return chosen
