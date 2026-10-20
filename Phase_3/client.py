import asyncio
import os
import time

from protocol import send_packet


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


HOST = "127.0.0.1"
PORT = 9000

INPUT_DIR = os.path.join(PROJECT_ROOT, "encrypted_shards")


async def send_shard(shard_path):
    """
    Open one TCP connection and send one encrypted shard.
    """

    filename = os.path.basename(shard_path)

    # Example:
    # encrypted_shard_03.bin
    #
    # Extract:
    # 03

    shard_id = int(
        filename.split("_")[2].split(".")[0]
    )

    # Read encrypted shard.
    with open(shard_path, "rb") as f:
        payload = f.read()

    start_time = time.perf_counter()

    # Create an independent TCP connection.
    reader, writer = await asyncio.open_connection(
        HOST,
        PORT
    )

    metadata = {
        "type": "ENCRYPTED_SHARD",
        "shard_id": shard_id,
        "payload_size": len(payload)
    }

    print(
        f"[CLIENT] TCP connection created "
        f"for shard {shard_id}"
    )

    await send_packet(
        writer,
        metadata,
        payload
    )

    # Wait for the TCP connection to close.
    writer.close()
    await writer.wait_closed()

    elapsed = time.perf_counter() - start_time

    print(
        f"[CLIENT] Shard {shard_id} sent "
        f"in {elapsed:.4f} seconds"
    )

    return shard_id


async def send_all_shards():

    shard_files = sorted(
        filename
        for filename in os.listdir(INPUT_DIR)
        if filename.startswith("encrypted_shard_")
        and filename.endswith(".bin")
    )

    if not shard_files:
        raise ValueError(
            "No encrypted shards found."
        )

    shard_paths = [
        os.path.join(INPUT_DIR, filename)
        for filename in shard_files
    ]

    print("===================================")
    print("       MPOS PHASE 3 CLIENT")
    print("===================================")

    print(
        f"Encrypted shards: {len(shard_paths)}"
    )

    print()
    print("Starting simultaneous TCP transfers...")
    print()

    start_time = time.perf_counter()

    # Create one asyncio task for every shard.
    tasks = [
        asyncio.create_task(
            send_shard(path)
        )
        for path in shard_paths
    ]

    # Wait for ALL connections concurrently.
    results = await asyncio.gather(
        *tasks
    )

    total_time = time.perf_counter() - start_time

    print()
    print("===================================")
    print("       TRANSFER COMPLETE")
    print("===================================")

    print(
        f"Shards transmitted: {len(results)}"
    )

    print(
        f"Total transfer time: "
        f"{total_time:.4f} seconds"
    )


if __name__ == "__main__":
    asyncio.run(
        send_all_shards()
    )
