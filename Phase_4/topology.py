SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9001

BASE_GUARD_PORT = 9100
BASE_MIDDLE_PORT = 9200
BASE_EXIT_PORT = 9300


def create_topology(num_shards):
    """
    Create one independent 3-hop circuit per shard.

    Circuit i:

        Guard i
           ↓
        Middle i
           ↓
        Exit i
           ↓
        Server
    """

    circuits = {}

    for circuit_id in range(num_shards):

        circuits[circuit_id] = {
            "guard": BASE_GUARD_PORT + circuit_id,
            "middle": BASE_MIDDLE_PORT + circuit_id,
            "exit": BASE_EXIT_PORT + circuit_id
        }

    return circuits