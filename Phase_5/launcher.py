import os
import subprocess
import sys
import time

from onion import create_keys


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


PHASE_5_DIR = os.path.join(
    PROJECT_ROOT,
    "Phase_5"
)


INPUT_DIR = os.path.join(
    PROJECT_ROOT,
    "encrypted_shards"
)


RELAY_PATH = os.path.join(
    PHASE_5_DIR,
    "relay.py"
)


# ============================================================
# NETWORK CONFIGURATION
# ============================================================

SERVER_HOST = "127.0.0.1"

SERVER_PORT = 9001

BASE_GUARD_PORT = 9100

BASE_MIDDLE_PORT = 9200

BASE_EXIT_PORT = 9300


# ============================================================
# DETECT SHARDS
# ============================================================

def detect_num_shards():

    if not os.path.isdir(
        INPUT_DIR
    ):

        raise FileNotFoundError(
            f"Directory not found:\n"
            f"{INPUT_DIR}"
        )

    shard_files = [

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

    if not shard_files:

        raise ValueError(
            f"No encrypted shards found "
            f"in:\n{INPUT_DIR}"
        )

    return len(
        shard_files
    )


# ============================================================
# START ONE RELAY
# ============================================================

def start_relay(
    relay_name,
    role,
    circuit_id,
    listen_port,
    next_host,
    next_port
):

    return subprocess.Popen(
        [

            sys.executable,

            RELAY_PATH,

            relay_name,

            role,

            str(circuit_id),

            str(listen_port),

            next_host,

            str(next_port)
        ]
    )


# ============================================================
# START ALL RELAYS
# ============================================================

def start_all_relays(
    num_shards
):

    processes = []

    print()

    print(
        "======================================"
    )

    print(
        "          MPOS PHASE 5 LAUNCHER"
    )

    print(
        "======================================"
    )

    print(
        f"Project root      : "
        f"{PROJECT_ROOT}"
    )

    print(
        f"Encrypted shards  : "
        f"{INPUT_DIR}"
    )

    print(
        f"Number of shards  : "
        f"{num_shards}"
    )

    print(
        f"Number of circuits: "
        f"{num_shards}"
    )

    print(
        f"Total relays      : "
        f"{num_shards * 3}"
    )

    print()

    # --------------------------------------------------------
    # Generate onion keys
    # --------------------------------------------------------

    create_keys(
        num_shards
    )

    print(
        "Generated AES-256-GCM "
        "onion keys."
    )

    print()

    # ========================================================
    # CREATE CIRCUITS
    # ========================================================

    for circuit_id in range(
        num_shards
    ):

        guard_port = (
            BASE_GUARD_PORT
            + circuit_id
        )

        middle_port = (
            BASE_MIDDLE_PORT
            + circuit_id
        )

        exit_port = (
            BASE_EXIT_PORT
            + circuit_id
        )

        # ----------------------------------------------------
        # EXIT
        # ----------------------------------------------------

        exit_relay = start_relay(
            f"Exit-{circuit_id}",
            "exit",
            circuit_id,
            exit_port,
            SERVER_HOST,
            SERVER_PORT
        )

        time.sleep(
            0.1
        )

        # ----------------------------------------------------
        # MIDDLE
        # ----------------------------------------------------

        middle = start_relay(
            f"Middle-{circuit_id}",
            "middle",
            circuit_id,
            middle_port,
            SERVER_HOST,
            exit_port
        )

        time.sleep(
            0.1
        )

        # ----------------------------------------------------
        # GUARD
        # ----------------------------------------------------

        guard = start_relay(
            f"Guard-{circuit_id}",
            "guard",
            circuit_id,
            guard_port,
            SERVER_HOST,
            middle_port
        )

        time.sleep(
            0.1
        )

        processes.extend(
            [
                guard,
                middle,
                exit_relay
            ]
        )

        print(
            f"Circuit {circuit_id}: "
            f"Guard {guard_port} -> "
            f"Middle {middle_port} -> "
            f"Exit {exit_port} -> "
            f"Server {SERVER_PORT}"
        )

    print()

    print(
        f"Started "
        f"{len(processes)} "
        f"relay processes."
    )

    return processes


# ============================================================
# STOP RELAYS
# ============================================================

def stop_relays(
    processes
):

    print()

    print(
        "Stopping all relay processes..."
    )

    for process in processes:

        if process.poll() is None:

            process.terminate()

    for process in processes:

        try:

            process.wait(
                timeout=2
            )

        except subprocess.TimeoutExpired:

            process.kill()

    print(
        "All relay processes stopped."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    processes = []

    try:

        num_shards = (
            detect_num_shards()
        )

        processes = (
            start_all_relays(
                num_shards
            )
        )

        print()

        print(
            "All Phase 5 onion relay "
            "circuits are running."
        )

        print(
            "Press Ctrl+C to stop Phase 5."
        )

        print()

        while True:

            for process in processes:

                if (
                    process.poll()
                    is not None
                ):

                    print(
                        "Warning: "
                        "A relay process "
                        "has stopped."
                    )

            time.sleep(
                1
            )

    except KeyboardInterrupt:

        print()

        print(
            "Ctrl+C received."
        )

    except Exception as e:

        print()

        print(
            f"Launcher error: "
            f"{e}"
        )

    finally:

        if processes:

            stop_relays(
                processes
            )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()