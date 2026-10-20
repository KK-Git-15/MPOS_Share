import base64
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ============================================================
# PHASE 5 ONION CONFIGURATION
# ============================================================

KEY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "onion_keys.json"
)

NONCE_SIZE = 12
KEY_SIZE = 32


# ============================================================
# KEY MANAGEMENT
# ============================================================

def _new_key():
    return base64.b64encode(
        secrets.token_bytes(KEY_SIZE)
    ).decode("ascii")


def create_keys(num_circuits):
    """
    Create one independent AES-256-GCM key
    for Guard, Middle and Exit of every circuit.
    """

    keys = {}

    for circuit_id in range(num_circuits):

        keys[str(circuit_id)] = {
            "guard": _new_key(),
            "middle": _new_key(),
            "exit": _new_key()
        }

    with open(KEY_FILE, "w") as f:
        json.dump(
            keys,
            f,
            indent=4
        )

    return keys


def load_keys():

    if not os.path.isfile(KEY_FILE):

        raise FileNotFoundError(
            f"Onion key file not found:\n"
            f"{KEY_FILE}\n"
            f"Run launcher.py first."
        )

    with open(KEY_FILE, "r") as f:
        return json.load(f)


def get_key(
    circuit_id,
    role
):

    keys = load_keys()

    circuit = keys[
        str(circuit_id)
    ]

    if role not in circuit:

        raise ValueError(
            f"Unknown onion role: {role}"
        )

    return base64.b64decode(
        circuit[role]
    )


# ============================================================
# AES-256-GCM ONION LAYER
# ============================================================

def encrypt_layer(
    data,
    key
):
    """
    Encrypt one onion layer.

    Format:

        12-byte nonce
        +
        AES-GCM ciphertext + authentication tag
    """

    nonce = secrets.token_bytes(
        NONCE_SIZE
    )

    aes = AESGCM(key)

    ciphertext = aes.encrypt(
        nonce,
        data,
        None
    )

    return (
        nonce
        +
        ciphertext
    )


def decrypt_layer(
    data,
    key
):

    if len(data) < (
        NONCE_SIZE + 16
    ):

        raise ValueError(
            "Invalid onion layer."
        )

    nonce = data[
        :NONCE_SIZE
    ]

    ciphertext = data[
        NONCE_SIZE:
    ]

    aes = AESGCM(key)

    return aes.decrypt(
        nonce,
        ciphertext,
        None
    )


# ============================================================
# BUILD ONION
# ============================================================

def build_onion(
    circuit_id,
    metadata,
    payload
):
    """
    Build the three-layer onion.

    Original payload:

        Phase 2 encrypted shard

    Encryption order:

        Exit
        Middle
        Guard

    Result:

        Guard(
            Middle(
                Exit(
                    shard
                )
            )
        )
    """

    inner = json.dumps(
        {
            "metadata": metadata,
            "payload": base64.b64encode(
                payload
            ).decode("ascii")
        },
        separators=(
            ",",
            ":"
        )
    ).encode("utf-8")

    # --------------------------------------------------------
    # Layer 1: Exit
    # --------------------------------------------------------

    inner = encrypt_layer(
        inner,
        get_key(
            circuit_id,
            "exit"
        )
    )

    # --------------------------------------------------------
    # Layer 2: Middle
    # --------------------------------------------------------

    inner = encrypt_layer(
        inner,
        get_key(
            circuit_id,
            "middle"
        )
    )

    # --------------------------------------------------------
    # Layer 3: Guard
    # --------------------------------------------------------

    inner = encrypt_layer(
        inner,
        get_key(
            circuit_id,
            "guard"
        )
    )

    return inner


# ============================================================
# REMOVE ONE ONION LAYER
# ============================================================

def remove_layer(
    circuit_id,
    role,
    payload
):

    return decrypt_layer(
        payload,
        get_key(
            circuit_id,
            role
        )
    )


# ============================================================
# UNPACK FINAL PAYLOAD
# ============================================================

def unpack_final_payload(
    data
):

    packet = json.loads(
        data.decode("utf-8")
    )

    metadata = packet[
        "metadata"
    ]

    payload = base64.b64decode(
        packet["payload"]
    )

    return (
        metadata,
        payload
    )