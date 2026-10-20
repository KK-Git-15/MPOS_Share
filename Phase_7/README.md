# Phase 7 — Desktop GUI

A PySide6 window that runs, watches and controls MPOS nodes. The network engine runs on a background asyncio thread. The window only sends it commands and draws the events it publishes, so the window stays responsive during large transfers.

## Run it

```bash
pip install PySide6          # ~600 MB, once
mpos gui --demo              # starts alice, bob and relays r0..r4 on 127.0.0.1:9100-9106
```

Then open **Send / Receive**: From `alice`, To `bob`, pick a file, and click **Send**. Switch to **Routing map** to watch the shards travel.

Other ways to start:
```bash
mpos gui --directory nodes.json --data-dir data   # a real multi-device directory
mpos gui --demo --demo-port 19100                 # if ports 9100-9106 are busy
```

## Pages

| Page | Shows | Controls |
|---|---|---|
| **Nodes** | Every directory entry: running ● / offline ●, address, roles, open links, onions relayed, onions dropped | Open nodes.json…, Start demo cluster, Start selected / all, Stop selected, listen address |
| **Routing map** | Nodes on a ring. Each circuit is a fading line, coloured by transfer (solid = data, dashed = acknowledgement). Yellow dots are onions moving hop by hop; a node flashes red when it drops one. The sender is ringed blue, the receiver purple | — |
| **Send / Receive** | Transfer list with progress and the verified ✓ / failed ✗ result (hover for the sha256 or the error). The shard grid has rows = segments and columns = shards: grey pending, yellow sent, orange retried, green acknowledged; faded = not needed because the segment already had K | From, To, file, K of N, segment size, Send |
| **Fault Lab** | Layout for Phase 8 fault injection (disabled for now) | — |
| **Logs** | The newest 10,000 log lines | Level filter, text filter (node, transfer id, …), Follow, Clear, Export… |

The status bar shows nodes running, active transfers and engine events per second.

## Packet Inspector

**Double-click a node on the Routing map**, or right-click it and choose *Inspect packets at …*, to open the inspector dock on the right. You can also open it from **View → Packet Inspector**, or double-click near a node on a circuit line. It lists the last 100 onions that node processed, newest first. *Follow live* keeps it on the newest one, and clicking an older packet pins it. For the selected packet it shows:

- **AES-GCM badge:** green `VERIFIED` or red `FAILED (InvalidTag)` (try the tampering tests to see red).
- **Header info:** node, previous hop, circuit and time. The circuit id is per link, so it can't be used to follow an onion across hops. **Position** is what the node can *infer from the packet's size*. Relays are never told their position, but each layer adds exactly 127 bytes, so the size gives it away. This is the documented leak in CLAUDE.md §3.1.3.
- **Hex view:** the 17-byte link frame header, the public 45-byte layer prefix (version, one-time X25519 key, nonce), the first 32 bytes of ciphertext, and the 16-byte GCM tag, each at its real offset.
- **Decrypted routing header:** everything the node learned by decrypting its layer. That's `{cmd: relay, next: r0}` for a relay, and just "deliver" for the receiver.
- **Visible / Hidden:** a plain statement of what this node can and cannot learn.

What the inspector does and doesn't prove:
- Relays see only their own layer: a routing header naming the next hop, plus ciphertext they can't read. They never see file contents.
- The **guard always learns the sender** (its previous hop), and the **exit always learns the receiver** (its next hop). This is true of every onion-routing design, Tor included. Only middle relays know neither, and the inspector flags these cases with ⚠ instead of hiding them.
- Packet inspection is **opt-in** (`Engine(inspect_packets=True)`, enabled by `mpos gui`). The CLI doesn't publish it. The events never contain payload plaintext; `tests/integration/test_inspection.py` checks this byte by byte with a marker payload.

## How it is built

```
Qt main thread                              engine thread (asyncio)
--------------                              -----------------------
MainWindow + panels  ◄── QtBridge.events ◄── EventBus ◄── NodeDaemon / TransferService
      │                  (batched list)                        ▲
      └── QtBridge.call(coro) ── run_coroutine_threadsafe ──► Engine (mpos/engine.py)
```
- `mpos/events.py`: typed events plus a non-blocking `EventBus` (no Qt). A slow subscriber loses its oldest events and never stalls the network.
- `mpos/engine.py`: the headless engine. The CLI uses it too.
- `mpos/gui/bridge.py`: `EngineThread` and `QtBridge`. Command results come back to the GUI thread through a queued signal.
- Panels update their state on every event batch and repaint at most 20 times a second, from one 50 ms timer.

## Verified

`tests/gui/test_app.py` drives the real window offscreen:
1. Start the demo cluster.
2. Send 12 MB (49 segments).
3. Check that both ends show verified ✓, the grid shows ≥ K acknowledged shards in every segment, and the received file is byte-identical.

A 10 ms heartbeat timer runs throughout and must never be more than 250 ms late. In practice it stays under 20 ms.
