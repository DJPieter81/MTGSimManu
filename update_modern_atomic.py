#!/usr/bin/env python3
"""
Download the latest ModernAtomic.json from MTGJSON and split into 8 parts.

Usage:
    python update_modern_atomic.py

This script:
1. Downloads ModernAtomic.json.gz from mtgjson.com
2. Decompresses it
3. Splits the data into 8 roughly equal part files (ModernAtomic_part1..8.json)
4. Reassembles and verifies the result matches the original
5. Removes the full ModernAtomic.json (it's in .gitignore; rebuilt from parts)

Run this whenever you want to update to the latest MTGJSON card data.
"""

import gzip
import json
import math
import os
import sys
import urllib.request
import tempfile

MTGJSON_URL = "https://mtgjson.com/api/v5/ModernAtomic.json.gz"
NUM_PARTS = 8
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def download(url: str, dest: str) -> None:
    """Download a file with progress indication."""
    print(f"Downloading {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "MTGSimManu-updater/1.0"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as out:
        total = resp.headers.get("Content-Length")
        downloaded = 0
        block = 1 << 16  # 64 KB
        while True:
            chunk = resp.read(block)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 / int(total)
                print(f"\r  {downloaded / 1024 / 1024:.1f} MB / {int(total) / 1024 / 1024:.1f} MB ({pct:.0f}%)", end="", flush=True)
            else:
                print(f"\r  {downloaded / 1024 / 1024:.1f} MB", end="", flush=True)
    print()


def decompress_gz(gz_path: str, out_path: str) -> None:
    """Decompress a .gz file."""
    print(f"Decompressing to {os.path.basename(out_path)} ...")
    with gzip.open(gz_path, "rb") as f_in, open(out_path, "wb") as f_out:
        while True:
            chunk = f_in.read(1 << 16)
            if not chunk:
                break
            f_out.write(chunk)


def load_atomic(path: str) -> dict:
    """Load the full ModernAtomic.json and return (meta, data)."""
    print(f"Loading {os.path.basename(path)} ...")
    with open(path, "r") as f:
        raw = json.load(f)
    meta = raw.get("meta", {})
    data = raw.get("data", {})
    print(f"  Version: {meta.get('version', 'unknown')}")
    print(f"  Date:    {meta.get('date', 'unknown')}")
    print(f"  Cards:   {len(data)}")
    return meta, data


def split_data(meta: dict, data: dict, num_parts: int) -> list[dict]:
    """Split card data dict into num_parts roughly equal chunks."""
    keys = sorted(data.keys())
    chunk_size = math.ceil(len(keys) / num_parts)
    parts = []
    for i in range(num_parts):
        start = i * chunk_size
        end = min(start + chunk_size, len(keys))
        chunk_keys = keys[start:end]
        chunk_data = {k: data[k] for k in chunk_keys}
        parts.append({"meta": meta, "data": chunk_data})
    return parts


def write_parts(parts: list[dict]) -> None:
    """Write part files to project root."""
    for i, part in enumerate(parts, 1):
        path = os.path.join(PROJECT_ROOT, f"ModernAtomic_part{i}.json")
        print(f"  Writing part {i}: {len(part['data'])} cards ...", end=" ")
        with open(path, "w") as f:
            json.dump(part, f)
        size_mb = os.path.getsize(path) / 1024 / 1024
        print(f"({size_mb:.1f} MB)")


def verify_parts(meta: dict, original_count: int) -> bool:
    """Reassemble from parts and verify card count matches."""
    print("Verifying reassembly ...")
    merged = {}
    for i in range(1, NUM_PARTS + 1):
        path = os.path.join(PROJECT_ROOT, f"ModernAtomic_part{i}.json")
        with open(path, "r") as f:
            part = json.load(f)
        merged.update(part["data"])
    if len(merged) == original_count:
        print(f"  OK: {len(merged)} cards reassembled correctly.")
        return True
    else:
        print(f"  MISMATCH: expected {original_count}, got {len(merged)}")
        return False


def main():
    # Step 1: Download
    gz_path = os.path.join(PROJECT_ROOT, "ModernAtomic.json.gz")
    json_path = os.path.join(PROJECT_ROOT, "ModernAtomic.json")

    try:
        download(MTGJSON_URL, gz_path)
    except Exception as e:
        print(f"\nDownload failed: {e}")
        print("Make sure you have internet access and mtgjson.com is reachable.")
        sys.exit(1)

    # Step 2: Decompress
    decompress_gz(gz_path, json_path)
    os.remove(gz_path)

    # Step 3: Load and inspect
    meta, data = load_atomic(json_path)

    # Step 4: Split into parts
    print(f"\nSplitting into {NUM_PARTS} parts ...")
    parts = split_data(meta, data, NUM_PARTS)
    write_parts(parts)

    # Step 5: Verify
    if not verify_parts(meta, len(data)):
        print("\nERROR: Verification failed! Part files may be corrupt.")
        sys.exit(1)

    # Step 6: Clean up full file (it's .gitignored; rebuilt from parts at runtime)
    os.remove(json_path)
    print(f"\nDone! Removed {os.path.basename(json_path)} (rebuild with:")
    print('  python3 -c "')
    print("  import json")
    print("  merged = {}")
    print("  for i in range(1, 9):")
    print("      with open(f'ModernAtomic_part{i}.json') as f:")
    print("          merged.update(json.load(f)['data'])")
    print("  with open('ModernAtomic.json', 'w') as f:")
    print("      json.dump({'meta': {}, 'data': merged}, f)")
    print('  "')
    print(f"\nCommit the updated ModernAtomic_part*.json files to git.")


if __name__ == "__main__":
    main()
