"""Bara briefing agent: Muse Spark calls the Detect, Forecast, Triage, Dispatch and water
tools, then writes the daily briefing (English for the hackathon).

The model never computes the plan. It decides which tools to call, reads their JSON and
explains it. Its final answer must be one JSON object (checked with pydantic) and every
number in it must trace back to a tool output (grounding check). If either check fails,
the errors are sent back once for a repair.

Usage:
    python src/agent.py --date 2019-09-15            # live (cached after the first run)
    python src/agent.py --date 2019-09-15 --no-cache # force fresh API calls
"""
import argparse
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from llm import MetaLLM, spent_usd
from places import PROVINCE_ID
from tools import FireTools

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
MAX_STEPS = 6
MAX_REPAIRS = 1
# Muse Spark reasons before answering and reasoning counts toward max_tokens: at 4000 the
# final answer was cut off (3998 reasoning tokens). A cut-off reply is retried once at double.
MAX_TOKENS = 8000
MAX_TOKENS_RETRY = 16000


class ClusterBrief(BaseModel):
    cluster_id: str
    province: str
    tier: str
    earliest_detection_lat: float
    earliest_detection_lon: float
    expected_new_cells: float
    max_probability: float
    top_drivers: list[str]
    note: str = Field(description="one sentence on why this cluster matters")


class WatchCell(BaseModel):
    lat: float
    lon: float
    probability: float


class CrewRoute(BaseModel):
    base: str
    order: list[str]


class Briefing(BaseModel):
    date: str
    forecast_for: str
    headline: str
    clusters: list[ClusterBrief]
    new_fire_watch: list[WatchCell]
    dispatch: list[CrewRoute]
    briefing: str
    caveats: list[str]


TOOLS = [
    {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": {
            "top_n": {"type": "integer", "minimum": 1, "maximum": 10,
                      "description": "how many items to return"}},
            "required": []}}}
    for name, desc in [
        ("detect_clusters", "Fire clusters active today from VIIRS hotspots of the last 7 days, "
                            "with each cluster's earliest detection (not the ignition point)."),
        ("forecast_spread", "Next-day spread forecast per cluster: expected newly burning 1.1 km "
                            "cells, max cell probability, peat share, rain and top model drivers."),
        ("forecast_new_fires", "Next-day probability of new fires in quiet 11 km cells (no fire "
                               "in the last 7 days), top cells first."),
        ("triage_clusters", "Tier counts (P1 brigade now, P2 village fire watch, P3 monitor) "
                            "from peat-weighted forecast risk, and the top P1 clusters."),
        ("plan_dispatch", "Crew routes from an exact solver: for each base, the order of cluster "
                          "visits with arrival hours, maximising peat-weighted risk covered."),
        ("find_water_sources", "Nearest visible river to each stop of the top crew routes, from "
                               "SAM 3.1 masks on Sentinel-2 imagery (distance, direction). "
                               "top_n = number of routes."),
    ]
]

SYSTEM = """You are the analyst of Bara, a peat fire forecast for Sumatra and Kalimantan.
You write the daily briefing for a fire brigade commander (Manggala Agni / BPBD).

Rules:
- Get every fact from the tools. Call all six: detect_clusters, forecast_spread,
  forecast_new_fires, triage_clusters, plan_dispatch and find_water_sources (top_n 3).
- For each crew stop, give the nearest river from find_water_sources (distance and direction)
  or say no river was seen within the searched distance. Canals are not detected; never
  claim there is no water at all.
- Copy numbers exactly as the tools give them. Do not add, average or otherwise compute new
  numbers. You may write a probability as a percentage (0.742 -> 74.2%).
- Refer to clusters by their cluster_id (C1, C4, ...). Keep the forecast_spread order.
- The earliest detection is the first satellite detection, not the ignition point. Say so.
- Tiers and crew routes come from the tools. Explain them; never change a tier or a visit
  order. Visiting a fire is not extinguishing it.
- Place and province names only from the tools' location, base and province fields.

When you have the tool results, reply with ONE JSON object and nothing else:
{"date": str, "forecast_for": str, "headline": str,
 "clusters": [{"cluster_id": str, "province": str, "tier": str,
               "earliest_detection_lat": float, "earliest_detection_lon": float,
               "expected_new_cells": float, "max_probability": float, "top_drivers": [str],
               "note": str}],
 "new_fire_watch": [{"lat": float, "lon": float, "probability": float}],
 "dispatch": [{"base": str, "order": [cluster_id, ...]}],
 "briefing": str, "caveats": [str]}
Write in plain English for a fire commander, not a programmer: never use field names or
underscores, write shares as percentages (peat_share 0.95 -> "95% peat"), and write every
count as digits ("5 P1 clusters", not "five"). The briefing is at most 120 words in 2 or 3
short paragraphs and says why the top clusters matter (peat share, days since 10 mm of rain,
detections today), not only the forecast numbers. Leave definitions and caveats out of the
briefing; they belong in caveats.
Each note is one sentence specific to that cluster: its peat_share, days_since_10mm and its
first top driver. Do not repeat the caveats inside the notes.
Use the top 5 spread clusters, the top 3 new-fire cells and the top 3 crew routes. The
briefing says which crew goes where first, e.g. "Palangkaraya crew: C4, then C11, then C2"."""

NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*")
CLUSTER_ID = re.compile(r"\bC\d+\b")


def allowed_numbers(obj) -> list[float]:
    """Every number in the tool outputs, including numbers inside strings and keys (10mm)."""
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, str):
        return [float(t) for t in re.findall(r"\d+(?:\.\d+)?", obj)]
    if isinstance(obj, dict):
        return [x for k, v in obj.items() for x in allowed_numbers(k) + allowed_numbers(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in allowed_numbers(v)]
    return []


def readings(token: str) -> list[tuple[float, int]]:
    """Possible (value, decimals) readings of a number token, English or Indonesian style."""
    if re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", token):  # Indonesian thousands: 5.449
        return [(float(token.replace(".", "")), 0)]
    out = []
    for dec, thou in ((".", ","), (",", ".")):
        t = token.replace(thou, "")
        if t.count(dec) <= 1:
            t = t.replace(dec, ".")
            out.append((float(t), len(t.split(".")[1]) if "." in t else 0))
    return out


def grounded(token: str, pool: list[float], percent: bool) -> bool:
    for value, decimals in readings(token):
        tol = 0.5 * 10 ** -decimals + 1e-9
        for a in pool:
            if abs(value - (a * 100 if percent else a)) <= tol:
                return True
    return False


PROVINCE_NAMES = set(PROVINCE_ID) | set(PROVINCE_ID.values())


def check_grounding(brief: Briefing, results: dict[str, list[dict]]) -> list[str]:
    errors = []
    spread = {c["cluster_id"]: c for r in results.get("forecast_spread", []) for c in r["clusters"]}
    watch = [(c["lat"], c["lon"], c["probability"])
             for r in results.get("forecast_new_fires", []) for c in r["cells"]]
    routes = {r["base"]: [s["cluster_id"] for s in r["stops"]]
              for d in results.get("plan_dispatch", []) for r in d["routes"]}
    for c in brief.clusters:
        tool = spread.get(c.cluster_id)
        if tool is None:
            errors.append(f"{c.cluster_id} is not in the forecast_spread output")
            continue
        for field, value in (("province", tool["location"]["province"]), ("tier", tool["tier"])):
            if getattr(c, field) != value:
                errors.append(f"{c.cluster_id}.{field} is {getattr(c, field)}, tool says {value}")
        pairs = [("earliest_detection_lat", tool["earliest_detection"]["lat"]),
                 ("earliest_detection_lon", tool["earliest_detection"]["lon"]),
                 ("expected_new_cells", tool["expected_new_cells"]),
                 ("max_probability", tool["max_probability"])]
        for field, value in pairs:
            if abs(getattr(c, field) - value) > 1e-6:
                errors.append(f"{c.cluster_id}.{field} is {getattr(c, field)}, tool says {value}")
    for w in brief.new_fire_watch:
        if not any(abs(w.lat - a) < 1e-6 and abs(w.lon - b) < 1e-6 and abs(w.probability - p) < 1e-6
                   for a, b, p in watch):
            errors.append(f"new_fire_watch cell ({w.lat}, {w.lon}, {w.probability}) is not in the tool output")

    for r in brief.dispatch:
        if routes.get(r.base) != r.order:
            errors.append(f"route {r.base} {r.order} does not match plan_dispatch {routes.get(r.base)}")

    pool = allowed_numbers(results)
    tool_text = json.dumps(results, ensure_ascii=False)
    texts = [brief.headline, brief.briefing, *brief.caveats, *(c.note for c in brief.clusters)]
    for text in texts:
        for name in PROVINCE_NAMES:
            if name in text and name not in tool_text:
                errors.append(f"province {name} in the text is not in any tool output")
        text = CLUSTER_ID.sub("", text)
        for m in NUMBER.finditer(text):
            token = m.group(0).rstrip(".,")
            percent = text[m.end():m.end() + 2].lstrip().startswith("%")
            if not grounded(token, pool, percent):
                errors.append(f"number {token}{'%' if percent else ''} in the text is not in any tool output")
    return errors


def parse_final(content: str) -> Briefing:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return Briefing.model_validate_json(text)


def run(date: str, use_cache: bool = True) -> dict:
    tools = FireTools(date)
    llm = MetaLLM(use_cache=use_cache)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Write the briefing for {date}."}]
    results: dict[str, list[dict]] = {}
    trace, brief, errors, repairs = [], None, [], 0
    max_tokens = MAX_TOKENS

    for step in range(MAX_STEPS):
        reply = llm.chat(messages, tools=TOOLS, max_tokens=max_tokens, tag=f"agent {date} step {step}")
        msg = reply.choices[0].message
        if reply.choices[0].finish_reason == "length":
            errors = [f"reply cut off at max_tokens={max_tokens}"]
            trace.append({"step": step, "cut_off": max_tokens})
            if max_tokens >= MAX_TOKENS_RETRY:
                break
            max_tokens = MAX_TOKENS_RETRY
            continue
        if msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                top_n = min(max(int(args.get("top_n", 8)), 1), 10)
                try:
                    result = getattr(tools, tc.function.name)(top_n=top_n)
                    results.setdefault(tc.function.name, []).append(result)
                except AttributeError:
                    result = {"error": f"unknown tool {tc.function.name}"}
                trace.append({"step": step, "tool": tc.function.name, "top_n": top_n})
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, separators=(",", ":"))})
            continue

        content = msg.content or ""
        messages.append({"role": "assistant", "content": content})
        try:
            brief = parse_final(content)
            errors = check_grounding(brief, results)
        except ValidationError as e:
            brief, errors = None, [f"schema: {err['loc']}: {err['msg']}" for err in e.errors()]
        except json.JSONDecodeError as e:
            brief, errors = None, [f"not valid JSON: {e}"]
        trace.append({"step": step, "final": True, "errors": errors})
        if not errors or repairs >= MAX_REPAIRS:
            break
        repairs += 1
        messages.append({"role": "user", "content": "Fix these problems and reply with the "
                         "corrected JSON object only:\n- " + "\n- ".join(errors)})

    return {
        "date": date,
        "briefing": brief.model_dump() if brief else None,
        "grounding_errors": errors,
        "passed": brief is not None and not errors,
        "repairs": repairs,
        "tool_trace": trace,
        "tool_results": results,
        "llm_calls": llm.run_calls,
        "run_cost_usd": round(llm.run_cost, 6),
    }


