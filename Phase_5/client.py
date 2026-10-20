import asyncio
import os
import random
import time

from protocol import send_packet

from topology import (
    create_topology
)

from onion import (
    build_onion
)


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


INPUT_DIR = os.path.join(
    PROJECT_ROOT,
    "encrypted_shards"
)


HOST = "127.0.0.1"


# ============================================================
# FIND ENCRYPTED SHARDS
# ============================================================

def get_shard_files():

    if not os.path.isdir(
        INPUT_DIR
    ):

        raise FileNotFoundError(
            f"Encrypted shard directory "
            f"not found:\n"
            f"{INPUT_DIR}"
        )

    files = [

        filename

        for filename in os.listdir(
            INPUT_DIR
        )

        if filename.startswith(
            "encrypted_shard_"
        )

        and filename.endswith(
            ".bin"
        )
    ]

    return sorted(
        files
    )


# ============================================================
# EXTRACT SHARD ID
# ============================================================

def get_shard_id(
    filename
):

    return int(
        filename
        .split("_")[2]
        .split(".")[0]
    )


# ============================================================
# RANDOM SHARD → CIRCUIT
# ============================================================

def create_random_assignment(
    shard_files,
    topology
):

    shard_ids = [

        get_shard_id(
            filename
        )

        for filename in shard_files
    ]

    circuit_ids = list(
        topology.keys()
    )

    if len(shard_ids) != len(
        circuit_ids
    ):

        raise ValueError(
            "Number of shards and "
            "circuits must be equal."
        )

    random.shuffle(
        circuit_ids
    )

    return dict(
        zip(
            shard_ids,
            circuit_ids
        )
    )


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

    circuit_id = (
        shard_to_circuit[
            shard_id
        ]
    )

    circuit = topology[
        circuit_id
    ]

    guard_port = circuit[
        "guard"
    ]

    # --------------------------------------------------------
    # Read Phase 2 encrypted shard
    # --------------------------------------------------------

    with open(
        shard_path,
        "rb"
    ) as f:

        payload = f.read()

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    shard_metadata = {

        "type":
            "ENCRYPTED_SHARD",

        "shard_id":
            shard_id,

        "circuit_id":
            circuit_id,

        "payload_size":
            len(payload)
    }

    # --------------------------------------------------------
    # Build onion
    # --------------------------------------------------------

    onion_payload = build_onion(
        circuit_id,
        shard_metadata,
        payload
    )

    outer_metadata = {

        "type":
            "ONION",

        "circuit_id":
            circuit_id,

        "onion_size":
            len(onion_payload)
    }

    print(
        f"[CLIENT] "
        f"Shard {shard_id} "
        f"-> Circuit {circuit_id} "
        f"-> Guard {guard_port}"
    )

    print(
        f"[CLIENT] "
        f"Layers: "
        f"GUARD -> MIDDLE -> EXIT"
    )

    start_time = time.perf_counter()

    try:

        reader, writer = (
            await asyncio.open_connection(
                HOST,
                guard_port
            )
        )

        await send_packet(
            writer,
            outer_metadata,
            onion_payload
        )

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
            f"{circuit_id}: "
            f"{e}"
        )

        return False


# ============================================================
# MAIN
# ============================================================

async def main():

    shard_files = (
        get_shard_files()
    )

    if not shard_files:

        raise ValueError(
            "No encrypted shards found."
        )

    num_shards = len(
        shard_files
    )

    topology = create_topology(
        num_shards
    )

    shard_to_circuit = (
        create_random_assignment(
            shard_files,
            topology
        )
    )

    print()

    print(
        "======================================"
    )

    print(
        "          MPOS PHASE 5 CLIENT"
    )

    print(
        "======================================"
    )

    print(
        f"Detected shards   : "
        f"{num_shards}"
    )

    print(
        f"Available circuits: "
        f"{num_shards}"
    )

    print(
        "Encryption        : "
        "AES-256-GCM onion layers"
    )

    print()

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
            f"(Guard "
            f"{circuit['guard']} -> "
            f"Middle "
            f"{circuit['middle']} -> "
            f"Exit "
            f"{circuit['exit']})"
        )

    print()

    start_time = (
        time.perf_counter()
    )

    tasks = []

    for filename in shard_files:

        shard_path = os.path.join(
            INPUT_DIR,
            filename
        )

        tasks.append(
            asyncio.create_task(
                send_shard(
                    shard_path,
                    topology,
                    shard_to_circuit
                )
            )
        )

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
        f"Total shards : "
        f"{num_shards}"
    )

    print(
        f"Successful   : "
        f"{successful}"
    )

    print(
        f"Failed       : "
        f"{failed}"
    )

    print(
        f"Total time   : "
        f"{elapsed:.4f}s"
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