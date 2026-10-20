"""CSV view of a transfer's RTT-window trace (Phase 9b, CLAUDE.md §3.4.2 item 10).

Shared by ``mpos send --trace-cwnd`` and the ``cwnd_trace`` experiment.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

from mpos.events import CwndChanged

CWND_COLUMNS: Final = (
    "t_s",
    "cwnd",
    "inflight",
    "srtt_ms",
    "base_rtt_ms",
    "queue_est",
    "rto_ms",
    "mode",
)


def cwnd_rows(events: Iterable[CwndChanged]) -> list[dict[str, str]]:
    """One row per event, times relative to the first event."""
    events = list(events)
    start = events[0].ts if events else 0.0
    return [
        {
            "t_s": f"{e.ts - start:.3f}",
            "cwnd": f"{e.cwnd:.2f}",
            "inflight": str(e.inflight),
            "srtt_ms": "" if e.srtt_ms is None else f"{e.srtt_ms:.1f}",
            "base_rtt_ms": "" if e.base_rtt_ms is None else f"{e.base_rtt_ms:.1f}",
            "queue_est": f"{e.queue_est:.2f}",
            "rto_ms": f"{e.rto_ms:.0f}",
            "mode": e.mode,
        }
        for e in events
    ]


def write_cwnd_csv(path: Path, rows: Sequence[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CWND_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
