import asyncio
import os
import random
import time

from protocol import send_packet
from topology import create_topology


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

INPUT_DIR = os.path.join(PROJECT_ROOT, "encrypted_shards")

HOST = "127.0.0.1"


# ============================================================
# FIND ENCRYPTED SHARDS
# ============================================================

def get_shard_files():

    if not os.path.isdir(INPUT_DIR):

        raise FileNotFoundError(
            f"Encrypted shard directory not found:\n"
            f"{INPUT_DIR}"
        )

    files = [
        filename
        for filename in os.listdir(INPUT_DIR)
        if filename.startswith("encrypted_shard_")
        and filename.endswith(".bin")
    ]

    return sorted(files)


# ============================================================
# EXTRACT SHARD ID
# ============================================================

def get_shard_id(filename):

    # Example:
    #
    # encrypted_shard_03.bin
    #
    # → 3

    return int(
        filename.split("_")[2].split(".")[0]
    )


# ============================================================
# CREATE RANDOM SHARD → CIRCUIT MAPPING
# ============================================================

def create_random_assignment(
    shard_files,
    topology
):

    shard_ids = [
        get_shard_id(filename)
        for filename in shard_files
    ]

    circuit_ids = list(
        topology.keys()
    )

    if len(shard_ids) != len(circuit_ids):

        raise ValueError(
            "Number of shards and circuits "
            "must be equal."
        )

    # Randomize circuit order.
    random.shuffle(
        circuit_ids
    )

    # Create one-to-one mapping.
    assignment = dict(
        zip(
            shard_ids,
            circuit_ids
        )
    )

    return assignment


# ============================================================
# SEND ONE SHARD
# ============================================================

async def send_shard(
    shard_path,
    topology,
    shard_to_circuit
):

    filename = os.path.basename(
        shard_path
    )

    shard_id = get_shard_id(
        filename
    )

    # -----------------------------------------
    # Get randomly assigned circuit
    # -----------------------------------------

    circuit_id = shard_to_circuit[
        shard_id
    ]

    circuit = topology[
        circuit_id
    ]

    guard_port = circuit[
        "guard"
    ]

    # -----------------------------------------
    # Read encrypted shard
    # -----------------------------------------

    with open(
        shard_path,
        "rb"
    ) as f:

        payload = f.read()

    # -----------------------------------------
    # Packet metadata
    # -----------------------------------------

    metadata = {

        "type": "ENCRYPTED_SHARD",

        "shard_id": shard_id,

        "circuit_id": circuit_id,

        "payload_size": len(payload)
    }

    print(
        f"[CLIENT] "
        f"Shard {shard_id} "
        f"-> Circuit {circuit_id} "
        f"-> Guard {guard_port}"
    )

    start_time = time.perf_counter()

    try:

        # -------------------------------------
        # Connect to Guard
        # -------------------------------------

        reader, writer = (
            await asyncio.open_connection(
                HOST,
                guard_port
            )
        )

        # -------------------------------------
        # Send encrypted shard
        # -------------------------------------

        await send_packet(
            writer,
            metadata,
            payload
        )

        # -------------------------------------
        # Close connection
        # -------------------------------------

        writer.close()

        await writer.wait_closed()

        elapsed = (
            time.perf_counter()
            - start_time
        )

        print(
            f"[CLIENT] "
            f"Shard {shard_id} "
            f"completed through Circuit "
            f"{circuit_id} "
            f"in {elapsed:.4f}s"
        )

        return True

    except Exception as e:

        print(
            f"[CLIENT] "
            f"Shard {shard_id} "
            f"FAILED through Circuit "
            f"{circuit_id}: {e}"
        )

        return False


# ============================================================
# MAIN
# ============================================================

async def main():

    # -----------------------------------------
    # Find encrypted shards
    # -----------------------------------------

    shard_files = get_shard_files()

    if not shard_files:

        raise ValueError(
            "No encrypted shards found."
        )

    num_shards = len(
        shard_files
    )

    # -----------------------------------------
    # Create circuits
    # -----------------------------------------

    topology = create_topology(
        num_shards
    )

    # -----------------------------------------
    # Randomly assign shards to circuits
    # -----------------------------------------

    shard_to_circuit = create_random_assignment(
        shard_files,
        topology
    )

    # -----------------------------------------
    # Header
    # -----------------------------------------

    print()
    print(
        "======================================"
    )

    print(
        "          MPOS PHASE 4 CLIENT"
    )

    print(
        "======================================"
    )

    print(
        f"Detected shards   : {num_shards}"
    )

    print(
        f"Available circuits: {num_shards}"
    )

    print()

    # -----------------------------------------
    # Display random assignment
    # -----------------------------------------

    print(
        "Random shard-to-circuit assignment:"
    )

    for shard_id in sorted(
        shard_to_circuit
    ):

        circuit_id = (
            shard_to_circuit[
                shard_id
            ]
        )

        circuit = topology[
            circuit_id
        ]

        print(
            f"  Shard {shard_id} "
            f"-> Circuit {circuit_id} "
            f"(Guard {circuit['guard']} -> "
            f"Middle {circuit['middle']} -> "
            f"Exit {circuit['exit']})"
        )

    print()

    # -----------------------------------------
    # Start transfer
    # -----------------------------------------

    start_time = time.perf_counter()

    tasks = []

    for filename in shard_files:

        shard_path = os.path.join(
            INPUT_DIR,
            filename
        )

        task = asyncio.create_task(
            send_shard(
                shard_path,
                topology,
                shard_to_circuit
            )
        )

        tasks.append(
            task
        )

    # -----------------------------------------
    # Wait for all shards
    # -----------------------------------------

    results = await asyncio.gather(
        *tasks
    )

    elapsed = (
        time.perf_counter()
        - start_time
    )

    successful = sum(
        results
    )

    failed = (
        num_shards
        - successful
    )

    # -----------------------------------------
    # Transfer summary
    # -----------------------------------------

    print()

    print(
        "======================================"
    )

    print(
        "          TRANSFER COMPLETE"
    )

    print(
        "======================================"
    )

    print(
        f"Total shards : {num_shards}"
    )

    print(
        f"Successful   : {successful}"
    )

    print(
        f"Failed       : {failed}"
    )

    print(
        f"Total time   : {elapsed:.4f}s"
    )

    print(
        "======================================"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )