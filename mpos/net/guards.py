"""Entry guards (Phase 9a, CLAUDE.md §3.4.1).

Without guards every shard draws a fresh random first hop, so over a long
transfer a malicious relay is almost certain to be the first hop of some
shard and see the sender. A guard set fixes the first hops once (3
primaries + 2 backups) and reuses them across transfers, so exposure is a
single draw instead of one per shard. Shards are still spread over the 3
primaries, so no single relay sees every shard.

Guards do not hide the sender from the guard itself and do not stop a
global observer.

Privacy: guard ids are never logged (only counts), and are stored only in
``data/<node_id>/guards.json`` (mode 0600).
"""

from __future__ import annotations

import json
import logging
import os
import random
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from mpos.net.directory import Directory

log = logging.getLogger(__name__)

GUARDS_VERSION: Final = 1
PRIMARY: Final = "primary"
BACKUP: Final = "backup"
RELAY_ROLE: Final = "relay"


@dataclass(frozen=True, slots=True)
class GuardConfig:
    enabled: bool = True
    primaries: int = 3
    backups: int = 2
    lifetime_days: float = 30
    fail_threshold: int = 3
    min_usable: int = 2
    reply_via_guards: bool = True

    def __post_init__(self) -> None:
        if self.primaries < 1 or self.backups < 0:
            raise ValueError("Need primaries >= 1 and backups >= 0")
        if self.lifetime_days <= 0:
            raise ValueError("lifetime_days must be positive")
        if self.fail_threshold < 1 or self.min_usable < 1:
            raise ValueError("fail_threshold and min_usable must be >= 1")


@dataclass(slots=True)
class Guard:
    node_id: str
    added_at: float  # unix time
    role: str  # PRIMARY | BACKUP
    fails: int = 0  # consecutive failures
    down: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "added_at": self.added_at,
            "role": self.role,
            "fails": self.fails,
            "down": self.down,
        }

    @classmethod
    def from_json(cls, data: Any) -> Guard:
        if not isinstance(data, dict):
            raise ValueError("Guard entry is not an object")
        role = data.get("role")
        if role not in (PRIMARY, BACKUP):
            raise ValueError(f"Unknown guard role {role!r}")
        node_id, added_at = data.get("id"), data.get("added_at")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("Guard id must be a non-empty string")
        if not isinstance(added_at, int | float) or isinstance(added_at, bool):
            raise ValueError("Guard added_at must be a number")
        fails, down = data.get("fails", 0), data.get("down", False)
        if not isinstance(fails, int) or not isinstance(down, bool):
            raise ValueError("Guard fails/down have the wrong type")
        return cls(node_id, float(added_at), role, fails, down)


class HealthView(Protocol):
    """What GuardSet reads from mpos.net.health.HealthMonitor."""

    @property
    def down(self) -> set[str]: ...

    @property
    def suspect(self) -> set[str]: ...


def eligible_relays(directory: Directory, self_id: str) -> list[str]:
    """Directory nodes that may serve as this node's guards."""
    return [
        node.node_id
        for node in directory
        if RELAY_ROLE in node.roles and node.node_id != self_id
    ]


