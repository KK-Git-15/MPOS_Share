import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


NONCE_SIZE = 12
KEY_SIZE = 32


def encrypt_shard(shard_path, output_path, key_path):
    """
    Encrypt one shard using AES-256-GCM.

    File format:

        [12-byte nonce][ciphertext + 16-byte GCM tag]
    """

    # Read shard
    with open(shard_path, "rb") as f:
        plaintext = f.read()

    # Generate a unique 256-bit key for this shard
    key = AESGCM.generate_key(bit_length=256)

    # Generate a fresh 96-bit nonce
    nonce = os.urandom(NONCE_SIZE)

    # Create AES-GCM cipher
    aesgcm = AESGCM(key)

    # Encrypt
    ciphertext = aesgcm.encrypt(
        nonce,
        plaintext,
        None
    )

    # Store nonce + ciphertext + authentication tag
    encrypted_data = nonce + ciphertext

    with open(output_path, "wb") as f:
        f.write(encrypted_data)

    # Store key separately for the Phase 2 prototype
    with open(key_path, "wb") as f:
        f.write(key)

    print(f"Encrypted: {shard_path}")
    print(f"          → {output_path}")


def encrypt_all_shards(
    shard_dir,
    output_dir,
    key_dir
):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(key_dir, exist_ok=True)

    shard_files = sorted(
        filename
        for filename in os.listdir(shard_dir)
        if filename.startswith("shard_")
        and filename.endswith(".bin")
    )

    if not shard_files:
        raise ValueError("No shard files found")

    print("=== AES-256-GCM Encryption ===")
    print(f"Shards found: {len(shard_files)}")
    print()

    for shard_file in shard_files:

        shard_path = os.path.join(
            shard_dir,
            shard_file
        )

        # shard_00.bin → 00
        shard_id = shard_file.split("_")[1].split(".")[0]

        encrypted_path = os.path.join(
            output_dir,
            f"encrypted_shard_{shard_id}.bin"
        )

        key_path = os.path.join(
            key_dir,
            f"key_{shard_id}.bin"
        )

        encrypt_shard(
            shard_path,
            encrypted_path,
            key_path
        )

    print()
    print("Encryption complete.")


if __name__ == "__main__":

    encrypt_all_shards(
        shard_dir=os.path.join(PROJECT_ROOT, "shards"),
        output_dir=os.path.join(PROJECT_ROOT, "encrypted_shards"),
        key_dir=os.path.join(PROJECT_ROOT, "keys")
    )