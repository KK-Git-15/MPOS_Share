"""On-disk receiver journal: interrupted transfers resume after a restart.

    <inbox>/.mpos/<transfer_id>/
        manifest.json            the sender's manifest (public data, no keys)
        state.json               {"done": [segments], "result": {...} | null}
        shards/<segment>/<shard> shards received but not yet decoded

A shard is journaled *before* it is acknowledged, so everything the sender
was told "received" survives a crash. Keys are never written: the file key
is re-derived from the node identity and the manifest's ephemeral key.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

JOURNAL_DIR: Final = ".mpos"


def _write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file + rename, so readers never see half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


@dataclass(slots=True)
class JournalEntry:
    manifest: dict[str, Any]
    done: set[int] = field(default_factory=set)
    result: dict[str, Any] | None = None
    pending: dict[int, dict[int, bytes]] = field(default_factory=dict)


class Journal:
    """One transfer's journal. All methods do blocking I/O: call via to_thread."""

    def __init__(self, inbox: Path, transfer_id: str) -> None:
        self.root = inbox / JOURNAL_DIR / transfer_id
        self.transfer_id = transfer_id

    @classmethod
    def all(cls, inbox: Path) -> list[Journal]:
        base = inbox / JOURNAL_DIR
        if not base.is_dir():
            return []
        return [
            cls(inbox, entry.name)
            for entry in sorted(base.iterdir())
            if (entry / "manifest.json").is_file()
        ]

    def create(self, manifest: dict[str, Any]) -> None:
        (self.root / "shards").mkdir(parents=True, exist_ok=True)
        _write_atomic(self.root / "manifest.json", json.dumps(manifest).encode())
        self.save_state(set(), None)

    def save_shard(self, segment: int, shard: int, data: bytes) -> None:
        folder = self.root / "shards" / str(segment)
        folder.mkdir(parents=True, exist_ok=True)
        _write_atomic(folder / str(shard), data)

    def drop_segment(self, segment: int) -> None:
        shutil.rmtree(self.root / "shards" / str(segment), ignore_errors=True)

    def save_state(self, done: set[int], result: dict[str, Any] | None) -> None:
        state = {"done": sorted(done), "result": result}
        _write_atomic(self.root / "state.json", json.dumps(state).encode())
        if result is not None:
            shutil.rmtree(self.root / "shards", ignore_errors=True)

    def load(self) -> JournalEntry:
        manifest = json.loads((self.root / "manifest.json").read_text())
        entry = JournalEntry(manifest)

        state_path = self.root / "state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text())
            entry.done = {int(s) for s in state.get("done", [])}
            entry.result = state.get("result")

        shards = self.root / "shards"
        if shards.is_dir():
            for seg_dir in shards.iterdir():
                if not seg_dir.name.isdigit():
                    continue
                for shard_file in seg_dir.iterdir():
                    if shard_file.name.isdigit():
                        entry.pending.setdefault(int(seg_dir.name), {})[
                            int(shard_file.name)
                        ] = shard_file.read_bytes()
        return entry
