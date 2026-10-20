"""Phase 9a entry guards: selection, persistence, usable() (CLAUDE.md §3.4.1)."""

from __future__ import annotations

import dataclasses
import json
import random
import stat
from pathlib import Path

import pytest

from mpos.crypto.keys import generate_private_key
from mpos.net.directory import Directory, NodeInfo
from mpos.net.guards import GuardConfig, GuardSet

NOW = 1_700_000_000.0


def _directory(relays: int = 7, *, receivers_only: tuple[str, ...] = ()) -> Directory:
    names = ["alice", "bob", *(f"r{i}" for i in range(relays)), *receivers_only]
    return Directory(
        NodeInfo(
            name,
            "127.0.0.1",
            9000 + i,
            generate_private_key().public_key(),
            ("receiver",) if name in receivers_only else ("relay", "receiver"),
        )
        for i, name in enumerate(names)
    )


def _create(
    tmp_path: Path,
    directory: Directory | None = None,
    *,
    seed: int = 1,
    config: GuardConfig | None = None,
) -> GuardSet:
    return GuardSet.load_or_create(
        tmp_path / "alice" / "guards.json",
        directory or _directory(),
        "alice",
        config or GuardConfig(),
        random.Random(seed),
        NOW,
    )


class FakeHealth:
    def __init__(self) -> None:
        self.down: set[str] = set()
        self.suspect: set[str] = set()


# ============================================================
# CONFIG
# ============================================================


def test_config_defaults_match_spec() -> None:
    config = GuardConfig()

    assert (config.enabled, config.primaries, config.backups) == (True, 3, 2)
    assert (config.lifetime_days, config.fail_threshold, config.min_usable) == (
        30,
        3,
        2,
    )
    assert config.reply_via_guards


