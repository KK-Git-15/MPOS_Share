from __future__ import annotations

import json
from pathlib import Path

from mpos.net.journal import JOURNAL_DIR, Journal

MANIFEST = {"transfer_id": "ab" * 16, "size": 10}


def test_roundtrip(tmp_path: Path) -> None:
    journal = Journal(tmp_path, "ab" * 16)
    journal.create(MANIFEST)
    journal.save_shard(0, 1, b"one")
    journal.save_shard(0, 3, b"three")
    journal.save_shard(2, 0, b"zero")
    journal.save_state({1}, None)

    entry = Journal.all(tmp_path)[0].load()

    assert entry.manifest == MANIFEST
    assert entry.done == {1}
    assert entry.result is None
    assert entry.pending == {0: {1: b"one", 3: b"three"}, 2: {0: b"zero"}}


def test_decoded_segment_and_finish_clean_up_shards(tmp_path: Path) -> None:
    journal = Journal(tmp_path, "cd" * 16)
    journal.create(MANIFEST)
    journal.save_shard(0, 0, b"x")
    journal.save_shard(1, 0, b"y")

    journal.drop_segment(0)
    assert journal.load().pending == {1: {0: b"y"}}

    journal.save_state({0, 1}, {"ok": True, "sha256": "f" * 64, "path": "/x"})
    entry = journal.load()
    assert entry.pending == {} and entry.result == {
        "ok": True,
        "sha256": "f" * 64,
        "path": "/x",
    }
    assert not (journal.root / "shards").exists()


def test_writes_are_atomic_and_listing_skips_junk(tmp_path: Path) -> None:
    journal = Journal(tmp_path, "ef" * 16)
    journal.create(MANIFEST)
    (tmp_path / JOURNAL_DIR / "not-a-journal").mkdir()

    assert [j.transfer_id for j in Journal.all(tmp_path)] == ["ef" * 16]
    assert not list(journal.root.rglob("*.tmp"))
    assert json.loads((journal.root / "state.json").read_text()) == {
        "done": [],
        "result": None,
    }
    assert Journal.all(tmp_path / "missing") == []
