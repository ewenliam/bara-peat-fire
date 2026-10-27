"""SAM 3.1 (Meta Model API) text-prompted segmentation of Sentinel-2 chips.

Request format from https://dev.meta.ai/docs/sam/segmenting: Responses API, model "sam-3.1",
one user message with an input_text noun phrase and an input_image data URL. The text output
is parsed with Meta's `meta-sam-parser` package. Each mask arrives as a small raster plus its
box in image pixels; it is resized onto the box to build a full-chip mask.

Cost: $2.50 per 1,000 images (https://dev.meta.ai/docs/pricing-rate-limits), logged to the
same ledger and capped by the same META_SPEND_CAP_USD as Muse Spark (src/llm.py). Outputs are
cached in cache/meta/, so a repeated call costs nothing.

Usage:
    python src/sam.py data/processed/chips/<chip>.png "canal"
"""
import argparse
import base64
import hashlib
import os
import sys
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from meta_sam_parser import CompletedOutcome, decode_mask_to_raster, image_segmentation_format
from openai import OpenAI

from llm import CACHE_DIR, ROOT, append_ledger, check_budget

SAM_MODEL = "sam-3.1"
PRICE_PER_IMAGE = 2.50 / 1000


def _request(png: bytes, prompt: str) -> dict:
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    return {
        "model": SAM_MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": url},
        ]}],
        "metadata": {"include_confidence": "true"},
    }


def raw_segment(png_path: Path, prompt: str, use_cache: bool = True, tag: str = "") -> str:
    png = Path(png_path).read_bytes()
    key = hashlib.sha256(png + b"|" + prompt.encode() + b"|conf").hexdigest()[:24]
    cached = CACHE_DIR / f"sam_{key}.txt"
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag or f"sam {prompt}",
             "model": SAM_MODEL, "cache_key": key}
    if use_cache and cached.exists():
        append_ledger({**entry, "cache_hit": True, "seconds": 0.0, "cost_usd": 0.0})
        return cached.read_text(encoding="utf-8")

    check_budget(PRICE_PER_IMAGE)
    load_dotenv(ROOT / ".env")
    client = OpenAI(api_key=os.environ["META_API_KEY"], base_url=os.environ["META_BASE_URL"])
    start = time.time()
    response = client.responses.create(**_request(png, prompt))
    text = response.output_text or ""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    append_ledger({**entry, "cache_hit": False, "seconds": round(time.time() - start, 2),
                   "cost_usd": PRICE_PER_IMAGE, "status": response.status})
    return text


def parse(text: str, width: int, height: int) -> list[dict]:
    """Objects with box, confidence and a full-chip boolean mask."""
    parser = image_segmentation_format().create_parser()
    parser.push(text)
    result = parser.finish(CompletedOutcome()).result
    if result.diagnostics:
        print(f"parser diagnostics: {[d.code for d in result.diagnostics]}", file=sys.stderr)
    objects = []
    for rec in result.records:
        if rec.kind != "mask":
            continue
        small = np.frombuffer(decode_mask_to_raster(rec.mask), dtype=np.uint8)
        small = small.reshape(rec.mask.height, rec.mask.width).astype(bool)
        b = rec.bounds
        left, top = int(round(b.left)), int(round(b.top))
        right, bottom = int(round(b.right)), int(round(b.bottom))
        full = np.zeros((height, width), dtype=bool)
        if right > left and bottom > top:
            rows = (np.arange(bottom - top) * rec.mask.height // (bottom - top))
            cols = (np.arange(right - left) * rec.mask.width // (right - left))
            full[top:bottom, left:right] = small[rows][:, cols]
        objects.append({"object_id": rec.object_id, "confidence": rec.confidence,
                        "box": [left, top, right, bottom], "pixels": int(full.sum()), "mask": full})
    return objects


def segment(png_path: Path, prompt: str, width: int, height: int, use_cache: bool = True,
            tag: str = "") -> list[dict]:
    return parse(raw_segment(png_path, prompt, use_cache, tag), width, height)


def overlay(png_path: Path, masks: dict[str, np.ndarray], out: Path) -> None:
    """Chip with each prompt's union mask tinted, for checking masks by eye."""
    import matplotlib.image as mpimg
    rgb = mpimg.imread(png_path)[..., :3].astype(float)
    if rgb.max() > 1:
        rgb /= 255
    colours = [(0.1, 0.5, 1.0), (1.0, 0.2, 0.6), (1.0, 0.8, 0.0)]
    for colour, mask in zip(colours, masks.values()):
        rgb[mask] = 0.4 * rgb[mask] + 0.6 * np.array(colour)
    mpimg.imsave(out, np.clip(rgb, 0, 1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("png")
    parser.add_argument("prompt")
    args = parser.parse_args()
    import matplotlib.image as mpimg
    h, w = mpimg.imread(args.png).shape[:2]
    objects = segment(Path(args.png), args.prompt, w, h)
    print(f"{len(objects)} objects for '{args.prompt}'")
    for o in objects:
        print(f"  {o['object_id']}: confidence {o['confidence']}, box {o['box']}, {o['pixels']} px")


if __name__ == "__main__":
    main()
