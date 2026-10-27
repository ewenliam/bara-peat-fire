"""Field photo reports: a ranger sends a photo and a location; Bara checks it against the plan.

Muse Spark 1.3 looks at the photo (image input) and returns one category with the visible
evidence. Our code, not the model, then matches the report to the nearest fire cluster and
decides what it means for the plan (confirms a cluster, suggests escalating it, flags a fire
the satellite has not seen, or a possible false alarm). The model's confidence is its own
estimate and is shown as such.

Usage:
    python src/reports.py web/samples/marking_the_fire.jpg --lat -2.21 --lon 113.82 --date 2019-09-15
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, ValidationError

from llm import MetaLLM
from places import km_xy, locate
from tools import FireTools

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "outputs" / "reports"
MATCH_KM = 5.0
MAX_TOKENS = 4000

Category = Literal["active_flames", "smouldering_peat", "smoke_only", "burn_scar", "no_fire", "unclear"]

PROMPT = """You check field photos for a peat fire brigade in Sumatra and Kalimantan.
Classify what is visible in this photo. Use only what you can see; do not guess the place.
Categories:
- active_flames: open flames are visible
- smouldering_peat: smoke rising from the ground or peat, no open flame (peat burns below the surface)
- smoke_only: smoke or haze but its source on the ground is not visible
- burn_scar: burned ground or vegetation with no visible smoke or flame
- no_fire: no sign of fire, smoke or burning
- unclear: the photo is too dark, blurry or unrelated to judge
Reply with ONE JSON object only:
{"category": one of the names above, "confidence": number 0 to 1,
 "visible": [up to 4 short phrases naming what you see], "crew_present": true or false}"""


class Classification(BaseModel):
    category: Category
    confidence: float = Field(ge=0, le=1)
    visible: list[str]
    crew_present: bool


def classify(image: bytes, mime: str = "image/jpeg", use_cache: bool = True) -> dict:
    url = f"data:{mime};base64," + base64.b64encode(image).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": url}},
    ]}]
    llm = MetaLLM(use_cache=use_cache)
    reply = llm.chat(messages, max_tokens=MAX_TOKENS, tag="report classify")
    text = (reply.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        result, error = Classification.model_validate_json(text).model_dump(), None
    except ValidationError as e:
        result, error = None, f"schema: {e.errors()[0]['msg']}"
    return {"classification": result, "error": error, "llm_calls": llm.run_calls, "cost_usd": round(llm.run_cost, 6)}


def fuse(classification: dict, lat: float, lon: float, tools: FireTools) -> dict:
    """Deterministic rules: what the report means for today's plan."""
    table = tools.table()
    d = np.hypot(*(km_xy(table["lat"].to_numpy(), table["lon"].to_numpy()) - km_xy([lat], [lon])).T)
    i = int(d.argmin())
    cid, dist = table.index[i], float(d[i])
    near = dist <= MATCH_KM
    c = table.iloc[i]
    cat = classification["category"]
    burning = cat in ("active_flames", "smouldering_peat")
    if cat == "unclear":
        action, kind = "Ask the ranger for another photo.", "unclear"
    elif burning and near and c["tier"] != "P1":
        action, kind = f"Burning confirmed at {cid} ({c['tier']}). Review for escalation to P1.", "escalate"
    elif (burning or cat == "smoke_only") and near:
        action, kind = f"Confirms {cid} ({c['tier']}) from the ground.", "confirm"
    elif burning or cat == "smoke_only":
        action, kind = (f"Fire not in any satellite cluster (nearest {cid} is {dist:.1f} km away). "
                        "Add to the watch list."), "new_fire"
    elif cat == "burn_scar":
        action, kind = ("Burned ground. Peat can keep smouldering below the surface: check again "
                        "in the coming days."), "burn_scar"
    elif near:
        action, kind = (f"No fire seen near {cid}, which the satellite still shows. Possible false "
                        "alarm or wrong location: ask for a second look."), "possible_false_alarm"
    else:
        action, kind = "No fire seen and no satellite cluster nearby.", "clear"
    loc = locate(np.array([lat]), np.array([lon])).iloc[0]
    return {"kind": kind, "action": action, "nearest_cluster": cid, "nearest_cluster_km": round(dist, 1),
            "nearest_cluster_tier": c["tier"], "place": loc["place"], "province": loc["province"]}


def submit(image: bytes, mime: str, lat: float, lon: float, date: str, note: str = "") -> dict:
    key = hashlib.sha256(image).hexdigest()[:16]
    REPORTS.mkdir(parents=True, exist_ok=True)
    ext = {"image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
    (REPORTS / f"{key}.{ext}").write_bytes(image)
    out = classify(image, mime)
    report = {"id": key, "date": date, "lat": lat, "lon": lon, "note": note,
              "photo": f"/outputs/reports/{key}.{ext}", **out}
    if out["classification"]:
        report["decision"] = fuse(out["classification"], lat, lon, FireTools(date))
    (REPORTS / f"{key}_{date}.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def list_reports(date: str) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(REPORTS.glob(f"*_{date}.json"))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("photo")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    path = Path(args.photo)
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    r = submit(path.read_bytes(), mime, args.lat, args.lon, args.date)
    print(json.dumps({k: r[k] for k in ("classification", "error", "cost_usd")}, indent=1))
    print(json.dumps(r.get("decision"), indent=1))


if __name__ == "__main__":
    main()
