from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mpos.crypto.keys import encode_public_key, generate_private_key, public_key_bytes
from mpos.net.directory import Directory, NodeInfo


def _info(node_id: str, port: int = 9100) -> NodeInfo:
    return NodeInfo(node_id, "10.0.0.1", port, generate_private_key().public_key())


def test_save_load_roundtrip(tmp_path: Path) -> None:
    directory = Directory([_info("alpha", 9100), _info("bravo", 9101)])
    path = tmp_path / "nodes.json"

    directory.save(path)
    loaded = Directory.load(path)

    assert [n.node_id for n in loaded] == ["alpha", "bravo"]
    for original in directory:
        copy = loaded.get(original.node_id)
        assert (copy.host, copy.port, copy.roles) == (
            original.host,
            original.port,
            original.roles,
        )
        assert public_key_bytes(copy.public_key) == public_key_bytes(
            original.public_key
        )


def test_lookup_and_membership() -> None:
    directory = Directory([_info("alpha")])

    assert "alpha" in directory and "ghost" not in directory
    assert len(directory) == 1
    assert directory.get("alpha").hop.node_id == "alpha"
    with pytest.raises(KeyError):
        directory.get("ghost")

    directory.remove("alpha")
    assert len(directory) == 0


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": "alpha",
        "host": "h",
        "port": 9100,
        "pubkey": encode_public_key(generate_private_key()),
    }
    entry.update(overrides)
    return entry


@pytest.mark.parametrize(
    "entry",
    [
        _entry(port=0),
        _entry(port=70000),
        _entry(port="abc"),
        _entry(pubkey="short"),
        {"id": "alpha", "host": "h"},
    ],
)
def test_invalid_entries_rejected(entry: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="Invalid directory entry"):
        Directory.from_dict({"version": 1, "nodes": [entry]})


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        Directory.from_dict({"version": 1, "nodes": [_entry(), _entry()]})


def test_wrong_version_rejected() -> None:
    with pytest.raises(ValueError, match="version"):
        Directory.from_dict({"version": 2, "nodes": []})
