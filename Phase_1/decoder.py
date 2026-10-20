import os
import random
from zfec.easyfec import Decoder


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


def read_metadata(metadata_path):
    metadata = {}

    with open(metadata_path, "r") as f:
        for line in f:
            key, value = line.strip().split("=")
            metadata[key] = int(value)

    return metadata


def decode_file(shard_dir, output_file):

    # --------------------------------
    # Read metadata
    # --------------------------------
    metadata_path = os.path.join(
        shard_dir,
        "metadata.txt"
    )

    if not os.path.exists(metadata_path):
        raise FileNotFoundError(
            f"Metadata file not found: {metadata_path}"
        )

    metadata = read_metadata(metadata_path)

    k = metadata["k"]
    n = metadata["n"]
    original_size = metadata["original_size"]
    padlen = metadata["padlen"]

    # --------------------------------
    # Find available shards
    # --------------------------------
    available_shards = []

    for shard_id in range(n):

        shard_path = os.path.join(
            shard_dir,
            f"shard_{shard_id:02d}.bin"
        )

        if os.path.exists(shard_path):
            available_shards.append(shard_id)

    print("=== Shard Availability ===")
    print(f"Required shards : {k}")
    print(f"Total shards    : {n}")
    print(f"Available       : {len(available_shards)}")
    print(f"Shard IDs       : {available_shards}")
    print()

    # --------------------------------
    # Check if enough shards exist
    # --------------------------------
    if len(available_shards) < k:

        print("=== Reed-Solomon Decoding ===")
        print("ERROR: Not enough shards available!")
        print(
            f"Required: {k} shards, "
            f"but only {len(available_shards)} found."
        )
        print("Reconstruction cannot be performed.")

        return None

    # --------------------------------
    # Randomly select K available shards
    # --------------------------------
    shard_ids = random.sample(
        available_shards,
        k
    )

    print(f"Randomly selected shards: {shard_ids}")
    print()

    # --------------------------------
    # Read selected shards
    # --------------------------------
    shards = []

    for shard_id in shard_ids:

        shard_path = os.path.join(
            shard_dir,
            f"shard_{shard_id:02d}.bin"
        )

        with open(shard_path, "rb") as f:
            shards.append(f.read())

    # --------------------------------
    # Create Reed-Solomon decoder
    # --------------------------------
    decoder = Decoder(k, n)

    # --------------------------------
    # Decode
    # --------------------------------
    reconstructed_data = decoder.decode(
        shards,
        shard_ids,
        padlen
    )

    # Remove any remaining padding.
    reconstructed_data = reconstructed_data[:original_size]

    # --------------------------------
    # Write reconstructed file
    # --------------------------------
    output_directory = os.path.dirname(output_file)

    if output_directory:
        os.makedirs(
            output_directory,
            exist_ok=True
        )

    with open(output_file, "wb") as f:
        f.write(reconstructed_data)

    # --------------------------------
    # Display result
    # --------------------------------
    print("=== Reed-Solomon Decoding ===")
    print(f"K            : {k}")
    print(f"N            : {n}")
    print(f"Received     : {shard_ids}")
    print(f"Pad length   : {padlen} bytes")
    print(f"Output       : {output_file}")
    print(f"Output size  : {len(reconstructed_data)} bytes")
    print("Reconstruction successful!")

    return output_file


if __name__ == "__main__":

    shard_dir = os.path.join(PROJECT_ROOT, "shards")

    decode_file(
        shard_dir=shard_dir,
        output_file=os.path.join(PROJECT_ROOT, "reconstructed.txt")
    )