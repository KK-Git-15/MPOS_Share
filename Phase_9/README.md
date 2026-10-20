# Phase 9 — Tor-inspired hardening

Spec: CLAUDE.md §3.4.
- **9a, entry guards: done**, and on by default.
- **9b, RTT-based window: done**, and on by default (`--window fixed` restores the old window).
- Next: the report experiments (CLAUDE.md §3.4.3).

## 9a — Entry guards

**The problem.** Without guards, every shard picks a random first relay, and that relay sees the sender's address. A 10 MB file is 60 shards, so 60 independent chances for a malicious relay to be first. Across many transfers it's almost certain to happen eventually.

**What guards do.** Each node picks **3 primary and 2 backup guards** once, saves them, and uses only those as first hops, for every transfer, for 30 days. Exposure becomes one draw instead of one per shard. Each segment's shards are still spread over the 3 primaries, so no single relay sees every shard.

| Behaviour | Detail |
|---|---|
| **Failover** | A guard that refuses 3 connections in a row, or that Phase 8 health marks Down or Suspect (dead, or a blackhole), stops being used. Backups fill in until at least 2 guards are usable. A recovered primary is preferred again immediately. |
| **Never stalls** | If no guard is usable, the path is chosen the old way. A warning is logged, a `GuardFallback` event fires, and the transfer still completes. |
| **Rotation** | Only at transfer start: guards older than 30 days, removed from `nodes.json`, or no longer relays are replaced, with backups promoted first. |
| **Return paths** | The manifest carries `reply_via` (up to 3 of the sender's guards). The receiver ends every ACK and COMPLETE route on one of them, so the sender only ever hears from its own guards. This tells the receiver who the sender's guards are; turn it off with `GuardConfig(reply_via_guards=False)`. |
| **Storage** | `data/<node>/guards.json`, mode 0600, written atomically. A missing or corrupt file is regenerated, never a crash. Guard ids are never logged. |

**Use it**
```bash
mpos send ... --file f.bin --to bob             # guards on (default)
mpos send ... --no-guards                        # control run: random first hops
mpos send ... --guards 2                         # 2 primaries instead of 3
mpos guards list  --id alice --directory nodes.json
mpos guards reset --id alice --directory nodes.json
```
In the GUI:
- The Nodes page shows a **Guard** column: **P** (primary) or **B** (backup), and red ✗ when down.
- The routing map rings guards in teal: solid for primaries, dotted for backups.
- The Logs page shows guard changes, as counts only.

**Acceptance (CLAUDE.md §3.4.1), all met**

| Criterion | Test |
|---|---|
| With guards on, 100% of shard paths start at a guard (apart from logged fallbacks) | `test_every_first_hop_is_a_guard`, plus a 1000-call property test |
| Killing a guard before or during a transfer still completes it with the correct sha256 | `test_guard_killed_before_sending_is_covered`, `test_guard_killed_mid_transfer` |
| A blackholed (Suspect) guard is skipped and the transfer completes | `test_blackholed_guard_becomes_suspect_and_is_skipped` |
| The guard set is identical across transfers and across a restart | `test_guard_set_is_stable_across_transfers_and_restart` |
| `guards.json` has mode 0600, and no log line reveals guard status | `test_every_first_hop_is_a_guard`, `test_no_log_line_reveals_guard_status` (see D11) |
| Existing suite green with the new default | 390 tests passed, 3 runs |

**Limits (for the report)**
- **Small networks:** on a 5-relay network with 3 guards, most of the network is a guard, so the benefit is small. It grows with 10 or more relays.
- **The guard still sees you:** guards don't hide the sender from the guard itself, and don't stop a global observer that watches both ends.
- **`nodes.json` is unsigned:** whoever can edit it can steer your choice of guards.
- **A persistent fingerprint:** a guard set doesn't change for weeks, so any relay watching you across transfers can recognise it.

## 9b — RTT-based window

**The problem.** The old sender kept a fixed 4 segments (24 shards) in flight and waited a fixed 10 s for each ACK. On a slow path, 24 shards can't fill the pipe. On a fast one, a lost shard is noticed only after 10 s.

**What 9b does** (Tor 0.4.7's Tor-Vegas, itself based on TCP Vegas). Loss can't be the congestion signal here, because every hop is TCP and a missing ACK can just as well be a dead relay. So the window reacts to **delay**:
- **Window:** counted in **shards**. It starts at N (6) and ranges from 2 to 16 × N.
- **Queue estimate:** every round (about one RTT), the sender estimates how many of its shards sit in queues: `queued = cwnd × (1 − base_rtt / rtt)`.
- **Slow start:** +1 per ACK, until `queued > 4`.
- **Steady state:** grow by 1 if `queued < 2`, shrink by 1 if `queued > 4`.
- **Baseline:** the RTT baseline is kept **per first-hop relay**, so different path lengths don't look like congestion.
- **Timeouts:** each shard's timeout is an **adaptive RTO** (RFC 6298: srtt + 4·rttvar, 1 s to 10 s), not a fixed 10 s. A shard that is re-sent backs off its own deadline.
- **Bursts:** only a burst of timeouts across several relays halves the window. One bad relay is handled by path penalties and guards instead.
- **Memory:** segments are read and encoded only when the window needs them, capped at `max_inflight_bytes` (64 MiB).

**Use it**
```bash
mpos send ... --window rtt                          # the default
mpos send ... --window fixed                        # the old behaviour
mpos send ... --window rtt --trace-cwnd results/cwnd.csv   # t_s,cwnd,inflight,srtt_ms,base_rtt_ms,queue_est,rto_ms,mode
```
In the GUI, the Send / Receive page has a **window** selector, and a live **cwnd / srtt plot** next to the shard grid.

**Acceptance (9b)** — all ticked, so `window_mode` now defaults to `"rtt"`.

| Item | Evidence |
|---|---|
| sha256 matches in every scenario, in both window modes | every test in `tests/integration/test_window_rtt.py` checks the receiver-confirmed sha256; `test_both_modes_deliver_the_same_bytes` |
| +50 ms global delay, 50 MB: `rtt` goodput ≥ `fixed` | measured (default configs, 7-node loopback): **fixed 6.3 s, 7.96 MB/s**; **rtt 4.1 s, 12.12 MB/s (+52 %)**; both 0 retries and 0 duplicates. CI runs a 2 MB version: `test_window_grows_on_a_high_latency_path` |
| Slow relay (+500 ms for a few seconds mid-transfer): `duplicate_shards` rtt ≤ fixed | `test_slow_relay_mid_transfer_no_extra_spurious_retransmits`; heterogeneous relays (+10/+60/+120 ms), retries rtt ≤ fixed: `test_heterogeneous_relays_rtt_no_worse_than_fixed` |
| Blackholed relay times out in < 10 s (health probes off) | `test_blackhole_detected_by_rtt_timeout_well_before_fixed`: first timeouts have a median of about 1 s, and none reach 10 s; `test_blackhole_with_default_health` repeats it with default health |
| `--trace-cwnd` plots a growing then stable window | `test_trace_cwnd_csv_from_the_cli`. In the 50 MB run, slow start goes 6 → 8 → 11 → 17 → 28 → 36 → 60 → 91 shards, one step per RTT (srtt ≈ 440 ms), then the window holds at the cap of 96 (16 × N) in avoidance until the file ends |
| Existing suite green with the new default; ruff and mypy --strict clean | see the commit for 9b-7 |

**Limits (for the report)**
- **It doesn't remove retries:** the window adapts to delay, but lost shards are still found by timeouts and re-sent on new paths.
- **Loopback numbers:** every relay shares one machine and one CPU, so the absolute goodput says little about a real network. Only `rtt` against `fixed` in the same run is meaningful.
- **The cap matters:** at +50 ms the window hits `max_window_segments × N` (96 shards) before queues build. A larger cap would go faster on long fat paths but holds more encoded segments in memory (still bounded by `max_inflight_bytes`).
- **The baseline needs samples:** with fewer than 3 samples on a first hop, the global 10th percentile stands in, so the first round on a new guard is estimated less precisely.

## Report experiments (§3.4.3)

All four are **opt-in**: `--scenario all` still runs only the Phase 8 scenarios.
```bash
mpos experiment --scenario guard_exposure    --runs 20 --seed 1   # E1, no cluster, about a minute
mpos experiment --scenario window_vs_latency --runs 3  --seed 1   # E2, 50 MB per run
mpos experiment --scenario slow_relay        --runs 5  --seed 1   # E3, 16 MB per run
mpos experiment --scenario cwnd_trace        --runs 1  --seed 1   # E4, writes traces/*.csv
# add --quick for a fast look (fewer settings, 2 MB files)
```
Each writes `runs.csv` and `summary.csv` to `results/<scenario>-<time>/`; E4 also writes one cwnd CSV per run to `traces/` (same columns as `--trace-cwnd`).

| | What it measures | Columns in `summary.csv` |
|---|---|---|
| **E1 `guard_exposure`** | Monte Carlo over the real `choose_path` and `GuardSet` on a synthetic directory: R ∈ {5, 10, 20} relays, adversary fraction f ∈ {0.1, 0.2, 0.3} (at least 1 relay), transfers of S ∈ {1, 10, 100} segments of N = 6 shards, guards on and off. Each trial is a fresh sender (new guards) and a fresh adversary set | `first_hop_pct`: P(adversary is the first hop of ≥ 1 shard); `both_ends_pct`: P(≥ 1 path has an adversary as both first and last relay) |
| **E2 `window_vs_latency`** | `fixed` vs `rtt`, every link delayed by {0, 25, 50, 100, 200} ms, 50 MB file | `goodput_mbps`, `median_s`, `mean_retries`, `mean_duplicates`, `median_peak_cwnd`, `median_srtt_ms` |
| **E3 `slow_relay`** | A seeded relay turns **slow** (+500 ms for 2 s) or **blackholed** once 4 shards are ACKed, on a +20 ms network, 16 MB file, `fixed` vs `rtt` | `median_first_retry_ms` (fault → the sender's first retransmit, i.e. how fast it notices), `retried_runs`, `median_s`, `mean_duplicates` |
| **E4 `cwnd_trace`** | cwnd / srtt over time for `rtt` transfers at {0, 50, 100} ms, 50 MB file (20 MB never left slow start at +50 ms) | `traces/cwnd_trace-delay_ms<d>-run<n>.csv` for the figure |

### Results (seed 1, 7-node loopback cluster on one laptop)

Raw CSVs: `results/phase9/e1..e4/` (git-ignored; rerun with the commands above). Every cluster run delivered a correct file: 100 % success, 100 % intact, 0 wrong files.

**E1: guard exposure** (500 simulated senders per row, S = 100 segments)

| Relays | Adversary | First hop, no guards | First hop, guards | Both ends, no guards | Both ends, guards |
|---|---|---|---|---|---|
| 20 | 10 % (2) | 100 % | 29 % | 97 % | 26 % |
| 20 | 20 % (4) | 100 % | 53 % | 100 % | 53 % |
| 20 | 30 % (6) | 100 % | 68 % | 100 % | 68 % |
| 10 | 20 % (2) | 100 % | 54 % | 100 % | 35 % |
| 5 | 30 % (2) | 100 % | 91 % | 100 % | 91 % |

- **Without guards**, an adversary is almost certainly some shard's first hop once a transfer has 10 or more segments.
- **With guards**, exposure is one draw of 3 primaries, whatever the length. It matches the closed form `1 − C(R−a, 3) / C(R, 3)`: 28.4 % expected for R = 20, a = 2, against 29 % measured.
- **Small networks:** on 5 relays, 3 of the 5 are guards, so guards barely help, as the limits above say. Rows with a single adversary relay show 0 % "both ends" either way, because one relay can't be both first and last on a path.
- **Caveat:** guards don't change *whether* an adversary guard sees you; they stop the probability from compounding with every segment and every transfer.

**E2: window vs latency** (50 MB, 3 runs each, medians)

| Added delay per link | fixed | rtt | rtt peak cwnd | srtt | rtt retries |
|---|---|---|---|---|---|
| 0 ms | 31.1 MB/s | 31.1 MB/s | 10 | 58 ms | 0 |
| 25 ms | 15.7 MB/s | 20.0 MB/s (+27 %) | 59 | 249 ms | 0 |
| 50 ms | 8.3 MB/s | 12.8 MB/s (+54 %) | 90 | 425 ms | 0 |
| 100 ms | 4.3 MB/s | 6.7 MB/s (+55 %) | 96 (cap) | 852 ms | 0 |
| 200 ms | 2.2 MB/s | 3.1 MB/s (+39 %) | 88 | 1671 ms | 36 |

- With no added delay the two modes are equal: the fixed window of 24 shards is already enough to fill the pipe.
- With delay, `rtt` is 27–55 % faster, and `fixed` never retries.
- Duplicate shards were 0 in every run, in both modes.
- **The 36 retries at +200 ms are spurious timeouts.** The added delay is constant, so `rttvar` shrinks towards 0 and `rto` settles about 2 % above `srtt` (1.70 s against 1.66 s). When slow start grew the window from 46 to 59 shards, RTTs rose past that margin, and about 46 shards timed out at the same instant. The controller read that as a burst and halved the window. No retransmitted copy reached the receiver: the original ACKs arrived moments later and the segments completed. But the halving cost throughput. A floor on the variance term (as Linux TCP has) would prevent this; it isn't implemented.

**E3: slow or blackholed relay** (16 MB on a +20 ms network, 5 runs each, medians)

| Fault | Mode | Time | Fault → first retransmit | Retries | Duplicates (mean) |
|---|---|---|---|---|---|
| blackhole | fixed | 11.6 s | 10.5 s | 18.6 | 0.8 |
| blackhole | rtt | 4.4 s | 1.0 s | 17.8 | 3.0 |
| slow (+500 ms, 2 s) | fixed | 2.5 s | none (0 of 5 runs) | 0 | 0 |
| slow (+500 ms, 2 s) | rtt | 2.8 s | 1.0 s (1 of 5 runs) | 0.4 | 0 |

- **Blackhole:** `rtt` notices it 10× sooner and finishes 2.6× faster. The cost is a few more spurious retransmits (3.0 against 0.8 duplicate shards per 16 MB): the timeout is short (min 1 s), so a shard that is only slow is sometimes re-sent too.
- **Slow spell:** both modes ride it out with no duplicates. `rtt` is about 10 % slower here. It started at cwnd 6 and spent part of the short transfer in slow start, while `fixed` puts 24 shards on the wire at once.

**E4: cwnd traces** (50 MB, `traces/cwnd_trace-delay_ms*.csv`)

| Added delay | Slow start | Then |
|---|---|---|
| 0 ms | 6 → 11 | Vegas sees queueing and holds at 11 (srtt 58 ms) |
| +50 ms | 6 → 8 → 11 → 18 → 30 → 47 → 76 → 79, one step per RTT (about 0.43 s) | holds at 79, below the cap |
| +100 ms | 6 → 8 → 11 → 18 → 30 → 41 → 66 → 95 | holds at the cap of 96 |

**What this does and doesn't show**
- The RTT window wins on long, high-latency transfers and detects a dead relay much sooner.
- It does **not** help short transfers: a 4–16 MB file is about as fast as with `fixed`, or slower, because slow start begins at N = 6 shards. The same goes for zero-latency paths, where both modes are equal.
- It doesn't remove retries. On very stable, long paths, the tight RFC 6298 timeout can fire spuriously (E2 at +200 ms).
- All numbers come from one machine. Only `rtt` against `fixed` in the same experiment is meaningful, not the absolute MB/s.

## Deviations from the spec (CLAUDE.md §3.4)

As §3.4.0 asks, wherever the spec and the real code differ, the code wins and the difference is recorded here.

| # | Spec | Real code / decision |
|---|---|---|
| D1 | "existing test suite (210 tests)" | The suite had **339 tests** when Phase 9 started. All of them must stay green. |
| D2 | 9b: the old fixed ACK timeout is 20 s | `TransferConfig.ack_timeout` defaults to **10 s**. 9b compares against 10 s, and `max_rto` defaults to `ack_timeout`. |
| D3 | 9b-3: check whether the receiver ACKs only after decoding K shards | It **already ACKs each valid shard on arrival**, right after the Phase 8 journal write. 9b-3 only adds a regression test and `duplicate_shards`. |
| D4 | `tests/helpers/fake_clock.py` | `tests/helpers.py` was a single module. It becomes a package in 9b-1, keeping `free_port_block`. |
| D5 | `GuardSet` reads health "Up / Suspect / Down" | `HealthMonitor` exposes `down` and `suspect` sets. `GuardSet.health` takes any object with those two properties (`HealthView` protocol). |
| D6 | Never log guard ids on relay nodes | Every node can be a relay, so guard ids are **never logged at all**, on any node. Logs only ever show counts. |
| D7 | First hop ranked by "usage in this segment" | Usage counts every position, and on small networks guards also appear as hop 2 or 3, which skewed the spread. `choose_path` takes a separate `first_hops` counter that ranks guards only by how often they were the *first* hop. One segment's N shards then spread with max−min ≤ 1 (property test). |
| D8 | `GuardFallback` event when no guard is usable | `choose_path` is pure, so it reports through an `on_fallback(reason)` callback. `TransferService` counts `stats.guard_fallbacks` and logs a warning; the event arrives in 9a-5. |
| D9 | `mark_failure` / `mark_success` | Called by the *sender* on a refused first hop and on a single-path ACK. ACKs over 2 parallel control paths can't say which path worked, so they don't count as a success. The guard file is rewritten on the event loop (a few hundred bytes), never from a thread, because `GuardSet` is single-loop. |
| D10 | Events `GuardSelected(node_id, role)`, `GuardStateChanged(node_id, down)`, `GuardFallback(reason)` | Each event also has `owner` (whose guards they are; several nodes may send), and `GuardSelected` has an `epoch`, so the GUI can replace an owner's set when guards rotate. They're published only on the owner's own bus, never by relays. |
| D11 | "No guard ids appear in any relay log" | Relay logs name their neighbours ("relayed alice -> r1"), and neighbours may happen to be guards. That doesn't reveal guard *status*. The rule enforced and tested is: **no log line about guards contains a guard id** (logs give counts only). |
| D12 | A round ends when the shard sent right after it began is ACKed, and its ratios decide the window | Each shard is tagged with the round it was **sent** in, and only same-round samples count toward a round's `queued` estimate. Otherwise late ACKs from the previous round dilute the median and hide a growing queue (a regression test covers this). Slow-start growth still happens on every valid sample. |
| D13 | Global baseline fallback is the "10th percentile" | Defined as nearest rank, `sorted[floor(0.1·(n−1))]`, over the same 30 s window. |
| D14 | `duplicate_shards`: "a duplicate shard arrival means the original had arrived too" | The receiver tracks every (segment, shard) it has received, and counts only exact repeats. A shard that arrives *after* its segment is complete is usually one of the N−K spares, not a duplicate. Spares now get 2 reply copies; only true repeats get the escalated 4 (Phase 8 sent 4 to both). |
| D15 | — | The sender now publishes `ShardAcked` once per shard. Duplicate ACK copies (the receiver replies over 2 paths) used to fire it twice, which would double-count in the GUI and the RTT logic. |
| D16 | Phase 8 `delay` fault (used by the 9b tests) | The injector modelled delay by sleeping **inside the sender's coroutine**. That stalled the receiver's sequential message loop on every ACK reply, so a "+50 ms" test really cost about 100 ms of sender time per shard. A 2 MB `fixed` transfer took 20.8 s instead of 4 s, and RTTs read 8.5 s. **Fixed:** a delayed onion is now written by a background task after the delay, and the caller continues, which gives true latency semantics. All Phase 8 tests still pass. |
| D17 | Slow start ends "when queued > gamma" | Checked on **every ACK** once the current round has 3 or more samples, not only at round end. Waiting for the round end let the window overshoot (cwnd 76 with 50 retries and 8 duplicates); with the per-ACK check there are 0 retries and 0 duplicates, at +50 ms on 2 MB. |
| D18 | The RTT dispatcher | Lives in `TransferService` (`_send_segments_rtt` and friends) next to the fixed path, sharing its routing, guards and penalties. `_send_on` is split into `_build` (a fresh onion) and `_transmit`, so the send time is stamped between building and writing. `SendResult` also reports `peak_inflight_bytes`, which the memory-cap test uses. |
| D19 | "Each timeout doubles rto (up to max_rto) until the next valid sample resets it" | That's RFC 6298's single-path rule. With multipath, shards routed through one blackholed relay doubled the shared rto for **every** path, up to 10 s, and late in a transfer most shards are retransmits (no samples under Karn's rule), so nothing reset it. **Now:** a retransmitted shard backs off its own deadline (`rto × 2^attempts`, capped at `max_rto`), and the shared rto backs off only when the controller detects a **burst** across paths (spec item 6). Measured with a blackholed relay and health probes off: first timeouts have a median of about 1 s, and none reach the old 10 s. |
| D20 | `CwndChanged(transfer_id, cwnd, inflight, srtt_ms, base_rtt_ms, queue_est, rto_ms, mode)` | Also carries `node_id` and `inflight_bytes`, so the spec's memory-cap check can read it from the events. `base_rtt_ms` is the global baseline (10th percentile). A final event is always published when the transfer ends. |
| D21 | — | **Test-harness bug found and fixed:** the CLI tests started the cluster with `stdout=PIPE` and never read it. The larger 9b transfers logged more than 64 KB of relay lines, filled the pipe, and blocked the cluster. Output now goes to `DEVNULL` or a log file. |
| D22 | E4: "cwnd and srtt traces from `--trace-cwnd`" | A scenario of its own, `cwnd_trace`, so the traces come from the same seeded clusters as E2. The CSV code moved from `cli.py` to `mpos/net/cwnd_trace.py` and is shared by both. |
| D23 | E3: "time to recover" | Measured as **time from the fault to the sender's first retransmit** (`first_retry_ms`): the moment it notices. A slow spell that causes no retransmit at all leaves it empty (`retried_runs` counts the runs that had one). The file is 16 MB because the `fixed` window has 4 MiB in flight from the start: with a smaller file, `fixed` has already sent everything when the fault lands, and the comparison is meaningless. |
| D24 | E1: "transfers of S segments" | One trial = one sender doing one transfer. With guards, S hardly matters (the first hops are the same 3 guards), so "many transfers" is the same as "many segments"; without guards each segment is fresh exposure. The two endpoints are not relays, so R is exactly the relay count. |
| D25 | — | Experiment runs now stop the clock when the **transfer** ends, not when the scenario's fault script returns (E3's slow spell can outlast the transfer). Phase 8's `relay_kill` timings use the same, slightly more precise, rule. |
