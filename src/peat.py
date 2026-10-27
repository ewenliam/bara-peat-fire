"""Peat share per ~1.1 km grid cell from the Global Forest Watch peatland extent layer.

Source: GFW "Global peatland extent" v20230315 (CC BY 4.0). For Indonesia it uses
Miettinen et al. 2016, combined with Gumbricht et al. 2017. We read the public map
tiles at zoom 9 (~300 m pixels); a pixel's alpha channel is its peat coverage.

Usage:
    python src/peat.py
"""
import math
import time
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pandas as pd
import requests

from firms import REGION_BBOX
from labels import RES

ROOT = Path(__file__).resolve().parents[1]
TILE_URL = "https://tiles.globalforestwatch.org/gfw_peatlands/v20230315/default/{z}/{x}/{y}.png"
ZOOM = 9
TILE_DIR = ROOT / "data" / "raw" / "peat_tiles"
OUT = ROOT / "data" / "processed" / "peat_share.parquet"


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def pixel_centres(x: int, y: int, z: int, size: int = 256) -> tuple[np.ndarray, np.ndarray]:
    """Lon/lat of every pixel centre in a web-mercator tile."""
    n = 2 ** z
    px = (x + (np.arange(size) + 0.5) / size) / n
    py = (y + (np.arange(size) + 0.5) / size) / n
    lon = px * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * py))))
    return np.meshgrid(lon, lat)


def fetch_tile(x: int, y: int, z: int) -> np.ndarray | None:
    path = TILE_DIR / f"{z}_{x}_{y}.png"
    if not path.exists():
        for attempt in range(5):
            try:
                r = requests.get(TILE_URL.format(z=z, x=x, y=y), timeout=30)
                break
            except requests.ConnectionError:
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"tile {z}/{x}/{y} failed after 5 attempts")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        path.write_bytes(r.content)
        time.sleep(0.1)
    img = mpimg.imread(path)
    return img[..., 3] if img.ndim == 3 and img.shape[-1] == 4 else None


def main() -> None:
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    x0, y0 = lonlat_to_tile(REGION_BBOX["lon_min"], REGION_BBOX["lat_max"], ZOOM)
    x1, y1 = lonlat_to_tile(REGION_BBOX["lon_max"], REGION_BBOX["lat_min"], ZOOM)
    frames = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            alpha = fetch_tile(x, y, ZOOM)
            if alpha is None or alpha.max() == 0:
                continue
            lon, lat = pixel_centres(x, y, ZOOM)
            frames.append(pd.DataFrame({
                "ix": np.floor(lon.ravel() / RES).astype(np.int32),
                "iy": np.floor(lat.ravel() / RES).astype(np.int32),
                "peat": alpha.ravel(),
            }))
        print(f"tile column {x - x0 + 1}/{x1 - x0 + 1}")

    pixels = pd.concat(frames, ignore_index=True)
    share = pixels.groupby(["ix", "iy"], as_index=False)["peat"].mean()
    share = share.rename(columns={"peat": "peat_share"})
    share = share[share["peat_share"] > 0]
    share.to_parquet(OUT)
    area_km2 = share["peat_share"].sum() * (RES * 111.32) ** 2
    print(f"{len(share):,} cells with peat, ~{area_km2:,.0f} km2 of peat in the region")


if __name__ == "__main__":
    main()
