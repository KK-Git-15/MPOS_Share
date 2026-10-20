# Phase 8 — Failure Simulation & Fault Tolerance

Phase 8 breaks the network on purpose, and shows that files still arrive intact, or fail cleanly with a clear reason.

## 1. Injecting failures

| Fault | What happens | Where you can trigger it |
|---|---|---|
| **drop** | An onion vanishes on the link | GUI Fault Lab, `--faults drop=0.2`, experiments |
| **delay ± jitter** | Each send waits first | GUI, `--faults delay=50,jitter=10` |
| **corrupt** | One bit of ciphertext is flipped, so the next hop's AES-GCM check fails | GUI, `--faults corrupt=0.05`, experiments |
| **duplicate** | The onion is sent twice | GUI, `--faults duplicate=0.5` |
| **truncate** | Half a frame is sent, then the connection is cut | GUI, `--faults truncate=0.1` |
| **kill / revive** | The node stops abruptly, or restarts | GUI Fault Lab, experiments |
| **isolate (partition)** | A node can't reach anyone, and nobody can reach it | GUI Fault Lab |
| **blackhole** | The node accepts onions and silently throws them away. It still answers pings, which makes it the hardest fault to detect | GUI Fault Lab, `--faults blackhole` |

All randomness comes from one seed: the same seed and the same traffic give the same faults.

**In the GUI** (`mpos gui --demo`), open **Fault Lab**:
- Set the rates, choose *All nodes* or a single node, and click **Apply**.
- Use **Kill**, **Revive**, **Isolate** or **Blackhole** on a node.
- The *Status* box lists the active faults and counts every injected one.
- The map flashes nodes **orange** where a fault hits and **red** where a node rejects a packet.

**On a real relay** (multi-device test):
```bash
mpos node --id r2 --directory nodes.json --faults drop=0.2,corrupt=0.05
```

## 2. How the network copes

| Mechanism | Handles |
|---|---|
| **K-of-N erasure coding**: any 4 of 6 shards rebuild a segment | Up to 2 lost shards per segment without any retry |
| **ACK + retry on a new route**, with backoff capped at 2 s | Lost shards, dropped or corrupted onions, dead relays |
| **AES-GCM at every hop** | Corruption: detected at the very next hop and never forwarded |
| **Replay protection**: a relay drops any layer it has already processed | Duplicates (stops copies doubling at every hop) and replay attacks |
| **Heartbeats** (ping every 2 s; 3 misses = *down*) | Dead, killed and partitioned relays are avoided *before* shards are lost there |
| **Onion probes** (me → relay → me; 3 lost = *suspect*) | Blackholes, which answer pings but drop onions |
| **Critical messages over 2 routes**; receiver answers repeats over 4 | Manifest, completion and ACKs, which have no N−K spares |
| **Penalty learning**: failed routes are blamed, successful routes forgiven | Steers traffic away from bad relays quickly |
| **Resumable receiver**: shards are saved to disk *before* they're ACKed | Receiver crash or restart mid-transfer: it carries on from where it stopped |

## 3. Experiments

```bash
mpos experiment                       # everything: ~125 runs, a few minutes
mpos experiment --quick --runs 2      # a fast look
mpos experiment --scenario relay_kill --runs 10 --seed 7
```
Each run starts a **fresh 7-node cluster** (alice, bob, r0–r4) in a temporary folder, seeded, and sends one file from alice to bob. Results go to `results/<scenario>-<time>/`:
- `runs.csv`: one row per run (ok, intact, time, retries, sends, faults, error)
- `summary.csv`: one row per setting (success %, intact %, median time, mean retries, wrong files, …)

Both open directly in Excel or Google Sheets, so you can chart them.

| Scenario | Varies | File | Shows |
|---|---|---|---|
| `drop_sweep` | packet loss on **every link** of 0–30%, codes 4-of-6, 4-of-8, 6-of-8 | 256 KB | How redundancy (N−K) buys tolerance to loss |
| `relay_kill` | 0–3 of 5 relays killed mid-transfer | 2 MB | How many relays can die (`killed_mid_transfer` confirms the kill hit a running transfer) |
| `corruption` | 5–50% of onions corrupted | 256 KB | Detection rate (`detected_pct`, expected 100) and wrong files accepted (expected 0) |

