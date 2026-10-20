import asyncio
import json
import struct


HEADER_SIZE = 4


async def send_packet(writer, metadata, payload):
    """
    Send one packet.

    Packet format:

        4 bytes  -> metadata length
        N bytes  -> JSON metadata
        4 bytes  -> payload length
        N bytes  -> payload
    """

    metadata_bytes = json.dumps(
        metadata
    ).encode("utf-8")

    packet = (
        struct.pack(
            "!I",
            len(metadata_bytes)
        )
        + metadata_bytes
        + struct.pack(
            "!I",
            len(payload)
        )
        + payload
    )

    writer.write(packet)

    await writer.drain()


async def receive_packet(reader):
    """
    Receive one complete packet.
    """

    # -----------------------------
    # Metadata length
    # -----------------------------

    header = await reader.readexactly(
        HEADER_SIZE
    )

    metadata_length = struct.unpack(
        "!I",
        header
    )[0]

    # -----------------------------
    # Metadata
    # -----------------------------

    metadata_bytes = await reader.readexactly(
        metadata_length
    )

    metadata = json.loads(
        metadata_bytes.decode("utf-8")
    )

    # -----------------------------
    # Payload length
    # -----------------------------

    payload_header = await reader.readexactly(
        HEADER_SIZE
    )

    payload_length = struct.unpack(
        "!I",
        payload_header
    )[0]

    # -----------------------------
    # Payload
    # -----------------------------

    payload = await reader.readexactly(
        payload_length
    )

    return metadata, payload