# Phase 6 — Cross-Device Transfer

A file is split into K-of-N Reed-Solomon shards (default 4-of-6). Each shard is encrypted end to end and sent on its own onion route through 3 relays. The receiver needs any 4 shards to rebuild a segment, verifies the whole file's sha256, and confirms back to the sender.

## Setup (every device)

```bash
cd "CN_LAB_Project 2"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # or, without the GUI: grep -vi pyside requirements.txt | pip install -r /dev/stdin
pip install -e .                  # provides the `mpos` command
```

## 1. Try it on one machine

```bash
mpos local-cluster                      # terminal 1: bob + relays r0..r4 on 127.0.0.1:9101-9106
mpos send --id alice --directory data/cluster/nodes.json --data-dir data/cluster \
          --bind 127.0.0.1 --file Input/Input.txt --to bob   # terminal 2
```
The file lands in `data/cluster/bob/inbox/`. Stop the cluster with Ctrl+C.

## 2. Run across real devices

You need a **sender**, a **receiver** and **at least 3 relays besides them**. A device can run several nodes as long as each has its own id and port.

Example with two laptops on the same Wi-Fi:

| Device | IP | Nodes |
|---|---|---|
| Laptop A | 192.168.1.20 | `alice` (sender, port 9100), `r0` (9101), `r1` (9102) |
| Laptop B | 192.168.1.30 | `bob` (receiver, port 9100), `r2` (9101) |

**a) Find each device's LAN IP.** On macOS run `ipconfig getifaddr en0`, on Linux `hostname -I`, on Windows `ipconfig`.

**b) Create identities on the device where each node will run.** Private keys never leave the device.
```bash
# Laptop A
mpos keygen --id alice --host 192.168.1.20 --port 9100 --roles receiver --directory nodes.json
mpos keygen --id r0    --host 192.168.1.20 --port 9101 --directory nodes.json
mpos keygen --id r1    --host 192.168.1.20 --port 9102 --directory nodes.json
# Laptop B
mpos keygen --id bob   --host 192.168.1.30 --port 9100 --directory nodes.json
mpos keygen --id r2    --host 192.168.1.30 --port 9101 --directory nodes.json
```
`--roles receiver` keeps `alice` out of other people's relay paths. Use it on any node that is not always online.

**c) Merge the directory.** Combine the `nodes` entries from both `nodes.json` files into one file, and copy that same file to every device. It contains only public keys.

**d) Start the long-running nodes** (one terminal each):
```bash
# Laptop A
mpos node --id r0 --directory nodes.json
mpos node --id r1 --directory nodes.json
# Laptop B
mpos node --id bob --directory nodes.json
mpos node --id r2  --directory nodes.json
```

**e) Send** from Laptop A. `send` starts `alice` for the duration of the transfer, so don't also run `mpos node --id alice`:
```bash
head -c 52428800 /dev/urandom > big.bin   # 50 MB test file
mpos send --id alice --directory nodes.json --file big.bin --to bob
```

**f) Verify** that `shasum -a 256 big.bin` on A matches `shasum -a 256 data/bob/inbox/big.bin` on B. The sender also prints the sha256 the receiver confirmed.

## Troubleshooting

- **Connection timeouts between devices:**
  - Check that each device can reach the others with `nc -vz 192.168.1.30 9100`.
  - On macOS, allow incoming connections for Python (System Settings → Network → Firewall).
  - Some public, hotel and campus Wi-Fi networks block traffic between devices (client isolation). A phone hotspot or home router usually works.
- **`does not match the public key listed`:** the identity was regenerated after `nodes.json` was shared. Re-share the updated entry.
- **`Need 3 relays besides the endpoints`:** add relays, or lower `--hops` for a quick test.
- **Slow transfers with retries:** a listed relay is offline. Transfers still finish by routing around it. Stop listing nodes that aren't running.

## What the result shows

- `onions N sent, R retries` is the total number of onions sent. With N=6 shards per segment, a clean run sends 6 × segments + 1 (the manifest). R counts re-sends on new paths.
- Relay logs show only `relayed <previous> -> <next>`: no file names, sizes or content.
