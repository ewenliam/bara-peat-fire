"""Fusion: WhatsApp field reports change the day's crew plan.

Claude-written and deterministic (PLAN.md). Muse models turn a voice note into a report
(intake.py); the rules here decide what it means, and the exact solver re-plans. The rules
are policy choices kept in the open, like the tiers in triage.py:

    flames or smouldering seen within MATCH_KM of a satellite cluster -> confirmed, at least P1
    smoke seen near a cluster, or a burn scar (peat can smoulder below) -> at least P2
    no fire seen at a cluster -> reported clear: P3 for today's plan, ask for a second look
    flames or smouldering with no cluster within MATCH_KM -> new fire, added as P1
    fire state unclear, or no usable location -> no change; ask the reporter

Location comes from the spoken place name (geocode.py, exact GeoNames matches only). An
ambiguous name is resolved only when exactly one candidate lies within NEAR_FIRE_KM of a
fire cluster; otherwise the reporter is asked which one.

Usage (apply the last day's WhatsApp reports to a replay day, no API calls):
    python src/fusion.py --date 2019-09-15
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from dispatch import plan
from geocode import geocode, load_gazetteer
from places import STUDY_PROVINCES, crew_bases, km_xy
from triage import P1_MIN, P2_MIN

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "whatsapp" / "reports.jsonl"
MATCH_KM = 5.0       # a report speaks for a cluster within this distance (as for photos in reports.py)
NEAR_FIRE_KM = 15.0  # an ambiguous place name is accepted only if one candidate is this close to a fire
RECENT_H = 24        # live reports from the last day are applied to the plan
TIER_RANK = {"P1": 1, "P2": 2, "P3": 3}


def _distances(table: pd.DataFrame, lat: float, lon: float) -> np.ndarray:
    return np.hypot(*(km_xy(table["lat"].to_numpy(), table["lon"].to_numpy()) - km_xy([lat], [lon])).T)


def _near_town(gazetteer: pd.DataFrame, lat: float, lon: float, skip_key: str) -> str:
    """Nearest town or regency seat, so two places with the same name can be told apart."""
    towns = gazetteer[(gazetteer["kind"] == "town") & (gazetteer["key"] != skip_key)].drop_duplicates("geonameid")
    d = np.hypot(*(km_xy(towns["lat"].to_numpy(), towns["lon"].to_numpy()) - km_xy([lat], [lon])).T)
    i = int(d.argmin())
    return f"{d[i]:.0f} km from {towns['name'].iat[i]}"


def resolve_location(report: dict, table: pd.DataFrame, gazetteer: pd.DataFrame) -> dict:
    """Where the report is: {"status": "located" | "ask", lat, lon, place, why, question, options}."""
    place = (report.get("report") or {}).get("place_text")
    found = [c for c in geocode(place, gazetteer, max_candidates=20) if c["province"] in STUDY_PROVINCES]
    out = {"status": "ask", "lat": None, "lon": None, "place": None, "why": None, "question": None, "options": []}
    if not found:
        out["question"] = (f'I could not find "{place}". ' if place else "") + \
            "Where is the fire? Send the name of the nearest village."  # location pins: not handled yet
        return out
    if len(found) > 1:
        near = [c for c in found if _distances(table, c["lat"], c["lon"]).min() <= NEAR_FIRE_KM]
        if len(near) != 1:
            key = found[0]["matched"].replace(" ", "")
            out["options"] = [f'{c["name"]}, {c["province"]}, {_near_town(gazetteer, c["lat"], c["lon"], key)}'
                              for c in found[:3]]
            out["question"] = f'There are {len(found)} places called "{found[0]["matched"]}". Which one: ' + \
                "; ".join(out["options"]) + "?"
            return out
        found, why = near, f"{len(found)} places match; only this one is within {NEAR_FIRE_KM:g} km of a fire cluster"
    else:
        why = "only match in Sumatra and Kalimantan"
    c = found[0]
    out.update(status="located", lat=c["lat"], lon=c["lon"], place=c["name"], why=why)
    return out


def decide(report: dict, loc: dict, table: pd.DataFrame) -> dict:
    """What one located report means for the plan (a pure rule, no side effects)."""
    state = (report.get("report") or {}).get("fire_state", "unclear")
    d = {"fire_state": state, "kind": "no_change", "cluster_id": None, "km": None,
         "tier_before": None, "tier_after": None}
    if loc["status"] != "located" or state == "unclear":
        return d
    dist = _distances(table, loc["lat"], loc["lon"])
    i = int(dist.argmin())
    cid, km = table.index[i], float(dist[i])
    burning = state in ("active_flames", "smouldering_peat")
    if km > MATCH_KM:
        if burning:
            d.update(kind="new_fire", km=round(km, 1), tier_after="P1")
        return d
    before = table["tier"].iat[i]
    d.update(cluster_id=cid, km=round(km, 1), tier_before=before)
    if burning:
        d.update(kind="confirmed", tier_after="P1")
    elif state in ("smoke_only", "burn_scar"):
        d.update(kind="smoke_or_scar", tier_after=min(before, "P2", key=TIER_RANK.get))
    elif state == "no_fire":
        d.update(kind="reported_clear", tier_after="P3")
    return d


def apply(table: pd.DataFrame, decisions: list[dict], locations: list[dict]) -> pd.DataFrame:
    """A copy of the plan table with tiers changed and new fires added."""
    t = table.copy()
    for n, (d, loc) in enumerate(zip(decisions, locations), start=1):
        if d["kind"] in ("confirmed", "smoke_or_scar", "reported_clear"):
            cid, tier = d["cluster_id"], d["tier_after"]
            floor = {"P1": P1_MIN, "P2": P2_MIN}.get(tier)
            risk = t.at[cid, "risk"]
            t.at[cid, "risk"] = max(risk, floor) if floor else min(risk, P2_MIN * 0.99)
            t.at[cid, "tier"] = tier
        elif d["kind"] == "new_fire":
            fid = f"R{n}"
            d["cluster_id"] = fid
            t.loc[fid, ["lat", "lon", "risk", "tier", "n_today"]] = [loc["lat"], loc["lon"], P1_MIN, "P1", 0]
    return t


def route_diff(before: list[dict], after: list[dict]) -> list[dict]:
    """Crews whose stops changed, with what was added and dropped."""
    old = {(r["base"], r["crew"]): [s["cluster_id"] for s in r["stops"]] for r in before}
    new = {(r["base"], r["crew"]): [s["cluster_id"] for s in r["stops"]] for r in after}
    out = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key, []), new.get(key, [])
        if a != b:
            out.append({"base": key[0], "crew": key[1], "before": a, "after": b,
                        "added": [c for c in b if c not in a], "dropped": [c for c in a if c not in b]})
    return out


def recent_reports(hours: float = RECENT_H, now: float | None = None) -> list[dict]:
    """WhatsApp report rows from the last `hours` with an extracted report, latest row per id."""
    if not REPORTS.exists():
        return []
    cutoff = (now or time.time()) - hours * 3600
    latest = {}
    for line in REPORTS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            latest[row["id"]] = row
    keep = []
    for row in latest.values():
        when = time.mktime(time.strptime(row["received_at"], "%Y-%m-%dT%H:%M:%S"))
        if when >= cutoff and row["status"] in ("ok", "needs_review") and (row.get("extraction") or {}).get("report"):
            keep.append(row)
    return sorted(keep, key=lambda r: r["received_at"])


def reply_text(row: dict, loc: dict, d: dict, routes: list[dict], diff: list[dict]) -> str:
    """The message Bara would send back (drafted only; sending needs the user's approval)."""
    if loc["status"] == "ask":
        return loc["question"]
    if d["fire_state"] == "unclear":
        return f"Report received near {loc['place']}. What do you see: flames, smouldering ground, smoke only, or no fire?"
    seen = {"active_flames": "flames", "smouldering_peat": "smouldering peat", "smoke_only": "smoke",
            "burn_scar": "a burn scar", "no_fire": "no fire"}[d["fire_state"]]
    if d["kind"] == "new_fire":
        head = f"New fire ({seen}) near {loc['place']}, not seen by satellite. Added as P1 {d['cluster_id']}."
    elif d["cluster_id"]:
        change = f"{d['tier_before']} to {d['tier_after']}" if d["tier_before"] != d["tier_after"] else f"stays {d['tier_after']}"
        head = f"{seen.capitalize()} near {loc['place']} matches {d['cluster_id']} ({d['km']} km): {change}."
        if d["kind"] == "reported_clear":
            head += " Please check again tomorrow; peat can smoulder below the surface."
    else:
        head = f"{seen.capitalize()} near {loc['place']}; no satellite cluster within {MATCH_KM:g} km, plan unchanged."
    changed = {(c["base"], c["crew"]) for c in diff}
    if d["kind"] in ("confirmed", "smoke_or_scar", "new_fire"):
        visits = [r for r in routes if d["cluster_id"] in [s["cluster_id"] for s in r["stops"]]]
        for r in visits:
            order = [s["cluster_id"] for s in r["stops"]]
            head += (f" {r['base']} crew {'now' if (r['base'], r['crew']) in changed else 'already'} goes there "
                     f"(stop {order.index(d['cluster_id']) + 1} of {len(order)}: {', '.join(order)}).")
        if not visits:
            head += " No crew can reach it today within the working day; it stays on the list."
    for c in diff:
        if d["cluster_id"] in c["dropped"]:
            head += f" {c['base']} crew no longer goes there; its route is now {', '.join(c['after']) or 'empty'}."
    return head


def fuse_day(table: pd.DataFrame, routes: list[dict], rows: list[dict]) -> dict:
    """Apply report rows to one day's plan table and routes; return what changed."""
    gaz = load_gazetteer()
    locations = [resolve_location(r["extraction"], table, gaz) for r in rows]
    decisions = [decide(r["extraction"], loc, table) for r, loc in zip(rows, locations)]
    new_table = apply(table, decisions, locations)
    new_routes = sorted(plan(new_table, crew_bases(), "solver"), key=lambda r: -r["risk_covered"])
    diff = route_diff(routes, new_routes)
    reports = []
    for r, loc, d in zip(rows, locations, decisions):
        reports.append({"id": r["id"], "received_at": r["received_at"], "type": r["type"],
                        "transcript": r["transcript"], "report": r["extraction"]["report"],
                        "grounded": r["extraction"]["grounded"], "location": loc, "decision": d,
                        "reply": reply_text(r, loc, d, new_routes, diff)})
    covered = lambda rs: round(sum(x["risk_covered"] for x in rs), 1)  # noqa: E731
    return {"reports": reports, "route_diff": diff, "routes": new_routes, "table": new_table,
            "risk_covered_before": covered(routes), "risk_covered_after": covered(new_routes)}


def main() -> None:
    from tools import FireTools  # loads the models; only needed for the command line
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--hours", type=float, default=RECENT_H)
    args = ap.parse_args()
    t = FireTools(args.date)
    out = fuse_day(t.table(), t._routes(), recent_reports(args.hours))
    for r in out["reports"]:
        print(r["received_at"], r["location"]["status"], r["decision"]["kind"], "|", r["reply"])
    for c in out["route_diff"]:
        print(f"{c['base']} crew {c['crew']}: {c['before']} -> {c['after']}")
    print("risk covered:", out["risk_covered_before"], "->", out["risk_covered_after"])


if __name__ == "__main__":
    main()
