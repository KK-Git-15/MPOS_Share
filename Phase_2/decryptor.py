import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


NONCE_SIZE = 12


def decrypt_shard(
    encrypted_path,
    key_path,
    output_path
):
    # Read encrypted data
    with open(encrypted_path, "rb") as f:
        encrypted_data = f.read()

    # Read key
    with open(key_path, "rb") as f:
        key = f.read()

    # Basic validation
    if len(key) != 32:
        raise ValueError(
            "Invalid AES-256 key. Expected 32 bytes."
        )

    if len(encrypted_data) <= NONCE_SIZE:
        raise ValueError(
            "Encrypted file is too small."
        )

    # Extract nonce
    nonce = encrypted_data[:NONCE_SIZE]

    # Remaining data = ciphertext + GCM authentication tag
    ciphertext = encrypted_data[NONCE_SIZE:]

    # Create AES-GCM cipher
    aesgcm = AESGCM(key)

    # Decrypt and authenticate
    plaintext = aesgcm.decrypt(
        nonce,
        ciphertext,
        None
    )

    # Write decrypted shard
    with open(output_path, "wb") as f:
        f.write(plaintext)

    print(f"Decrypted: {encrypted_path}")
    print(f"        → {output_path}")


def decrypt_all_shards(
    encrypted_dir,
    key_dir,
    output_dir,
    n
):
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    decrypted_shards = []

    for shard_id in range(n):

        encrypted_path = os.path.join(
            encrypted_dir,
            f"encrypted_shard_{shard_id:02d}.bin"
        )

        key_path = os.path.join(
            key_dir,
            f"key_{shard_id:02d}.bin"
        )

        output_path = os.path.join(
            output_dir,
            f"shard_{shard_id:02d}.bin"
        )

        # Check encrypted shard exists
        if not os.path.exists(encrypted_path):
            print(
                f"WARNING: Missing encrypted shard "
                f"{shard_id}"
            )
            continue

        # Check corresponding key exists
        if not os.path.exists(key_path):
            print(
                f"WARNING: Missing key for shard "
                f"{shard_id}"
            )
            continue

        decrypt_shard(
            encrypted_path=encrypted_path,
            key_path=key_path,
            output_path=output_path
        )

        decrypted_shards.append(shard_id)

    print()
    print("=== Decryption Summary ===")
    print(f"Total shards     : {n}")
    print(f"Decrypted shards : {len(decrypted_shards)}")
    print(f"Shard IDs        : {decrypted_shards}")

    return decrypted_shards


if __name__ == "__main__":

    decrypt_all_shards(
        encrypted_dir=os.path.join(PROJECT_ROOT, "encrypted_shards"),
        key_dir=os.path.join(PROJECT_ROOT, "keys"),
        output_dir=os.path.join(PROJECT_ROOT, "decrypted_shards"),
        n=6
    )

    print()
    print("Decryption process completed!")