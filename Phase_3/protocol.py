import asyncio
import json
import struct


HEADER_SIZE = 4


async def send_packet(writer, metadata, payload):
    """
    Send one encrypted shard over a TCP connection.

    Packet format:

        4 bytes  -> metadata length
        N bytes  -> JSON metadata
        4 bytes  -> payload length
        N bytes  -> encrypted shard
    """

    # Convert metadata to JSON bytes.
    metadata_bytes = json.dumps(metadata).encode("utf-8")

    # Metadata length.
    metadata_length = len(metadata_bytes)

    # Payload length.
    payload_length = len(payload)

    # Build packet.
    packet = (
        struct.pack("!I", metadata_length)
        + metadata_bytes
        + struct.pack("!I", payload_length)
        + payload
    )

    # Send packet.
    writer.write(packet)

    await writer.drain()


async def receive_packet(reader):
    """
    Receive one encrypted-shard packet from a TCP connection.

    Returns:

        metadata, payload
    """

    # -----------------------------
    # Read metadata length
    # -----------------------------

    header = await reader.readexactly(
        HEADER_SIZE
    )

    metadata_length = struct.unpack(
        "!I",
        header
    )[0]

    # -----------------------------
    # Read metadata
    # -----------------------------

    metadata_bytes = await reader.readexactly(
        metadata_length
    )

    metadata = json.loads(
        metadata_bytes.decode("utf-8")
    )

    # -----------------------------
    # Read payload length
    # -----------------------------

    payload_header = await reader.readexactly(
        HEADER_SIZE
    )

    payload_length = struct.unpack(
        "!I",
        payload_header
    )[0]

    # -----------------------------
    # Read encrypted shard
    # -----------------------------

    payload = await reader.readexactly(
        payload_length
    )

    return metadata, payload