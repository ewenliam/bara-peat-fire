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
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import run as run_agent, save_run
from chat import ask
from imagery import chip_key
from llm import BudgetExceeded, spend_cap, spent_usd
from places import crew_bases, locate
from reports import list_reports, submit as submit_report
from selfcheck import review
from tools import FireTools
from water import CHIP_PX

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
WEB = ROOT / "web"
FIRST_DAY, LAST_DAY = "2019-01-08", "2019-12-30"

app = FastAPI(title="Bara")


@lru_cache(maxsize=12)
def tools_for(day: str) -> FireTools:
    if not FIRST_DAY <= day <= LAST_DAY:
        raise HTTPException(400, f"replay covers {FIRST_DAY} to {LAST_DAY}")
    t = FireTools(day)
    t.table()
    return t


def _r(x, n: int = 1) -> float:
    return round(float(x), n)


@app.get("/api/meta")
def meta() -> dict:
    return {"first_day": FIRST_DAY, "last_day": LAST_DAY, "default_day": "2019-09-15",
            "spent_usd": round(spent_usd(), 4), "cap_usd": spend_cap()}


@app.get("/api/day")
def day(date: str) -> dict:
    t = tools_for(date)
    table = t.table()
    routes = t._routes()
    detect = t.detect_clusters(top_n=1)
    spread = t.forecast_spread(top_n=1)
    new_fires = t.forecast_new_fires(top_n=10)

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
        "date": date, "forecast_for": spread["forecast_for"],
        "kpis": {
            "detections_today": detect["detections_today"],
            "clusters_active_today": detect["clusters_active_today"],
            "p1": int(counts.get("P1", 0)), "p2": int(counts.get("P2", 0)),
            "expected_new_cells": spread["expected_new_cells_total"],
            "crew_cover": _r(sum(r["risk_covered"] for r in routes) / max(p12["risk"].sum(), 1e-9), 3),
            "next_day_cells_total": int(table["actual_new_cells"].sum()),
            "next_day_cells_reached": int(table.loc[visited, "actual_new_cells"].sum()),
        },
        "clusters": clusters,
        "routes": out_routes,
        "bases": [{"name": n, "province": b["province"], "lat": _r(b["lat"], 4), "lon": _r(b["lon"], 4)}
                  for n, b in bases.iterrows()],
        "new_fires": new_fires["cells"],
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


@app.post("/api/briefing")
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


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    tools_for(req.date)
    try:
        return ask(req.date, req.messages[-12:])
    except BudgetExceeded as e:
        raise HTTPException(402, str(e))


@app.post("/api/report")
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


@app.post("/api/selfcheck")
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
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
