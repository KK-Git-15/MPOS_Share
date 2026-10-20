"""Step 6f: the CLI as separate OS processes over real TCP."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mpos.cli import build_cluster, identity_path, main
from mpos.crypto.keys import public_key_bytes
from mpos.net.directory import Directory
from tests.helpers import free_port_block

MPOS = [sys.executable, "-m", "mpos"]


def _wait_for_ports(ports: list[int], proc: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    for port in ports:
        while True:
            assert proc.poll() is None, proc.stdout.read() if proc.stdout else ""
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                assert time.monotonic() < deadline, f"port {port} never opened"
                time.sleep(0.05)


# ============================================================
# IN-PROCESS
# ============================================================


def test_keygen_writes_identity_and_directory(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.json"
    argv = ["--host", "10.0.0.5", "--port", "9100", "--data-dir", str(tmp_path)]

    assert main(["keygen", "--id", "alpha", *argv, "--directory", str(nodes)]) == 0
    assert main(["keygen", "--id", "bravo", *argv, "--directory", str(nodes)]) == 0

    directory = Directory.load(nodes)
    assert [n.node_id for n in directory] == ["alpha", "bravo"]
    assert (identity_path(tmp_path, "alpha").stat().st_mode & 0o777) == 0o600

    # Refuses to clobber an identity without --force
    assert main(["keygen", "--id", "alpha", *argv]) == 1


def test_build_cluster_reuses_identities(tmp_path: Path) -> None:
    first = build_cluster(tmp_path, relays=3, base_port=9100, host="127.0.0.1")
    second = build_cluster(tmp_path, relays=3, base_port=9200, host="127.0.0.1")

    assert [n.node_id for n in first] == ["alice", "bob", "r0", "r1", "r2"]
    for a, b in zip(first, second, strict=True):
        assert public_key_bytes(a.public_key) == public_key_bytes(b.public_key)
        assert b.port == a.port + 100


def test_node_refuses_mismatched_identity(tmp_path: Path) -> None:
    build_cluster(tmp_path, relays=3, base_port=free_port_block(5), host="127.0.0.1")
    identity_path(tmp_path, "bob").unlink()
    identity_path(tmp_path, "alice").rename(identity_path(tmp_path, "bob"))

    code = main(
        [
            "node",
            "--id",
            "bob",
            "--directory",
            str(tmp_path / "nodes.json"),
            "--data-dir",
            str(tmp_path),
            "--bind",
            "127.0.0.1",
        ]
    )

    assert code == 1


# ============================================================
# SEPARATE PROCESSES
# ============================================================


@pytest.mark.timeout(90)
def test_send_between_processes(tmp_path: Path) -> None:
    base = free_port_block(7)
    cluster_dir = tmp_path / "cluster"
    source = tmp_path / "payload.bin"
    source.write_bytes(os.urandom(3 * 1024 * 1024 + 7))

    log = (tmp_path / "cluster.log").open("w")
    cluster = subprocess.Popen(
        [*MPOS, "local-cluster", "--dir", str(cluster_dir), "--base-port", str(base)],
        # A file, not a PIPE: an unread pipe fills up (64 KB of relay logs)
        # and blocks the cluster mid-transfer
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_ports(list(range(base + 1, base + 7)), cluster)

        sent = subprocess.run(
            [
                *MPOS,
                "send",
                "--id",
                "alice",
                "--directory",
                str(cluster_dir / "nodes.json"),
                "--data-dir",
                str(cluster_dir),
                "--bind",
                "127.0.0.1",
                "--file",
                str(source),
                "--to",
                "bob",
                "--segment-kib",
                "256",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert sent.returncode == 0, sent.stdout + sent.stderr
        assert "confirmed by receiver" in sent.stdout

        received = cluster_dir / "bob" / "inbox" / "payload.bin"
        assert received.read_bytes() == source.read_bytes()

    finally:
        cluster.send_signal(signal.SIGTERM)
        cluster.wait(timeout=15)
        log.close()
    output = (tmp_path / "cluster.log").read_text()

    assert cluster.returncode == 0, output
    assert "[bob] received" in output


def test_gui_command_without_pyside6_fails_cleanly() -> None:
    """Every other command must keep working on machines without PySide6."""
    code = (
        "import sys; sys.modules['PySide6'] = None; "
        "from mpos.cli import main; sys.exit(main(['gui']))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 1
    assert "needs PySide6" in result.stderr