def _write_private(path: Path, data: bytes) -> None:
    """Atomic write, readable only by the owner (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o600)  # in case the tmp file already existed
    os.replace(tmp, path)


class GuardSet:
    """A node's persistent entry guards. Use from one event loop only."""

    def __init__(
        self,
        guards: Iterable[Guard],
        *,
        path: Path | None,
        self_id: str,
        config: GuardConfig,
        rng: random.Random | None = None,
        health: HealthView | None = None,
    ) -> None:
        self.guards: list[Guard] = list(guards)
        self.path = path
        self.self_id = self_id
        self.config = config
        self.rng = rng or random.SystemRandom()
        # Phase 8 health monitor; Down or Suspect guards are not usable
        self.health = health

    # ========================================================
    # CREATION & PERSISTENCE
    # ========================================================

    @classmethod
    def load_or_create(
        cls,
        path: Path,
        directory: Directory,
        self_id: str,
        config: GuardConfig,
        rng: random.Random | None = None,
        now: float = 0.0,
    ) -> GuardSet:
        """Load ``path``; a missing or corrupt file means select afresh."""
        try:
            data = json.loads(path.read_text())
            guards = cls.from_json(data, path=path, self_id=self_id, config=config)
            guards.rng = rng or random.SystemRandom()
            return guards
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:  # JSONDecodeError is a ValueError
            log.warning(
                "[%s] unreadable guard file, selecting new guards: %s",
                self_id,
                type(e).__name__,
            )

        guards = cls([], path=path, self_id=self_id, config=config, rng=rng)
        guards.select(directory, now)
        guards.save()
        return guards

    @classmethod
    def from_json(
        cls,
        data: Any,
        *,
        path: Path | None,
        self_id: str,
        config: GuardConfig,
    ) -> GuardSet:
        if not isinstance(data, dict) or data.get("version") != GUARDS_VERSION:
            raise ValueError("Not a version 1 guard file")
        entries = data.get("guards")
        if not isinstance(entries, list):
            raise ValueError("Guard file has no guard list")
        guards = [Guard.from_json(entry) for entry in entries]
        if len({g.node_id for g in guards}) != len(guards):
            raise ValueError("Duplicate guard ids")
        return cls(guards, path=path, self_id=self_id, config=config)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": GUARDS_VERSION,
            "guards": [g.to_json() for g in self.guards],
        }

    def save(self) -> None:
        if self.path is not None:
            _write_private(self.path, json.dumps(self.to_json(), indent=2).encode())

    # ========================================================
    # SELECTION
    # ========================================================

    def select(self, directory: Directory, now: float) -> None:
        """Fill empty primary and backup slots with random eligible relays."""
        taken = set(self.ids)
        pool = [n for n in eligible_relays(directory, self.self_id) if n not in taken]
        wanted = (self.config.primaries - len(self.primaries)) + (
            self.config.backups - len(self.backups)
        )
        picks = self.rng.sample(pool, min(max(wanted, 0), len(pool)))

        for node_id in picks:
            role = PRIMARY if len(self.primaries) < self.config.primaries else BACKUP
            self.guards.append(Guard(node_id, now, role))

        if len(self.guards) < self.config.primaries + self.config.backups:
            log.warning(
                "[%s] only %d eligible guard relays (wanted %d)",
                self.self_id,
                len(self.guards),
                self.config.primaries + self.config.backups,
            )

    # ========================================================
    # QUERIES
    # ========================================================

    @property
    def ids(self) -> list[str]:
        return [g.node_id for g in self.guards]

    @property
    def primaries(self) -> list[str]:
        return [g.node_id for g in self.guards if g.role == PRIMARY]

    @property
    def backups(self) -> list[str]:
        return [g.node_id for g in self.guards if g.role == BACKUP]

    def get(self, node_id: str) -> Guard:
        for guard in self.guards:
            if guard.node_id == node_id:
                return guard
        raise KeyError(node_id)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.ids

    def _healthy(self, guard: Guard, exclude: frozenset[str]) -> bool:
        if guard.down or guard.node_id in exclude:
            return False
        return self.health is None or (
            guard.node_id not in self.health.down
            and guard.node_id not in self.health.suspect
        )

    def usable(self, exclude: frozenset[str] = frozenset()) -> list[str]:
        """Healthy primaries, topped up with healthy backups to ``min_usable``."""
        result = [
            g.node_id
            for g in self.guards
            if g.role == PRIMARY and self._healthy(g, exclude)
        ]
        for guard in self.guards:
            if len(result) >= self.config.min_usable:
                break
            if guard.role == BACKUP and self._healthy(guard, exclude):
                result.append(guard.node_id)
        return result

    # ========================================================
    # FAILURES (9a-2)
    # ========================================================

    def mark_failure(self, node_id: str) -> bool:
        """Count a failure through ``node_id``. True if it just went down."""
        try:
            guard = self.get(node_id)
        except KeyError:
            return False
        guard.fails += 1
        went_down = guard.fails >= self.config.fail_threshold and not guard.down
        guard.down = guard.down or went_down
        self.save()
        return went_down

    def mark_success(self, node_id: str) -> bool:
        """A delivery went through ``node_id``. True if it just came back up."""
        try:
            guard = self.get(node_id)
        except KeyError:
            return False
        if guard.fails == 0 and not guard.down:
            return False
        came_back = guard.down
        guard.fails, guard.down = 0, False
        self.save()
        return came_back

    # ========================================================
    # ROTATION (9a-2) — only at transfer start
    # ========================================================

    def refresh(self, directory: Directory, now: float) -> bool:
        """Drop invalid or expired guards and refill. True if anything changed.

        Called at transfer start only, never mid-transfer.
        """
        lifetime = self.config.lifetime_days * 86_400
        eligible = set(eligible_relays(directory, self.self_id))
        kept = [
            g
            for g in self.guards
            if g.node_id in eligible and now - g.added_at <= lifetime
        ]
        changed = len(kept) != len(self.guards)
        self.guards = kept

        # Promote backups (oldest first) into empty primary slots
        for guard in self.guards:
            if len(self.primaries) >= self.config.primaries:
                break
            if guard.role == BACKUP:
                guard.role = PRIMARY
                changed = True

        before = len(self.guards)
        self.select(directory, now)
        changed = changed or len(self.guards) != before

        if changed:
            log.info(
                "[%s] guard set refreshed: %d primaries, %d backups",
                self.self_id,
                len(self.primaries),
                len(self.backups),
            )
            self.save()
        return changed


MAX_REPLY_VIA: Final = 3


def valid_reply_via(
    ids: object, directory: Directory, self_id: str, sender: str
) -> list[str]:
    """The usable part of a manifest's ``reply_via`` (receiver side).

    Keeps at most MAX_REPLY_VIA distinct ids that exist in this node's
    directory, have the relay role, and are neither this node nor the
    sender. Anything else, including a malformed value, is ignored.
    """
    if not isinstance(ids, list | tuple):
        return []
    valid: list[str] = []
    for node_id in ids:
        if (
            isinstance(node_id, str)
            and node_id not in valid
            and node_id not in (self_id, sender)
            and node_id in directory
            and RELAY_ROLE in directory.get(node_id).roles
        ):
            valid.append(node_id)
        if len(valid) == MAX_REPLY_VIA:
            break
    return valid
