import asyncio
import os

from protocol import receive_packet


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
# HANDLE CLIENT / EXIT CONNECTION
# ============================================================

async def handle_client(
    reader,
    writer
):

    peer = writer.get_extra_info(
        "peername"
    )

    try:

        # -----------------------------------------
        # Receive packet
        # -----------------------------------------

        metadata, payload = (
            await receive_packet(
                reader
            )
        )

        shard_id = metadata[
            "shard_id"
        ]

        circuit_id = metadata[
            "circuit_id"
        ]

        # -----------------------------------------
        # Display information
        # -----------------------------------------

        print(
            f"[SERVER] "
            f"Received shard {shard_id} "
            f"from Circuit {circuit_id}"
        )

        print(
            f"[SERVER] "
            f"Connection: {peer}"
        )

        # -----------------------------------------
        # Create output directory
        # -----------------------------------------

        os.makedirs(
            OUTPUT_DIR,
            exist_ok=True
        )

        # -----------------------------------------
        # Save encrypted shard
        # -----------------------------------------

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
            f"Saved shard {shard_id} "
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
        "          MPOS PHASE 4 SERVER"
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
        f"{OUTPUT_DIR}"
    )

    print()

    print(
        "Waiting for relay circuits..."
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