@pytest.mark.parametrize(
    "kwargs",
    [
        {"primaries": 0},
        {"backups": -1},
        {"lifetime_days": 0},
        {"fail_threshold": 0},
        {"min_usable": 0},
    ],
)
def test_config_validation(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        GuardConfig(**kwargs)


# ============================================================
# SELECTION
# ============================================================


def test_selection_excludes_self_and_non_relays(tmp_path: Path) -> None:
    directory = _directory(relays=6, receivers_only=("carol",))
    guards = _create(tmp_path, directory)

    ids = guards.ids
    assert len(ids) == 5 and len(set(ids)) == 5
    assert "alice" not in ids and "carol" not in ids
    assert len(guards.primaries) == 3 and len(guards.backups) == 2
    assert all(g.added_at == NOW for g in guards.guards)


def test_selection_is_deterministic_with_a_seeded_rng(tmp_path: Path) -> None:
    directory = _directory()
    a = _create(tmp_path / "a", directory, seed=5)
    b = _create(tmp_path / "b", directory, seed=5)
    c = _create(tmp_path / "c", directory, seed=6)

    assert a.ids == b.ids
    assert a.ids != c.ids


def test_small_network_takes_what_exists(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    guards = _create(tmp_path, _directory(relays=2))  # bob, r0, r1 are relays

    assert sorted(guards.ids) == ["bob", "r0", "r1"]
    assert len(guards.primaries) == 3 and guards.backups == []
    assert "guard" in caplog.text.lower()


# ============================================================
# PERSISTENCE
# ============================================================


def test_persistence_round_trip_and_owner_only_mode(tmp_path: Path) -> None:
    first = _create(tmp_path, seed=1)
    path = tmp_path / "alice" / "guards.json"

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert {g["role"] for g in data["guards"]} == {"primary", "backup"}
    assert set(data["guards"][0]) == {"id", "added_at", "role", "fails", "down"}

    # A different seed must not matter: the stored set is reused
    again = _create(tmp_path, seed=99)
    assert again.ids == first.ids


@pytest.mark.parametrize(
    "content",
    [b"", b"{not json", b'{"version": 2, "guards": []}', b'{"version": 1}', b"[]"],
)
def test_corrupt_file_is_regenerated(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "alice" / "guards.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)

    guards = _create(tmp_path)

    assert len(guards.ids) == 5
    assert json.loads(path.read_text())["version"] == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_from_json_rejects_bad_entries() -> None:
    bad = {"version": 1, "guards": [{"id": "r1", "role": "boss", "added_at": 1}]}
    with pytest.raises(ValueError):
        GuardSet.from_json(bad, path=None, self_id="alice", config=GuardConfig())


# ============================================================
# USABLE
# ============================================================


def test_usable_prefers_healthy_primaries(tmp_path: Path) -> None:
    guards = _create(tmp_path)
    p0, p1, p2 = guards.primaries
    b0, b1 = guards.backups

    assert guards.usable() == [p0, p1, p2]

    guards.get(p0).down = True
    assert guards.usable() == [p1, p2]  # still >= min_usable, no backup needed

    guards.get(p1).down = True
    assert guards.usable() == [p2, b0]  # topped up to min_usable

    guards.get(p2).down = True
    assert guards.usable() == [b0, b1]

    guards.get(p0).down = False  # a recovered primary is preferred again
    assert guards.usable() == [p0, b0]


def test_usable_honours_exclude(tmp_path: Path) -> None:
    guards = _create(tmp_path)
    p0, p1, p2 = guards.primaries
    b0, _ = guards.backups

    assert guards.usable(exclude=frozenset({p1})) == [p0, p2]
    assert guards.usable(exclude=frozenset({p0, p1})) == [p2, b0]


def test_usable_skips_guards_that_health_reports_down_or_suspect(
    tmp_path: Path,
) -> None:
    guards = _create(tmp_path)
    p0, p1, p2 = guards.primaries
    b0, _ = guards.backups
    health = FakeHealth()
    guards.health = health

    health.down.add(p0)
    health.suspect.add(p1)  # e.g. a blackhole: answers pings, loses probes

    assert guards.usable() == [p2, b0]

    health.suspect.clear()
    assert guards.usable() == [p1, p2]


def test_usable_can_be_empty(tmp_path: Path) -> None:
    guards = _create(tmp_path)
    for guard in guards.guards:
        guard.down = True

    assert guards.usable() == []


# ============================================================
# FAILURE TRACKING (9a-2)
# ============================================================


def test_consecutive_failures_mark_a_guard_down(tmp_path: Path) -> None:
    guards = _create(tmp_path)
    p0 = guards.primaries[0]

    assert guards.mark_failure(p0) is False
    assert guards.mark_failure(p0) is False
    assert guards.mark_failure(p0) is True  # third strike: state changed
    assert guards.get(p0).down and p0 not in guards.usable()

    # Persisted: a fresh load sees it down
    reloaded = _create(tmp_path, seed=42)
    assert reloaded.get(p0).down and reloaded.get(p0).fails == 3


def test_success_resets_failures_and_recovers(tmp_path: Path) -> None:
    guards = _create(tmp_path)
    p0 = guards.primaries[0]

    guards.mark_failure(p0)
    guards.mark_failure(p0)
    assert guards.mark_success(p0) is False  # was not down: no state change
    guards.mark_failure(p0)
    guards.mark_failure(p0)
    assert not guards.get(p0).down  # the success reset the count

    guards.mark_failure(p0)
    assert guards.get(p0).down
    assert guards.mark_success(p0) is True
    assert not guards.get(p0).down and guards.get(p0).fails == 0
    assert guards.usable()[0] == p0


def test_marks_for_non_guards_are_ignored(tmp_path: Path) -> None:
    guards = _create(tmp_path)
    before = guards.to_json()

    assert guards.mark_failure("not-a-guard") is False
    assert guards.mark_success("not-a-guard") is False
    assert guards.to_json() == before


def test_failures_never_rotate_guards(tmp_path: Path) -> None:
    """Membership changes only in refresh(), never mid-transfer."""
    guards = _create(tmp_path)
    ids = guards.ids
    for node_id in ids:
        for _ in range(5):
            guards.mark_failure(node_id)

    assert guards.ids == ids and guards.usable() == []


# ============================================================
# ROTATION (9a-2)
# ============================================================

DAY = 86_400.0


def test_refresh_without_reason_changes_nothing(tmp_path: Path) -> None:
    directory = _directory()
    guards = _create(tmp_path, directory)
    path = tmp_path / "alice" / "guards.json"
    stamp = path.stat().st_mtime_ns

    assert guards.refresh(directory, NOW + 29 * DAY) is False
    assert path.stat().st_mtime_ns == stamp


def test_expired_primary_is_replaced_by_a_backup(tmp_path: Path) -> None:
    directory = _directory()
    guards = _create(tmp_path, directory)
    p0, p1, p2 = guards.primaries
    b0, b1 = guards.backups
    guards.get(p0).added_at = NOW - 31 * DAY  # expired

    assert guards.refresh(directory, NOW) is True

    assert p0 not in guards
    assert guards.primaries == [p1, p2, b0]  # backup promoted
    assert len(guards.backups) == 2 and guards.backups[0] == b1
    new_backup = guards.backups[1]
    assert new_backup not in (p0, p1, p2, b0, b1, "alice")
    assert guards.get(new_backup).added_at == NOW
    assert _create(tmp_path, seed=7).ids == guards.ids  # persisted


def test_guard_removed_from_directory_or_role_is_replaced(tmp_path: Path) -> None:
    directory = _directory()
    guards = _create(tmp_path, directory)
    p0, p1, _ = guards.primaries

    directory.remove(p0)
    info = directory.get(p1)
    directory.add(NodeInfo(p1, info.host, info.port, info.public_key, ("receiver",)))

    assert guards.refresh(directory, NOW + DAY) is True
    assert p0 not in guards and p1 not in guards
    assert len(guards.primaries) == 3 and len(guards.backups) == 2
    assert all(RELAY in directory.get(g).roles for g in guards.ids)


def test_refresh_on_a_shrinking_network_keeps_what_it_can(tmp_path: Path) -> None:
    directory = _directory(relays=7)
    guards = _create(tmp_path, directory)
    for node_id in list(guards.ids)[2:]:
        directory.remove(node_id)
    for name in [n.node_id for n in directory if n.node_id not in guards.ids]:
        if name not in ("alice",):
            directory.remove(name)

    guards.refresh(directory, NOW)

    assert len(guards.ids) == 2
    assert len(guards.primaries) == 2 and guards.backups == []


RELAY = "relay"


# ============================================================
# PATH CHOICE (9a-3)
# ============================================================


def test_choose_path_property_first_hop_is_always_a_usable_guard(
    tmp_path: Path,
) -> None:

    from mpos.net.paths import choose_path

    directory = _directory(relays=9)
    guards = _create(tmp_path, directory)
    rng = random.Random(3)
    fallbacks: list[str] = []

    for call in range(1000):
        if call % 100 == 0:  # vary health so failover is exercised too
            for g in guards.guards:
                g.down = rng.random() < 0.3
        usable = guards.usable(frozenset({"alice", "bob"}))
        path = choose_path(
            directory,
            hops=3,
            exclude={"alice", "bob"},
            rng=rng,
            guards=guards,
            on_fallback=fallbacks.append,
        )
        assert len(set(path)) == 3
        assert not {"alice", "bob"} & set(path)
        if usable:
            assert path[0] in usable
    assert fallbacks == [] or all(isinstance(f, str) for f in fallbacks)


def test_one_segments_shards_spread_evenly_over_primaries(tmp_path: Path) -> None:
    from collections import Counter

    from mpos.net.paths import choose_path

    directory = _directory(relays=9)
    guards = _create(tmp_path, directory)
    rng = random.Random(8)

    for n in (3, 6, 8, 12):
        usage: Counter[str] = Counter()
        first_hops: Counter[str] = Counter()
        for _ in range(n):
            choose_path(
                directory,
                hops=3,
                exclude={"alice", "bob"},
                rng=rng,
                usage=usage,
                guards=guards,
                first_hops=first_hops,
            )
        counts = [first_hops[g] for g in guards.primaries]
        assert sum(counts) == n
        assert max(counts) - min(counts) <= 1


def test_non_guards_preferred_for_later_hops(tmp_path: Path) -> None:
    from mpos.net.paths import choose_path

    directory = _directory(relays=9)  # 10 relays incl. bob; 5 guards
    guards = _create(tmp_path, directory)
    rng = random.Random(2)

    for _ in range(200):
        path = choose_path(
            directory, hops=3, exclude={"alice", "bob"}, rng=rng, guards=guards
        )
        assert path[0] in guards
        assert not set(path[1:]) & set(guards.ids)  # enough non-guards exist


def test_small_network_still_routes_through_guards_in_later_hops(
    tmp_path: Path,
) -> None:
    """Guards are never banned from hops 2-3: tiny networks keep working."""
    from mpos.net.paths import choose_path

    directory = _directory(relays=3)  # bob + r0..r2: 3 relays besides alice
    guards = _create(tmp_path, directory)

    path = choose_path(
        directory, hops=3, exclude={"alice"}, rng=random.Random(1), guards=guards
    )
    assert len(set(path)) == 3 and path[0] in guards


def test_all_guards_down_falls_back_and_reports(tmp_path: Path) -> None:
    from mpos.net.paths import choose_path

    directory = _directory(relays=9)
    guards = _create(tmp_path, directory)
    for g in guards.guards:
        g.down = True
    reasons: list[str] = []

    path = choose_path(
        directory,
        hops=3,
        exclude={"alice", "bob"},
        rng=random.Random(1),
        guards=guards,
        on_fallback=reasons.append,
    )

    assert len(set(path)) == 3 and not {"alice", "bob"} & set(path)
    assert reasons and "usable" in reasons[0]


def test_choose_path_without_guards_is_unchanged() -> None:
    from mpos.net.paths import choose_path

    directory = _directory(relays=9)

    def paths(**extra: object) -> list[list[str]]:
        rng = random.Random(4)
        return [
            choose_path(directory, hops=3, exclude={"alice"}, rng=rng, **extra)  # type: ignore[arg-type]
            for _ in range(20)
        ]

    assert paths() == paths(guards=None)


# ============================================================
# REPLY_VIA (9a-4)
# ============================================================


def test_reply_via_validation() -> None:
    from mpos.net.guards import valid_reply_via

    directory = _directory(relays=5, receivers_only=("carol",))
    check = lambda ids: valid_reply_via(ids, directory, "bob", "alice")  # noqa: E731

    assert check(["r0", "r1"]) == ["r0", "r1"]
    assert check(["ghost", "r0"]) == ["r0"]  # unknown ids ignored
    assert check(["bob", "alice", "r2"]) == ["r2"]  # never itself or the sender
    assert check(["carol", "r3"]) == ["r3"]  # must have the relay role
    assert check(["r0", "r0", "r1"]) == ["r0", "r1"]  # duplicates dropped
    assert check(["r0", "r1", "r2", "r3", "r4"]) == ["r0", "r1", "r2"]  # at most 3
    for garbage in (None, "r0", 5, [1, None], {"r0": 1}):
        assert check(garbage) == []


def test_manifest_carries_reply_via_only_when_set() -> None:
    import uuid

    from mpos.net.messages import Manifest

    base = Manifest(
        transfer_id=uuid.uuid4().hex,
        sender="alice",
        filename="f",
        size=10,
        sha256="ab" * 32,
        segment_size=4096,
        k=4,
        n=6,
        ephemeral_key="AAAA",
    )
    assert "reply_via" not in base.to_header()  # wire unchanged by default
    assert Manifest.from_header(base.to_header()).reply_via == ()

    with_guards = dataclasses.replace(base, reply_via=("r1", "r2"))
    header = with_guards.to_header()
    assert header["reply_via"] == ["r1", "r2"]
    assert Manifest.from_header(header) == with_guards

    header["reply_via"] = "not-a-list"  # an unexpected type is just ignored
    assert Manifest.from_header(header).reply_via == ()
