# ============================================================
# PHASE 5 DYNAMIC TOPOLOGY
# ============================================================

BASE_GUARD_PORT = 9100

BASE_MIDDLE_PORT = 9200

BASE_EXIT_PORT = 9300


def create_topology(
    num_circuits
):

    topology = {}

    for circuit_id in range(
        num_circuits
    ):

        topology[circuit_id] = {

            "guard":
                BASE_GUARD_PORT
                + circuit_id,

            "middle":
                BASE_MIDDLE_PORT
                + circuit_id,

            "exit":
                BASE_EXIT_PORT
                + circuit_id
        }

    return topology