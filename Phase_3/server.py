import asyncio
import os

from protocol import receive_packet


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


HOST = "127.0.0.1"
PORT = 9000

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "received_shards")


async def handle_client(reader, writer):
    """
    Handle one TCP connection.

    One connection carries one encrypted shard.
    """

    address = writer.get_extra_info("peername")

    try:
        metadata, payload = await receive_packet(reader)

        shard_id = metadata["shard_id"]

        print(
            f"[SERVER] Received shard {shard_id} "
            f"from {address}"
        )

        print(
            f"[SERVER] Connection: {address}"
        )

        os.makedirs(
            OUTPUT_DIR,
            exist_ok=True
        )

        output_path = os.path.join(
            OUTPUT_DIR,
            f"encrypted_shard_{shard_id:02d}.bin"
        )

        with open(output_path, "wb") as f:
            f.write(payload)

        print(
            f"[SERVER] Saved shard {shard_id} "
            f"({len(payload)} bytes)"
        )

    except asyncio.IncompleteReadError:
        print(
            f"[SERVER] Connection closed before "
            f"complete packet was received: {address}"
        )

    except Exception as e:
        print(
            f"[SERVER] Error handling {address}: {e}"
        )

    finally:
        writer.close()
        await writer.wait_closed()


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

    addresses = ", ".join(
        str(sock.getsockname())
        for sock in server.sockets
    )

    print("===================================")
    print("       MPOS PHASE 3 SERVER")
    print("===================================")
    print(f"Listening on: {addresses}")
    print()
    print("Waiting for encrypted shards...")
    print()

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())