"""Phase 8d: the experiment harness produces sound, parseable results."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from mpos.faults.scenarios import (
    RunResult,
    format_table,
    run_scenarios,
    summarize,
    write_csv,
)


@pytest.mark.timeout(120)
async def test_scenarios_run_and_summarize() -> None:
    lines: list[str] = []
    results = await run_scenarios(
        ["drop_sweep", "relay_kill", "corruption"],
        runs=1,
        seed=3,
        progress=lines.append,
        drop_rates=(0.0, 0.2),
        codes=((4, 6),),
        kills=(0, 1),
        corrupt_rates=(0.3,),
    )

    assert len(results) == len(lines) == 5
    by_scenario = {r.scenario for r in results}
    assert by_scenario == {"drop_sweep", "relay_kill", "corruption"}

    # A clean run always succeeds with zero faults
    clean = next(r for r in results if r.params == {"k": 4, "n": 6, "drop": 0.0})
    assert clean.ok and clean.intact and clean.extra["faults"] == 0

    summary = {
        (r["scenario"], r.get("drop"), r.get("killed")): r for r in summarize(results)
    }
    corruption = next(r for r in summarize(results) if r["scenario"] == "corruption")

    # Every corrupted onion was caught; no file was ever accepted wrong
    assert corruption["corrupted"] > 0
    assert corruption["detected_pct"] == 100.0
    assert all(row["wrong_files"] == 0 for row in summary.values())

    # The kill really happened while the transfer was running
    killed = summary[("relay_kill", None, 1)]
    assert killed["killed_mid_transfer"] == 1


def test_summary_handles_failures_and_writes_csv(tmp_path: Path) -> None:
    results = [
        RunResult("drop_sweep", {"drop": 0.3}, 0, 1, ok=False, elapsed=2.0, error="x"),
        RunResult("drop_sweep", {"drop": 0.3}, 1, 2, ok=True, elapsed=1.0, intact=True),
    ]
    (row,) = summarize(results)

    assert row["success_pct"] == 50.0 and row["median_s"] == 1.0
    assert "success_pct" in format_table([row])

    write_csv(tmp_path / "out" / "s.csv", [row])
    with (tmp_path / "out" / "s.csv").open() as f:
        assert next(csv.DictReader(f))["success_pct"] == "50.0"


def test_experiment_cli(tmp_path: Path) -> None:
    out = tmp_path / "results"
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "mpos",
            "experiment",
            "--scenario",
            "corruption",
            "--quick",
            "--runs",
            "1",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert done.returncode == 0, done.stderr
    assert "== corruption" in done.stdout
    with (out / "summary.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["detected_pct"] == "100.0"
    assert (out / "runs.csv").is_file()