def to_markdown(run_out: dict) -> str:
    b = run_out["briefing"]
    lines = [f"# Bara briefing, {run_out['date']}", ""]
    if b:
        lines += [f"**{b['headline']}**", "", b["briefing"], "",
                  f"## Clusters (forecast for {b['forecast_for']})", "",
                  "| Cluster | Tier | Province | Earliest detection (lat, lon) | Expected new cells "
                  "| Max prob. | Note |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f"| {c['cluster_id']} | {c['tier']} | {c['province']} "
                  f"| {c['earliest_detection_lat']}, {c['earliest_detection_lon']} "
                  f"| {c['expected_new_cells']} | {c['max_probability']} | {c['note']} |"
                  for c in b["clusters"]]
        lines += ["", "## Crew visit order", ""]
        lines += [f"- {r['base']} crew: {', then '.join(r['order'])}" for r in b["dispatch"]]
        lines += ["", "## New-fire watch", ""]
        lines += [f"- {w['lat']}, {w['lon']}: {w['probability']}" for w in b["new_fire_watch"]]
        lines += ["", "## Caveats", ""] + [f"- {c}" for c in b["caveats"]]
    status = "passed" if run_out["passed"] else "FAILED"
    lines += ["", "---", f"Checks: {status}, repairs {run_out['repairs']}, "
              f"{len(run_out['llm_calls'])} LLM calls, ${run_out['run_cost_usd']:.4f}"]
    lines += [f"- {e}" for e in run_out["grounding_errors"]]
    return "\n".join(lines) + "\n"


def save_run(out: dict) -> str:
    """Write outputs/briefing_<date>.json and .md; return the markdown."""
    OUT.mkdir(exist_ok=True)
    tag = out["date"].replace("-", "")
    (OUT / f"briefing_{tag}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False),
                                              encoding="utf-8")
    md = to_markdown(out)
    (OUT / f"briefing_{tag}.md").write_text(md, encoding="utf-8")
    return md


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    out = run(args.date, use_cache=not args.no_cache)
    print(save_run(out))
    for c in out["llm_calls"]:
        source = "cache" if c["cache_hit"] else f"{c['seconds']} s"
        print(f"{c['tag']}: {c['prompt_tokens']} in, {c['completion_tokens']} out "
              f"(reasoning {c['reasoning_tokens']}), {c['finish_reason']}, {source}, "
              f"${c['cost_usd']:.4f}")
    print(f"total logged spend ${spent_usd():.4f}")


if __name__ == "__main__":
    main()
