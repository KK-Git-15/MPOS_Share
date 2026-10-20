import asyncio
import json
import struct


HEADER_SIZE = 4


async def send_packet(
    writer,
    metadata,
    payload
):

    """
    Send one packet over TCP.

    Packet format:

        4 bytes  -> metadata length
        N bytes  -> JSON metadata
        4 bytes  -> payload length
        N bytes  -> payload
    """

    metadata_bytes = json.dumps(
        metadata
    ).encode("utf-8")

    metadata_length = len(
        metadata_bytes
    )

    payload_length = len(
        payload
    )

    packet = (

        struct.pack(
            "!I",
            metadata_length
        )

        + metadata_bytes

        + struct.pack(
            "!I",
            payload_length
        )

        + payload
    )

    writer.write(
        packet
    )

    await writer.drain()


async def receive_packet(
    reader
):

    """
    Receive one packet from TCP.

    Returns:

        metadata, payload
    """

    header = await reader.readexactly(
        HEADER_SIZE
    )

    metadata_length = struct.unpack(
        "!I",
        header
    )[0]

    metadata_bytes = (
        await reader.readexactly(
            metadata_length
        )
    )

    metadata = json.loads(
        metadata_bytes.decode(
            "utf-8"
        )
    )

    payload_header = (
        await reader.readexactly(
            HEADER_SIZE
        )
    )

    payload_length = struct.unpack(
        "!I",
        payload_header
    )[0]

    payload = (
        await reader.readexactly(
            payload_length
        )
    )

    return (
        metadata,
        payload
    )