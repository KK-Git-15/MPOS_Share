"""Finding free local ports for test clusters and experiments."""

from __future__ import annotations

import socket


def free_port_block(count: int, host: str = "127.0.0.1") -> int:
    """First port of ``count`` consecutive ports that are free right now."""
    for base in range(20000, 60000, 97):
        sockets = []
        try:
            for port in range(base, base + count):
                s = socket.socket()
                sockets.append(s)
                s.bind((host, port))
            return base
        except OSError:
            continue
        finally:
            for s in sockets:
                s.close()
    raise RuntimeError(f"No block of {count} free ports on {host}")
