import asyncio
import sys

from protocol import receive_packet, send_packet


HOST = "127.0.0.1"


class Relay:

    def __init__(
        self,
        relay_name,
        listen_port,
        next_host,
        next_port
    ):

        self.relay_name = relay_name
        self.listen_port = listen_port
        self.next_host = next_host
        self.next_port = next_port


    async def handle_client(
        self,
        reader,
        writer
    ):

        peer = writer.get_extra_info(
            "peername"
        )

        try:

            # ---------------------------------
            # Receive packet
            # ---------------------------------

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

            print(
                f"[{self.relay_name}] "
                f"Received shard {shard_id} "
                f"(Circuit {circuit_id}) "
                f"from {peer}"
            )

            # ---------------------------------
            # Connect to next relay
            # ---------------------------------

            next_reader, next_writer = (
                await asyncio.open_connection(
                    self.next_host,
                    self.next_port
                )
            )

            print(
                f"[{self.relay_name}] "
                f"Forwarding shard {shard_id} "
                f"to "
                f"{self.next_host}:"
                f"{self.next_port}"
            )

            # ---------------------------------
            # Forward unchanged
            # ---------------------------------

            await send_packet(
                next_writer,
                metadata,
                payload
            )

            # ---------------------------------
            # Close next connection
            # ---------------------------------

            next_writer.close()

            await next_writer.wait_closed()

            print(
                f"[{self.relay_name}] "
                f"Shard {shard_id} "
                f"forwarded successfully"
            )

            print()

        except asyncio.IncompleteReadError:

            print(
                f"[{self.relay_name}] "
                f"Incomplete packet received"
            )

        except ConnectionRefusedError:

            print(
                f"[{self.relay_name}] "
                f"Next hop "
                f"{self.next_host}:"
                f"{self.next_port} "
                f"is unavailable"
            )

        except Exception as e:

            print(
                f"[{self.relay_name}] "
                f"Error: {e}"
            )

        finally:

            writer.close()

            try:

                await writer.wait_closed()

            except Exception:

                pass


    async def start(self):

        server = await asyncio.start_server(
            self.handle_client,
            HOST,
            self.listen_port
        )

        print(
            f"[{self.relay_name}] "
            f"Listening on "
            f"{HOST}:{self.listen_port}"
        )

        print(
            f"[{self.relay_name}] "
            f"Next hop: "
            f"{self.next_host}:"
            f"{self.next_port}"
        )

        print()

        async with server:

            await server.serve_forever()


async def main():

    if len(sys.argv) != 5:

        print(
            "Usage:"
        )

        print(
            "python3 relay.py "
            "<relay_name> "
            "<listen_port> "
            "<next_host> "
            "<next_port>"
        )

        sys.exit(1)

    relay_name = sys.argv[1]

    listen_port = int(
        sys.argv[2]
    )

    next_host = sys.argv[3]

    next_port = int(
        sys.argv[4]
    )

    relay = Relay(
        relay_name,
        listen_port,
        next_host,
        next_port
    )

    await relay.start()


if __name__ == "__main__":

    asyncio.run(
        main()
    )