"""Build the 2026 demo data pack that src/get_demo_data.py downloads from the GitHub release.

The pack holds only the processed tables the app reads for the 2026 season. It never includes .env
or data/whatsapp/. After building, upload build/bara-demo-data-2026.zip to the GitHub release named
in src/get_demo_data.py and put the printed SHA-256 in that file.

Usage (from the repository root):
    .venv/Scripts/python deploy/data_pack.py
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from get_demo_data import MEMBERS, ZIP_NAME  # noqa: E402

NOTICE = """Bara demo data pack: 2026 season, processed from these sources.

Fire detections: NASA FIRMS VIIRS NOAA-20 (LANCE, ESDIS). NASA asks to be acknowledged.
Rain: CHIRPS (public domain) and NASA GPM IMERG Early.
Peat extent: Global Forest Watch global peatland extent (CC BY 4.0).
Places: GeoNames (CC BY 4.0).
"""


def main() -> None:
    out = ROOT / "build" / ZIP_NAME
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in MEMBERS:
            z.write(ROOT / rel, rel)
        z.writestr("data/DATA_NOTICE.txt", NOTICE)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"{out} ({out.stat().st_size / 1e6:.0f} MB)\nsha256 {digest}")


if __name__ == "__main__":
    main()
