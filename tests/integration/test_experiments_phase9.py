"""Phase 9 report experiments (CLAUDE.md §3.4.3): E1–E4 are sound and opt-in."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from mpos.faults.scenarios import (
    ALL_SCENARIOS,
    SCENARIOS,
    run_scenarios,
    summarize,
    write_traces,
)

PHASE9 = ("guard_exposure", "window_vs_latency", "slow_relay", "cwnd_trace")


def test_phase9_scenarios_are_registered_but_not_in_all() -> None:
    assert set(PHASE9) <= set(SCENARIOS)
    assert not set(PHASE9) & set(ALL_SCENARIOS)


# ============================================================
# E1: guard exposure (no cluster, the real choose_path)
# ============================================================


async def test_guard_exposure_guards_cut_exposure() -> None:
    results = await run_scenarios(
        ["guard_exposure"],
        runs=2,
        seed=5,
        progress=lambda _line: None,
        relay_counts=(20,),
        fractions=(0.1,),
        segment_counts=(50,),
        trials=40,
    )
    rows = {r["guards"]: r for r in summarize(results)}

    assert set(rows) == {0, 1}
    assert all(r["trials"] == 80 for r in rows.values())
    # 300 paths without guards: the 2 adversary relays are almost surely first
    assert rows[0]["first_hop_pct"] > 95
    # With guards it is one draw of 5 guards out of 20, not 300 draws
    assert rows[1]["first_hop_pct"] < rows[0]["first_hop_pct"] - 30
    assert rows[1]["both_ends_pct"] <= rows[0]["both_ends_pct"]


async def test_guard_exposure_is_reproducible() -> None:
    kwargs = {
        "relay_counts": (10,),
        "fractions": (0.2,),
        "segment_counts": (5,),
        "trials": 20,
    }
    first = await run_scenarios(["guard_exposure"], 1, 9, lambda _: None, **kwargs)
    again = await run_scenarios(["guard_exposure"], 1, 9, lambda _: None, **kwargs)
    assert [r.extra for r in first] == [r.extra for r in again]


# ============================================================
# E2–E4: cluster runs, fixed vs rtt
# ============================================================


@pytest.mark.timeout(240)
async def test_window_slow_relay_and_trace_scenarios(tmp_path: Path) -> None:
    results = await run_scenarios(
        ["window_vs_latency", "slow_relay", "cwnd_trace"],
        runs=1,
        seed=2,
        progress=lambda _: None,
        delays_ms=(25,),
        faults=("blackhole",),
        trace_delays_ms=(25,),
        file_mb=8,  # > the fixed window's 4 MiB, so the fault lands mid-transfer
    )
    assert all(r.ok and r.intact for r in results), [r.error for r in results]

    latency = [r for r in results if r.scenario == "window_vs_latency"]
    assert {r.params["window"] for r in latency} == {"fixed", "rtt"}
    for r in latency:
        assert {"duplicate_shards", "bytes"} <= set(r.extra)
    rtt = next(r for r in latency if r.params["window"] == "rtt")
    assert rtt.extra["peak_cwnd"] >= 6

    slow = [r for r in results if r.scenario == "slow_relay"]
    assert {r.params["window"] for r in slow} == {"fixed", "rtt"}
    # A blackhole always forces a retransmit; the rtt timeout notices first
    retry = {r.params["window"]: r.extra["first_retry_ms"] for r in slow}
    assert retry["rtt"] < retry["fixed"]

    rows = {(r["scenario"], r.get("window")): r for r in summarize(results)}
    assert rows[("window_vs_latency", "rtt")]["goodput_mbps"] > 0

    (trace,) = [r for r in results if r.scenario == "cwnd_trace"]
    assert trace.trace and trace.trace[0]["t_s"] == "0.000"
    written = write_traces(tmp_path, results)
    assert len(written) == 1
    with written[0].open() as f:
        assert list(next(csv.reader(f)))[:2] == ["t_s", "cwnd"]


def test_experiment_cli_guard_exposure(tmp_path: Path) -> None:
    out = tmp_path / "results"
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "mpos",
            "experiment",
            "--scenario",
            "guard_exposure",
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
    assert "== guard_exposure" in done.stdout
    with (out / "summary.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert {"first_hop_pct", "both_ends_pct"} <= set(rows[0])
