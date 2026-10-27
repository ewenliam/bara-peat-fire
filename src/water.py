"""Nearest visible river around a fire cluster, from SAM 3.1 masks on a Sentinel-2 chip.

Crews pump water from rivers and canals. Oct 27 tests of SAM 3.1 on 10 m Sentinel-2 true
colour:
- "river" found the Kahayan at Palangkaraya (confidence 0.65) but missed a wide orange river
  that "waterway" found (0.51), and that "river" also found on a zoomed-in quarter crop.
- "canal", "drainage ditch", "road", "water", "lake", "stream", "black river" and "muddy
  river" found nothing, even on 2x zoomed crops of a plantation full of visible canals.
So each chip is searched with "river" and "waterway", plus "river" on each quarter, and the
masks are combined. Drainage canals are too narrow for SAM at this resolution: this gives a
water source hint, not a canal map.

Usage:
    python src/water.py --lat -2.209 --lon 113.819
"""
import argparse
import json
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
from rasterio.warp import transform as warp_transform

from imagery import CHIPS, get_chip
from sam import overlay, segment

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "water"
CHIP_PX = 1024  # 10 m pixels: about 10 x 10 km, so rivers up to ~5 km away are visible
FULL_PROMPTS = ("river", "waterway")
QUARTER_PROMPTS = ("river",)
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _quarters(png: Path) -> list[tuple[Path, int, int]]:
    rgb = mpimg.imread(png)[..., :3]
    half = CHIP_PX // 2
    out = []
    for y0 in (0, half):
        for x0 in (0, half):
            path = png.with_name(f"{png.stem}_q{x0}_{y0}.png")
            if not path.exists():
                mpimg.imsave(path, rgb[y0:y0 + half, x0:x0 + half])
            out.append((path, x0, y0))
    return out


def _mark(path: Path, river: tuple[int, int] | None, half: int = 7) -> None:
    """Red square at the cluster centre, yellow square at the nearest river pixel."""
    img = mpimg.imread(path)[..., :3].copy()
    c = CHIP_PX // 2
    points = [((c, c), (1.0, 0.0, 0.0))] + ([(river, (1.0, 0.9, 0.0))] if river else [])
    for (r, col), colour in points:
        img[max(r - half, 0):r + half + 1, max(col - half, 0):col + half + 1] = colour
    mpimg.imsave(path, img)


def nearest_water(lat: float, lon: float, use_cache: bool = True, tag: str = "") -> dict:
    meta = get_chip(lat, lon, CHIP_PX)
    png = CHIPS / meta["png"]
    union = np.zeros((CHIP_PX, CHIP_PX), dtype=bool)
    confidences = []
    jobs = [(png, 0, 0, CHIP_PX, p) for p in FULL_PROMPTS]
    jobs += [(q, x0, y0, CHIP_PX // 2, p) for q, x0, y0 in _quarters(png) for p in QUARTER_PROMPTS]
    for path, x0, y0, size, prompt in jobs:
        for o in segment(path, prompt, size, size, use_cache, tag=tag or f"sam {prompt}"):
            union[y0:y0 + size, x0:x0 + size] |= o["mask"]
            confidences.append(o["confidence"])
    OUT.mkdir(parents=True, exist_ok=True)
    overlay(png, {"river": union}, OUT / meta["png"])
    _mark(OUT / meta["png"], None)
    out = {"image": f"Sentinel-2 {meta['item']} ({meta['date']})", "overlay": f"outputs/water/{meta['png']}",
           "searched_km_each_side": round(CHIP_PX * meta["pixel_m"] / 2000, 1)}
    if not union.any():
        return {**out, "river_found": False}

    rows, cols = np.nonzero(union)
    c = CHIP_PX / 2
    d = np.hypot(rows - c, cols - c)
    i = int(d.argmin())
    _mark(OUT / meta["png"], (rows[i], cols[i]))
    dy, dx = c - rows[i], cols[i] - c  # image rows grow southwards
    bearing = (np.degrees(np.arctan2(dx, dy)) + 360) % 360
    a, _, x0, _, e, y0 = meta["transform"]
    xs, ys = warp_transform(meta["crs"], "EPSG:4326", [x0 + (cols[i] + 0.5) * a], [y0 + (rows[i] + 0.5) * e])
    return {**out, "river_found": True,
            "nearest_river_m": int(round(d[i] * meta["pixel_m"], -1)),
            "direction": COMPASS[int((bearing + 22.5) // 45) % 8],
            "river_lat": round(ys[0], 4), "river_lon": round(xs[0], 4),
            "sam_confidence": round(max(x for x in confidences if x is not None), 2)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    args = parser.parse_args()
    print(json.dumps(nearest_water(args.lat, args.lon), indent=1))


if __name__ == "__main__":
    main()
