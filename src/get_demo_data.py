"""Download the processed 2026 tables so a fresh clone can run Bara without rebuilding the data.

No API key needed. Checks the SHA-256, then unpacks only the expected files into data/.

Usage:
    python src/get_demo_data.py
"""
from __future__ import annotations

import argparse
import hashlib
import tempfile
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ZIP_NAME = "bara-demo-data-2026.zip"
URL = f"https://github.com/ewenliam/bara-peat-fire/releases/download/demo-data-v1/{ZIP_NAME}"
SHA256 = "7a88c8d272a9f58f282ab4f01afcb7adce0207dafb540069c41850e9bee2d4df"  # printed by deploy/data_pack.py
MEMBERS = [
    "data/processed/samples_2026_2026_feat.parquet",
    "data/processed/ign_2026.parquet",
    "data/processed/places.parquet",
    "data/processed/gazetteer.parquet",
    "data/processed/live_2026.json",
    "data/raw/viirs-jpss1_2026_Indonesia.csv",
]
NOTICE = "data/DATA_NOTICE.txt"


def fetch(url: str, dest: Path) -> str:
    """Save url to dest and return its SHA-256."""
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=URL)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ZIP_NAME
        print(f"Downloading {args.url}")
        got = fetch(args.url, path)
        if got != SHA256:
            raise SystemExit(f"Checksum mismatch: expected {SHA256}, got {got}")
        with zipfile.ZipFile(path) as z:
            for name in MEMBERS + [NOTICE]:  # fixed names only: nothing in the zip chooses a path
                z.extract(name, ROOT)
    print(f"Unpacked {len(MEMBERS)} tables into {ROOT / 'data'}")


if __name__ == "__main__":
    main()
