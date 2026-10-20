import asyncio
import os

from protocol import receive_packet

from onion import (
    unpack_final_payload
)


# ============================================================
# CONFIGURATION
# ============================================================

HOST = "127.0.0.1"

PORT = 9001


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


OUTPUT_DIR = os.path.join(
    PROJECT_ROOT,
    "received_shards"
)


# ============================================================
# HANDLE CONNECTION
# ============================================================

async def handle_client(
    reader,
    writer
):

    peer = writer.get_extra_info(
        "peername"
    )

    try:

        # ----------------------------------------------------
        # Receive final packet
        # ----------------------------------------------------

        metadata, onion_payload = (
            await receive_packet(
                reader
            )
        )

        circuit_id = metadata.get(
            "circuit_id"
        )

        print(
            f"[SERVER] "
            f"Final onion payload received "
            f"from Circuit "
            f"{circuit_id}"
        )

        # ----------------------------------------------------
        # Recover Phase 2 encrypted shard
        # ----------------------------------------------------

        shard_metadata, payload = (
            unpack_final_payload(
                onion_payload
            )
        )

        shard_id = shard_metadata[
            "shard_id"
        ]

        print(
            f"[SERVER] "
            f"Recovered shard "
            f"{shard_id}"
        )

        print(
            f"[SERVER] "
            f"Connection: {peer}"
        )

        # ----------------------------------------------------
        # Create output directory
        # ----------------------------------------------------

        os.makedirs(
            OUTPUT_DIR,
            exist_ok=True
        )

        # ----------------------------------------------------
        # Save encrypted shard
        # ----------------------------------------------------

        output_path = os.path.join(
            OUTPUT_DIR,
            f"encrypted_shard_"
            f"{shard_id:02d}.bin"
        )

        with open(
            output_path,
            "wb"
        ) as f:

            f.write(
                payload
            )

        print(
            f"[SERVER] "
            f"Saved shard "
            f"{shard_id} "
            f"({len(payload)} bytes)"
        )

        print()

    except asyncio.IncompleteReadError:

        print(
            "[SERVER] "
            "Incomplete packet received"
        )

    except Exception as e:

        print(
            f"[SERVER] "
            f"Error: {e}"
        )

    finally:

        writer.close()

        try:

            await writer.wait_closed()

        except Exception:

            pass


# ============================================================
# SERVER
# ============================================================

async def main():

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    server = await asyncio.start_server(
        handle_client,
        HOST,
        PORT
    )

    print(
        "======================================"
    )

    print(
        "          MPOS PHASE 5 SERVER"
    )

    print(
        "======================================"
    )

    print(
        f"Listening on "
        f"{HOST}:{PORT}"
    )

    print(
        f"Output directory:"
    )

    print(
        OUTPUT_DIR
    )

    print()

    print(
        "Waiting for onion circuits..."
    )

    print()

    async with server:

        await server.serve_forever()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )