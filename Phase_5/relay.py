import asyncio
import sys

from protocol import (
    receive_packet,
    send_packet
)

from onion import remove_layer


HOST = "127.0.0.1"


# ============================================================
# RELAY
# ============================================================

class Relay:

    def __init__(
        self,
        relay_name,
        relay_role,
        circuit_id,
        listen_port,
        next_host,
        next_port
    ):

        self.relay_name = relay_name

        self.relay_role = relay_role

        self.circuit_id = circuit_id

        self.listen_port = listen_port

        self.next_host = next_host

        self.next_port = next_port


    # ========================================================
    # HANDLE CONNECTION
    # ========================================================

    async def handle_client(
        self,
        reader,
        writer
    ):

        peer = writer.get_extra_info(
            "peername"
        )

        try:

            # ------------------------------------------------
            # Receive packet
            # ------------------------------------------------

            metadata, payload = (
                await receive_packet(
                    reader
                )
            )

            circuit_id = metadata.get(
                "circuit_id"
            )

            if circuit_id != self.circuit_id:

                raise ValueError(
                    f"Expected circuit "
                    f"{self.circuit_id}, "
                    f"received circuit "
                    f"{circuit_id}"
                )

            print(
                f"[{self.relay_name}] "
                f"Received onion packet "
                f"from {peer}"
            )

            print(
                f"[{self.relay_name}] "
                f"Removing "
                f"{self.relay_role.upper()} "
                f"onion layer"
            )

            # ------------------------------------------------
            # Remove THIS relay's layer only
            # ------------------------------------------------

            inner_payload = remove_layer(
                self.circuit_id,
                self.relay_role,
                payload
            )

            # ------------------------------------------------
            # Connect to next relay
            # ------------------------------------------------

            next_reader, next_writer = (
                await asyncio.open_connection(
                    self.next_host,
                    self.next_port
                )
            )

            next_metadata = {

                "type": "ONION",

                "circuit_id":
                    self.circuit_id,

                "hop":
                    self.relay_role
            }

            # ------------------------------------------------
            # Forward remaining onion
            # ------------------------------------------------

            await send_packet(
                next_writer,
                next_metadata,
                inner_payload
            )

            next_writer.close()

            await next_writer.wait_closed()

            print(
                f"[{self.relay_name}] "
                f"Layer removed successfully"
            )

            print(
                f"[{self.relay_name}] "
                f"Forwarded to "
                f"{self.next_host}:"
                f"{self.next_port}"
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
                f"Next hop unavailable: "
                f"{self.next_host}:"
                f"{self.next_port}"
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


    # ========================================================
    # START RELAY
    # ========================================================

    async def start(self):

        server = await asyncio.start_server(
            self.handle_client,
            HOST,
            self.listen_port
        )

        print(
            f"[{self.relay_name}] "
            f"Listening on "
            f"{HOST}:"
            f"{self.listen_port}"
        )

        print(
            f"[{self.relay_name}] "
            f"Role: "
            f"{self.relay_role.upper()}"
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


# ============================================================
# MAIN
# ============================================================

async def main():

    if len(sys.argv) != 7:

        print(
            "Usage:"
        )

        print(
            "python3 relay.py "
            "<relay_name> "
            "<role> "
            "<circuit_id> "
            "<listen_port> "
            "<next_host> "
            "<next_port>"
        )

        sys.exit(1)

    relay_name = sys.argv[1]

    relay_role = sys.argv[2]

    circuit_id = int(
        sys.argv[3]
    )

    listen_port = int(
        sys.argv[4]
    )

    next_host = sys.argv[5]

    next_port = int(
        sys.argv[6]
    )

    if relay_role not in (
        "guard",
        "middle",
        "exit"
    ):

        raise ValueError(
            "Role must be "
            "guard, middle, or exit."
        )

    relay = Relay(
        relay_name,
        relay_role,
        circuit_id,
        listen_port,
        next_host,
        next_port
    )

    await relay.start()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )