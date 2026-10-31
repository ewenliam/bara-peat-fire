"""Bara web server: JSON API over the deterministic tools, plus the static front end in web/.

Run:
    .venv/Scripts/python -m uvicorn server:app --app-dir src --port 8600

Everything served under /api/day is computed by our own code (no LLM). Muse Spark and SAM 3.1
are only called by the POST endpoints, and every call goes through the cost ledger and cap.
"""
import json
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import run as run_agent, save_run
from chat import ask
from firms import load_years
from fusion import RECENT_H, fuse_day, recent_reports
from intake import process_upload
from imagery import chip_key
from llm import BudgetExceeded, spend_cap, spent_usd
from places import crew_bases, locate
from reports import list_reports, submit as submit_report
from selfcheck import review
from tools import FireTools
from water import CHIP_PX
from whatsapp_routes import router as whatsapp_router

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
WEB = ROOT / "web"
# Days the app can show: two past seasons, and the live year up to its latest built day (src/live.py)
SEASONS = {
    "2019": ("2019-01-08", "2019-12-30", "Practice season (2019): the year the model was tuned on"),
    "2023": ("2023-01-08", "2023-12-30", "Final exam (2023): held out, tested once"),
}
LIVE_FILE = ROOT / "data" / "processed" / "live_2026.json"


def live_info() -> dict | None:
    return json.loads(LIVE_FILE.read_text(encoding="utf-8")) if LIVE_FILE.exists() else None


def season_of(day: str) -> dict:
    live = live_info()
    if live and "2026-01-08" <= day <= live["live_day"]:
        is_live = day == live["live_day"]
        return {"mode": "live" if is_live else "recent",
                "label": "Live" if is_live else "Earlier this season (2026)", "live": live}
    for first, last, label in SEASONS.values():
        if first <= day <= last:
            return {"mode": "past", "label": label, "live": live}
    raise HTTPException(400, f"no data for {day}")

app = FastAPI(title="Bara")
app.include_router(whatsapp_router)  # /webhook; before the static mount at "/" so it wins


@lru_cache(maxsize=12)
def tools_for(day: str) -> FireTools:
    season_of(day)  # 400 if the day has no data
    t = FireTools(day)
    t.table()
    return t


@lru_cache(maxsize=4)
def median_daily_detections(year: int) -> int:
    """Median detections per day over the year so far, so a crisis day can be read in context."""
    per_day = load_years([year]).groupby("acq_date").size()
    days = pd.date_range(f"{year}-01-01", min(pd.Timestamp(f"{year}-12-31"), per_day.index.max()))  # live year: to date
    return int(per_day.reindex(days, fill_value=0).median())


def local_only(request: Request) -> None:
    """Endpoints that spend Meta API money answer only on this laptop, never through the public
    tunnel (run_demo.py), so a stranger with the tunnel address cannot use up the budget."""
    through_tunnel = "cf-ray" in request.headers or "cf-connecting-ip" in request.headers
    if through_tunnel or not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "This action runs only on the demo laptop.")


def _r(x, n: int = 1) -> float:
    return round(float(x), n)


@app.get("/api/meta")
def meta() -> dict:
    live = live_info()
    return {"first_day": "2019-01-08", "last_day": live["live_day"] if live else "2023-12-30",
            "default_day": live["live_day"] if live else "2019-09-15", "live": live,
            "seasons": {k: {"first": a, "last": b, "label": c} for k, (a, b, c) in SEASONS.items()},
            "spent_usd": round(spent_usd(), 4), "cap_usd": spend_cap()}


@app.get("/api/day")
def day(date: str) -> dict:
    t = tools_for(date)
    table = t.table()
    routes = t._routes()
    detect = t.detect_clusters(top_n=1)
    spread = t.forecast_spread(top_n=1)
    new_fires = t.forecast_new_fires(top_n=10)
    t._score_cells()  # per-cell spread forecast, drawn as tomorrow's chance of fire
    likely = t.cells[t.cells["p"] >= 0.05].nlargest(6000, "p")

    shown = table[(table["tier"] != "P3") | (table["n_today"] > 0)]
    loc = locate(shown["lat"].to_numpy(), shown["lon"].to_numpy())
    clusters = [{
        "id": cid, "lat": _r(r["lat"], 4), "lon": _r(r["lon"], 4), "tier": r["tier"],
        "risk": _r(r["risk"]), "expected_new_cells": _r(r["expected_new_cells"]),
        "max_probability": _r(r["max_probability"], 3), "peat_share": _r(r["peat_share"], 2),
        "detections_today": int(r["n_today"]), "days_since_10mm": int(round(r["days_since_10mm"])),
        "origin_lat": _r(r["origin_lat"], 4), "origin_lon": _r(r["origin_lon"], 4),
        "place": loc["place"].iat[i], "province": loc["province"].iat[i],
        "next_day_cells": int(r["actual_new_cells"]),  # replay truth, shown only on request
    } for i, (cid, r) in enumerate(shown.iterrows())]

    bases = crew_bases().set_index("name")
    p12 = table[table["tier"].isin(["P1", "P2"])]
    out_routes = []
    for r in routes:
        b = bases.loc[r["base"]]
        stops = [{"cluster_id": s["cluster_id"], "arrive_h": s["arrive_h"],
                  "lat": _r(table.loc[s["cluster_id"], "lat"], 4),
                  "lon": _r(table.loc[s["cluster_id"], "lon"], 4)} for s in r["stops"]]
        out_routes.append({"base": r["base"], "province": r["province"], "lat": _r(b["lat"], 4),
                           "lon": _r(b["lon"], 4), "risk_covered": r["risk_covered"], "stops": stops})
    visited = [s["cluster_id"] for r in routes for s in r["stops"]]
    counts = table["tier"].value_counts()
    return {
        "date": date, "forecast_for": spread["forecast_for"], "season": season_of(date),
        "kpis": {
            "detections_today": detect["detections_today"],
            "detections_median_day": median_daily_detections(int(date[:4])),
            "clusters_active_today": detect["clusters_active_today"],
            "p1": int(counts.get("P1", 0)), "p2": int(counts.get("P2", 0)),
            "expected_new_cells": spread["expected_new_cells_total"],
            "likely_cells": int((t.cells["p"] >= t.threshold).sum()),
            "crew_cover": _r(sum(r["risk_covered"] for r in routes) / max(p12["risk"].sum(), 1e-9), 3),
            "next_day_cells_total": int(table["actual_new_cells"].sum()),
            "next_day_cells_reached": int(table.loc[visited, "actual_new_cells"].sum()),
        },
        "clusters": clusters,
        "routes": out_routes,
        "bases": [{"name": n, "province": b["province"], "lat": _r(b["lat"], 4), "lon": _r(b["lon"], 4)}
                  for n, b in bases.iterrows()],
        "new_fires": new_fires["cells"],
        "forecast_cells": [[_r(x, 3), _r(y, 3), _r(q, 2)] for x, y, q in zip(likely["lon"], likely["lat"], likely["p"])],
        "forecast_threshold": _r(t.threshold, 3),
    }


def _water_images(date: str, saved: dict) -> dict:
    """Overlay image URL per crew stop that has a SAM river search in the saved briefing."""
    stops = (saved.get("tool_results", {}).get("find_water_sources") or [{}])[0].get("stops", [])
    if not stops:
        return {}
    table = tools_for(date).table()
    out = {}
    for s in stops:
        c = table.loc[s["cluster_id"]]
        name = f"{chip_key(float(c['centre_lat']), float(c['centre_lon']), CHIP_PX)}.png"
        if (OUT / "water" / name).exists():
            out[s["cluster_id"]] = {**s, "image": f"/outputs/water/{name}"}
    return out


@app.get("/api/briefing")
def briefing(date: str) -> dict:
    path = OUT / f"briefing_{date.replace('-', '')}.json"
    if not path.exists():
        raise HTTPException(404, "no briefing for this date yet")
    saved = json.loads(path.read_text(encoding="utf-8"))
    return {**saved, "water": _water_images(date, saved)}


@app.post("/api/briefing", dependencies=[Depends(local_only)])
def make_briefing(date: str) -> dict:
    tools_for(date)
    try:
        save_run(run_agent(date))
    except BudgetExceeded as e:
        raise HTTPException(402, str(e))
    return briefing(date)


class ChatRequest(BaseModel):
    date: str
    messages: list[dict]


@app.post("/api/chat", dependencies=[Depends(local_only)])
def chat(req: ChatRequest) -> dict:
    tools_for(req.date)
    try:
        return ask(req.date, req.messages[-12:])
    except BudgetExceeded as e:
        raise HTTPException(402, str(e))


@app.post("/api/report", dependencies=[Depends(local_only)])
async def report(photo: UploadFile, lat: float = Form(...), lon: float = Form(...),
                 date: str = Form(...), note: str = Form("")) -> dict:
    tools_for(date)
    image = await photo.read()
    if len(image) > 8_000_000:
        raise HTTPException(413, "photo larger than 8 MB")
    try:
        return submit_report(image, photo.content_type or "image/jpeg", lat, lon, date, note)
    except BudgetExceeded as e:
        raise HTTPException(402, str(e))


class SelfCheckRequest(BaseModel):
    date: str
    image: str  # data URL of the live map snapshot
    routes_shown: int = 12


