import os
from zfec.easyfec import Encoder


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


def encode_file(input_file, output_dir, k, n):
    if k < 1 or n < k:
        raise ValueError("Must satisfy 1 <= K <= N")

    os.makedirs(output_dir, exist_ok=True)

    with open(input_file, "rb") as f:
        data = f.read()

    if len(data) == 0:
        raise ValueError("Input file is empty")

    encoder = Encoder(k, n)

    # zfec internally pads the data so it can be divided into K blocks.
    # Calculate the padding required.
    block_size = (len(data) + k - 1) // k
    padded_size = block_size * k
    padlen = padded_size - len(data)

    # Encode the original data.
    shards = encoder.encode(data)

    shard_paths = []

    for i, shard in enumerate(shards):
        shard_path = os.path.join(
            output_dir,
            f"shard_{i:02d}.bin"
        )

        with open(shard_path, "wb") as f:
            f.write(shard)

        shard_paths.append(shard_path)

    # Store metadata required for decoding.
    metadata_path = os.path.join(
        output_dir,
        "metadata.txt"
    )

    with open(metadata_path, "w") as f:
        f.write(f"original_size={len(data)}\n")
        f.write(f"k={k}\n")
        f.write(f"n={n}\n")
        f.write(f"padlen={padlen}\n")

    print("=== Reed-Solomon Encoding ===")
    print(f"Input file   : {input_file}")
    print(f"Original size: {len(data)} bytes")
    print(f"K            : {k}")
    print(f"N            : {n}")
    print(f"Pad length   : {padlen} bytes")
    print(f"Shards       : {n}")
    print()

    for path in shard_paths:
        print(f"Generated: {path}")

    return shard_paths


if __name__ == "__main__":
    encode_file(
        input_file=os.path.join(PROJECT_ROOT, "Input", "Input.txt"),
        output_dir=os.path.join(PROJECT_ROOT, "shards"),
        k=4,
        n=6
    )