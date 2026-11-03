"""Assemble the public Vercel deployment of Bara in build/bara/.

The bundle is the web app (src/, web/, models/, outputs/) plus only the processed tables the GET
endpoints read for the 2019, 2023 and 2026 seasons. It never includes .env, data/whatsapp/ (phone
numbers) or anything else under data/. On Vercel the paid endpoints answer 403 (server.local_only
checks the VERCEL variable), and no API keys are set there.

Usage (from the repository root):
    .venv/Scripts/python deploy/vercel_bundle.py
    cd build/bara && vercel deploy --prod
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "build" / "bara"  # Vercel names the project after this folder
SEASONS = (2019, 2023, 2026)

DATA = (
    [f"data/processed/samples_{y}_{y}_feat.parquet" for y in SEASONS]
    + [f"data/processed/ign_{y}.parquet" for y in SEASONS]
    + ["data/processed/places.parquet", "data/processed/gazetteer.parquet", "data/processed/live_2026.json",
       "data/raw/viirs-snpp_2019_Indonesia.csv", "data/raw/viirs-snpp_2023_Indonesia.csv",
       "data/raw/viirs-jpss1_2023_Indonesia.csv", "data/raw/viirs-jpss1_2026_Indonesia.csv"]
)
FOLDERS = ["src", "web", "models", "outputs"]
FILES = ["logs/meta_usage.jsonl", "LICENSE"]

# Only what `import server` and the GET endpoints load (checked with sys.modules). rasterio and
# pystac-client are left out: only imagery fetching uses them, behind the laptop-only endpoints.
# uvicorn is not needed because Vercel serves the ASGI app itself.
REQUIREMENTS = """fastapi==0.162.0
lightgbm==4.7.0
matplotlib==3.11.2
meta-sam-parser==0.1.0
numpy==2.5.4
openai==3.39.0
pandas==3.1.0
pyarrow==26.0.0
pydantic==2.14.1
python-dotenv==1.2.4
python-multipart==0.0.32
requests==2.34.2
scikit-learn==1.9.1
scipy==1.18.1
"""

ENTRY = '''"""Vercel entrypoint: put src/ on the path and expose Bara's FastAPI app."""
import ctypes
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")  # the bundle is read-only; /tmp is not

try:
    import lightgbm  # noqa: F401
except OSError:
    # LightGBM needs libgomp.so.1, which the runtime may lack: load the bundled copy (deploy/lib)
    # globally so its soname satisfies LightGBM's dependency, then import again.
    ctypes.CDLL(str(Path(__file__).resolve().parent / "lib" / "libgomp.so.1"), mode=ctypes.RTLD_GLOBAL)
    import lightgbm  # noqa: F401

from server import app  # noqa: E402,F401
'''

# Vercel installs from pyproject.toml when it exists and then ignores requirements.txt, so the
# dependencies must be listed here.
DEPENDENCIES = "\n".join(f'    "{r}",' for r in REQUIREMENTS.split())
PYPROJECT = f"""[project]
name = "bara"
version = "1.0.0"
requires-python = ">=3.12,<3.13"
dependencies = [
{DEPENDENCIES}
]

[tool.vercel]
entrypoint = "app:app"
"""

VERCEL_JSON = """{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "functions": {
    "app.py": {
      "maxDuration": 60
    }
  }
}
"""


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for child in DEST.iterdir():  # keep .vercel, the link to the Vercel project
        if child.is_dir() and child.name != ".vercel":
            shutil.rmtree(child)
        elif child.is_file():
            child.unlink()
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for folder in FOLDERS:
        shutil.copytree(ROOT / folder, DEST / folder, ignore=ignore)
    missing = []
    for rel in DATA + FILES:
        src = ROOT / rel
        if not src.exists():
            missing.append(rel)
            continue
        (DEST / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, DEST / rel)
    if missing:
        raise SystemExit(f"missing files: {missing}")
    (DEST / "lib").mkdir()
    shutil.copy2(ROOT / "deploy" / "lib" / "libgomp.so.1", DEST / "lib" / "libgomp.so.1")
    (DEST / "app.py").write_text(ENTRY, encoding="utf-8")
    (DEST / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (DEST / "vercel.json").write_text(VERCEL_JSON, encoding="utf-8")
    (DEST / ".python-version").write_text("3.12\n", encoding="utf-8")

    leaks = [p for p in DEST.rglob("*") if p.name == ".env" or "whatsapp" in p.parts and p.parts[-2] == "whatsapp"]
    if leaks:
        raise SystemExit(f"refusing: private files in bundle: {leaks}")
    size = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
    print(f"bundle at {DEST} ({size / 1e6:.0f} MB of files, before Python packages)")


if __name__ == "__main__":
    main()