Settings for every run: 64 KB segments, ACK timeout 0.3 s, up to 6 retries per shard, backoff 0.02 s doubling to at most 0.5 s.

## 4. Results

Measured on one MacBook Air (Apple silicon), Python 3.14, with `mpos experiment --runs 5 --seed 1`. That's 125 runs in total, and the raw data is in [`sample-results/`](sample-results/). Your numbers will vary slightly with machine speed; the shape of the results should not.

**Across all 125 runs, not a single file was accepted with wrong contents** (`wrong_files = 0` everywhere). Every transfer either delivered a byte-identical file or failed with a clear error.

### Packet loss on every link (`drop_sweep`)

Success rate (%), with the median time in seconds in brackets:

| Loss per link | 4-of-6 (2 spares) | 4-of-8 (4 spares) | 6-of-8 (2 spares) |
|---|---|---|---|
| 0% | 100 (0.16) | 100 (0.22) | 100 (0.20) |
| 5% | 100 (0.14) | 100 (0.18) | 100 (0.40) |
| 10% | 100 (0.74) | 100 (0.44) | 100 (0.74) |
| 15% | 100 (1.11) | 100 (0.78) | 100 (1.11) |
| 20% | 100 (1.81) | 100 (1.36) | 100 (2.29) |
| 30% | **20** (3.59) | **60** (4.83) | **0** |

- **Up to 20% loss on every link** (about 59% of routes lose a shard end to end), every code delivers every file. It costs time and retries: 4-of-6 averaged 35 retries at 20% loss, against 0 at 0%.
- **More spares means more tolerance:** 4-of-8 (4 spare shards) still delivers 60% of files at 30% loss, while 4-of-6 manages 20%. It's also *faster* under loss (1.36 s against 1.81 s at 20%), because fewer shards need re-sending.
- **The number of spares matters, not just N:** 6-of-8 has the same N as 4-of-8 but only 2 spares, *and* needs 6 shards per segment instead of 4, so it does worst under loss.

### Relays killed mid-transfer (`relay_kill`, 5 relays, 2 MB file)

| Killed | Success | Median time | Mean retries |
|---|---|---|---|
| 0 | 100% | 1.13 s | 0 |
| 1 | 100% | 1.12 s | 9.6 |
| 2 | 100% | 1.13 s | 21.8 |
| 3 | 0% | — | — |

Every kill landed while the transfer was running (`killed_mid_transfer` = 5/5). Losing 1 or 2 of the 5 relays costs retries but no time and no data. With 3 killed, only 2 relays remain and every route needs 3 distinct relays, so transfers stop with `TransferTimeout`. That's the designed limit, not a bug.

### Corruption (`corruption`)

| Onions corrupted | Success | Corrupted | Rejected at next hop | Detected |
|---|---|---|---|---|
| 5% | 100% | 92 | 92 | **100%** |
| 20% | 100% | 540 | 540 | **100%** |
| 50% | 0% | 714 | 714 | **100%** |

**Every single corrupted onion (1,346 of 1,346) was caught by the AES-GCM check at the next hop, and none was forwarded.** Up to 20% corruption, retries hide it completely. At 50% corruption on every link, a route works end to end only about 0.4% of the time (0.5⁸), so transfers fail cleanly instead of delivering anything wrong.

## 5. Honest limitations (for the report)

- **Loss compounds over hops:** a shard crosses 4 links to arrive and its ACK crosses 4 more. At 30% loss on *every link*, a single route succeeds end to end only about 6% of the time (0.7⁸), so transfers start failing. That's the steep curve in the drop sweep, and raising N moves it.
- **Probes are statistical:** under heavy random loss, an individual node can briefly suspect a healthy relay. The GUI shows how many peers agree (red when most agree, amber for a minority), and the routing penalty is soft, so this costs only some efficiency.
- **Only the receiver can resume:** if the *sender* crashes, the transfer has to be restarted.
- **Faults apply to onions, not heartbeats:** link faults (drop, corrupt, …) hit onions only. Heartbeats are affected only by kill and partition, so random loss doesn't make healthy nodes look dead.