@app.post("/api/selfcheck", dependencies=[Depends(local_only)])
def selfcheck(req: SelfCheckRequest) -> dict:
    tools_for(req.date)
    if not req.image.startswith("data:image/"):
        raise HTTPException(400, "image must be a data URL")
    try:
        return review(req.date, req.image, req.routes_shown)
    except BudgetExceeded as e:
        raise HTTPException(402, str(e))


@app.get("/api/reports")
def reports(date: str) -> list[dict]:
    return list_reports(date)


VOICE_SUFFIX = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3", "audio/wav": ".wav"}


@app.post("/api/field/voice", dependencies=[Depends(local_only)])
def field_voice(audio: UploadFile) -> dict:
    """A voice note recorded in the app: the same Voice Transcribe and Muse Spark steps as WhatsApp."""
    data = audio.file.read()
    if not data:
        raise HTTPException(400, "empty recording")
    if len(data) > 16 * 1024 * 1024:
        raise HTTPException(413, "recording larger than 16 MB")
    suffix = VOICE_SUFFIX.get((audio.content_type or "").split(";")[0].strip(), ".webm")
    out = process_upload(data, suffix)
    return {k: out[k] for k in ("id", "status", "error", "transcript")}


@app.get("/api/test2023")
def test2023() -> dict:
    """The one pre-registered 2023 held-out run (src/test_2023.py), read from its committed results file."""
    path = OUT / "test_2023_results.json"
    if not path.exists():
        raise HTTPException(404, "the 2023 test has not been run")
    d = json.loads(path.read_text(encoding="utf-8"))
    ap = lambda block: {name: {"AP": _r(m["AP"], 3), "AP_new": _r(m["AP_new"], 3) if "AP_new" in m else None}  # noqa: E731
                        for name, m in d[block]["metrics"].items()}
    return {"started_utc": d["started_utc"], "commit": d["git"]["commit"][:7],
            "H1": d["forecast"]["H1"], "H1_noaa20": d["sensor_noaa20"]["H1"], "H2": d["dispatch"],
            "forecast": ap("forecast"), "new_fire": ap("ignition"),
            "rows": d["forecast"]["rows"], "positives": d["forecast"]["positives"]}


@app.get("/api/field")
def field(date: str, hours: float = RECENT_H) -> dict:
    """Live WhatsApp reports from the last `hours`, applied to this day's plan (no API calls).

    During the 2019 replay this places today's reports on the replay day: a demo, said so in the UI.
    """
    t = tools_for(date)
    out = fuse_day(t.table(), t._routes(), recent_reports(hours))
    table, bases = out["table"], crew_bases().set_index("name")

    def path(base: str, stops: list[str]) -> list[list[float]]:
        return [[_r(bases.loc[base, "lon"], 4), _r(bases.loc[base, "lat"], 4)]] + \
            [[_r(table.loc[c, "lon"], 4), _r(table.loc[c, "lat"], 4)] for c in stops]

    for r in out["reports"]:
        cid = r["decision"]["cluster_id"]
        r["decision"]["cluster_lat"] = _r(table.loc[cid, "lat"], 4) if cid else None
        r["decision"]["cluster_lon"] = _r(table.loc[cid, "lon"], 4) if cid else None
    changed = [{**c, "path_after": path(c["base"], c["after"])} for c in out["route_diff"]]
    body = {"window_hours": hours, "reports": out["reports"], "route_diff": changed,
            "risk_covered_before": out["risk_covered_before"], "risk_covered_after": out["risk_covered_after"]}
    return json.loads(json.dumps(body, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


@app.get("/api/replay")
def replay() -> dict:
    csv = OUT / "replay_20190701_20191031.csv"
    df = pd.read_csv(csv)
    s = df.sum(numeric_only=True)
    methods = ["solver", "greedy", "biggest_today", "nearest", "oracle"]
    return {
        "start": df["date"].iat[0], "end": df["date"].iat[-1], "days": len(df),
        "totals": {m: {"visits": int(s[f"{m}_visits"]), "cells": int(round(s[f"{m}_actual"])),
                       "peat_cells": int(round(s[f"{m}_peat_actual"]))} for m in methods},
        "triage": {"p1_cluster_share": _r(s["n_P1"] / s["clusters"], 4),
                   "p1_cells_share": _r(s["actual_in_P1"] / s["actual_total"], 4),
                   "p1_cells_share_persistence": _r(s["actual_in_P1_persist"] / s["actual_total"], 4)},
        "series": [{"date": r["date"], "next_day_cells": int(r["actual_total"]),
                    **{m: int(r[f"{m}_actual"]) for m in methods}} for _, r in df.iterrows()],
    }


app.mount("/outputs", StaticFiles(directory=OUT), name="outputs")


@app.get("/")
def index() -> FileResponse:
    # always revalidate the page, so the ?v= tags on app.js and style.css reach the browser
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB), name="web")
