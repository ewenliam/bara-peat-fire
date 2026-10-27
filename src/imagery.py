"""True-colour Sentinel-2 chips around a point, for SAM 3.1.

Source: Copernicus Sentinel-2 L2A on the Earth Search STAC catalogue (Element 84, AWS open
data, no key): https://earth-search.aws.element84.com/v1, asset "visual" (10 m RGB COG) and
"scl" (20 m scene classification, used to measure clouds inside the chip).

Rivers barely change, so chips come from the clearest recent scene, not from the fire date.
Indonesia is cloudy, so several scenes are tried until one is nearly cloud-free inside the
chip. The scene classification misses fire-season smoke haze (a Sep 2023 chip that was all
haze was 98.9% "bare soil"), so chips that are mostly bright are rejected too.

Usage:
    python src/imagery.py --lat -2.209 --lon 113.819
"""
import argparse
import json
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import rasterio
from pystac_client import Client
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
CHIPS = ROOT / "data" / "processed" / "chips"
STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
DATES = "2023-01-01/2025-12-31"
SCENE_CLOUD_MAX = 30   # scene-level filter, percent
CHIP_CLOUD_MAX = 0.05  # share of chip pixels that are cloud, shadow or cirrus
CLOUD_SCL = [3, 8, 9, 10]  # SCL classes: cloud shadow, cloud medium, cloud high, cirrus
HAZE_MAX = 0.2         # share of bright pixels (smoke, haze, thin cloud)
MAX_TRIES = 12
SIZE_PX = 512          # 10 m pixels, so a chip is about 5 x 5 km


def chip_key(lat: float, lon: float, size: int) -> str:
    return f"{lat:.3f}_{lon:.3f}_{size}".replace("-", "m")


def _window(src, lon: float, lat: float, size: int) -> Window:
    xs, ys = warp_transform("EPSG:4326", src.crs, [lon], [lat])
    row, col = src.index(xs[0], ys[0])
    return Window(col - size // 2, row - size // 2, size, size)


def get_chip(lat: float, lon: float, size: int = SIZE_PX) -> dict:
    """Clearest chip around (lat, lon); cached as PNG plus JSON metadata."""
    key = chip_key(lat, lon, size)
    meta_path = CHIPS / f"{key}.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))

    items = Client.open(STAC).search(
        collections=[COLLECTION], intersects={"type": "Point", "coordinates": [lon, lat]},
        datetime=DATES, query={"eo:cloud_cover": {"lt": SCENE_CLOUD_MAX}}, max_items=200,
    ).item_collection()
    items = sorted(items, key=lambda i: i.properties["eo:cloud_cover"])
    tried = []
    for item in items[:MAX_TRIES]:
        with rasterio.open(item.assets["scl"].href) as scl:
            w = _window(scl, lon, lat, size // 2)  # SCL is 20 m
            classes = scl.read(1, window=w, boundless=True, fill_value=0)
        cloud = float(np.isin(classes, CLOUD_SCL).mean())
        nodata = float((classes == 0).mean())
        tried.append({"id": item.id, "chip_cloud": round(cloud, 3), "nodata": round(nodata, 3)})
        if cloud > CHIP_CLOUD_MAX or nodata > 0.01:
            continue
        with rasterio.open(item.assets["visual"].href) as src:
            w = _window(src, lon, lat, size)
            rgb = src.read([1, 2, 3], window=w, boundless=True, fill_value=0)
            tf = src.window_transform(w)
            crs = src.crs.to_string()
        haze = float((rgb.mean(axis=0) > 120).mean())  # clear chips 0-4%, a hazy one 99%
        tried[-1]["haze"] = round(haze, 3)
        if haze > HAZE_MAX:
            continue
        CHIPS.mkdir(parents=True, exist_ok=True)
        mpimg.imsave(CHIPS / f"{key}.png", np.moveaxis(rgb, 0, -1).astype(np.uint8))
        meta = {"key": key, "png": f"{key}.png", "lat": lat, "lon": lon, "size_px": size,
                "item": item.id, "date": f"{item.datetime:%Y-%m-%d}", "chip_cloud": round(cloud, 3), "haze": round(haze, 3),
                "crs": crs, "transform": list(tf)[:6], "pixel_m": abs(tf.a)}
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return meta
    raise RuntimeError(f"no clear chip at ({lat}, {lon}); tried {tried}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--size", type=int, default=SIZE_PX)
    args = parser.parse_args()
    print(json.dumps(get_chip(args.lat, args.lon, args.size), indent=1))


if __name__ == "__main__":
    main()
