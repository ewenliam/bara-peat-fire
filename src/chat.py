"""Ask Bara: a commander asks questions or what-ifs; Muse Spark answers by calling our tools.

Muse Spark never plans on its own. For "what if we only had 2 crews in Palangkaraya?" it calls
`what_if_dispatch`, which re-runs the exact solver with the new crew setup and returns the
change against today's plan. The answer is checked like the briefing: every number must come
from a tool output and every cluster id must exist, or it is sent back once for a repair.

Usage:
    python src/chat.py --date 2019-09-15 "What if Palangkaraya had 2 crews?"
"""
import argparse
import json

import numpy as np

from agent import CLUSTER_ID, NUMBER, allowed_numbers, grounded
from dispatch import DEFAULT, Setup, plan
from llm import MetaLLM
from places import crew_bases
from tools import FireTools, _drivers

MAX_STEPS = 6
MAX_TOKENS = 8000

TOOLS = [
    {"type": "function", "function": {
        "name": "situation",
        "description": "Today's numbers: hotspots, clusters per tier, expected new fire cells, "
                       "top P1 clusters with place and province, and today's crew plan summary.",
        "parameters": {"type": "object", "properties": {
            "top_n": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": []}}},
    {"type": "function", "function": {
        "name": "cluster_details",
        "description": "Everything about one cluster: forecast, peat, rain, model drivers, "
                       "earliest detection, location, and which crew visits it in today's plan.",
        "parameters": {"type": "object", "properties": {
            "cluster_id": {"type": "string", "description": "e.g. C4"}}, "required": ["cluster_id"]}}},
    {"type": "function", "function": {
        "name": "what_if_dispatch",
        "description": "Re-run the exact dispatch solver with a different crew setup and compare "
                       "it with today's plan (risk covered, P1 clusters reached, routes). Today's "
                       "plan is 1 crew at every base. Set only what the question changes; call "
                       "it once per scenario the commander asks about.",
        "parameters": {"type": "object", "properties": {
            "crews_per_base": {"type": "integer", "minimum": 1, "maximum": 4,
                               "description": "crews at every base (default 1)"},
            "crews_at": {"type": "object", "additionalProperties": {"type": "integer"},
                         "description": "crews at named bases, e.g. {\"Palangkaraya\": 2}; "
                                        "other bases keep crews_per_base"},
            "only_bases": {"type": "array", "items": {"type": "string"},
                           "description": "use only these bases (names from list_bases)"},
            "exclude_bases": {"type": "array", "items": {"type": "string"}},
            "day_hours": {"type": "number", "minimum": 4, "maximum": 16},
            "speed_kmh": {"type": "number", "minimum": 5, "maximum": 80},
            "hours_on_site": {"type": "number", "minimum": 0.5, "maximum": 6}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "list_bases",
        "description": "Crew bases (provincial capitals, an assumption) with their provinces.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]

SYSTEM = """You are Bara, the assistant of a peat fire brigade commander in Sumatra and
Kalimantan. The forecast date is {date} (forecast for the next day).

Rules:
- Answer only from tool results. For any question about crews, bases, hours or speed, call
  what_if_dispatch; never work out a plan or a number yourself.
- Copy numbers exactly as the tools give them; do not add, subtract or average. The tools
  already give the changes against today's plan.
- Refer to clusters by id (C4) and say where they are (place, province) when useful.
- Plain English for a commander: no field names or underscores, counts as digits, at most
  120 words. Say clearly when something is an assumption (bases, speeds, hours).
- Visiting a fire is not extinguishing it."""


def _summary(routes: list[dict], table) -> dict:
    visited = {s["cluster_id"] for r in routes for s in r["stops"]}
    p1 = table[table["tier"] == "P1"]
    return {"crews_used": len(routes), "clusters_visited": len(visited),
            "risk_covered": round(sum(r["risk_covered"] for r in routes), 1),
            "p1_reached": int(p1.index.isin(list(visited)).sum()), "p1_total": len(p1)}


class ChatTools:
    def __init__(self, date: str):
        self.t = FireTools(date)
        self.table = self.t.table()
        self.bases = crew_bases()
        self.scenario_routes: list[dict] | None = None

    def situation(self, top_n: int = 5) -> dict:
        tri = self.t.triage_clusters(top_n=top_n)
        for c in tri["top_p1"]:
            c["location"] = self.t._place(c["cluster_id"])
        det = self.t.detect_clusters(top_n=1)
        spread = self.t.forecast_spread(top_n=1)
        return {"forecast_for": tri["forecast_for"], "hotspots_today": det["detections_today"],
                "active_clusters": det["clusters_active_today"],
                "clusters_per_tier": tri["clusters_per_tier"],
                "expected_new_cells_total": spread["expected_new_cells_total"],
                "tier_rule": tri["rule"], "top_p1": tri["top_p1"],
                "todays_plan": _summary(self.t._routes(), self.table)}

    def cluster_details(self, cluster_id: str) -> dict:
        cid = cluster_id.strip().upper()
        if cid not in self.table.index:
            return {"error": f"no cluster {cid} on this date"}
        rec = self.t._forecast_record(cid)
        rec["top_drivers"] = _drivers_for(self.t, cid)
        for r in self.t._routes():
            for i, s in enumerate(r["stops"], start=1):
                if s["cluster_id"] == cid:
                    rec["todays_plan"] = {"crew_base": r["base"], "visit_number": i, "arrive_h": s["arrive_h"]}
        rec.setdefault("todays_plan", "not visited by any crew today")
        return rec

    def _nearest_base(self, lat: float, lon: float) -> str:
        d = (self.bases["lat"] - lat) ** 2 + ((self.bases["lon"] - lon) * np.cos(np.radians(lat))) ** 2
        return self.bases["name"].iat[int(d.to_numpy().argmin())]

    def list_bases(self) -> dict:
        return {"bases": self.bases[["name", "province"]].to_dict(orient="records"),
                "note": "provincial capitals used as assumed crew bases"}

    def what_if_dispatch(self, crews_per_base: int = 1, crews_at: dict[str, int] | None = None,
                         only_bases: list[str] | None = None,
                         exclude_bases: list[str] | None = None, day_hours: float = DEFAULT.day_h,
                         speed_kmh: float = DEFAULT.speed_kmh,
                         hours_on_site: float = DEFAULT.on_site_h) -> dict:
        names = set(self.bases["name"])
        crews_at = {k: max(0, min(int(v), 4)) for k, v in (crews_at or {}).items()}
        unknown = [b for b in (only_bases or []) + (exclude_bases or []) + list(crews_at) if b not in names]
        if unknown:
            return {"error": f"unknown bases {unknown}", "valid_bases": sorted(names)}
        bases = self.bases
        if only_bases:
            bases = bases[bases["name"].isin(only_bases)]
        if exclude_bases:
            bases = bases[~bases["name"].isin(exclude_bases)]
        setup = Setup(speed_kmh=speed_kmh, on_site_h=hours_on_site, day_h=day_hours,
                      crews_per_base=crews_per_base)
        routes = sorted(plan(self.table, bases, "solver", setup, crews_at),
                        key=lambda r: -r["risk_covered"])
        self.scenario_routes = routes
        base, new = _summary(self.t._routes(), self.table), _summary(routes, self.table)
        reached = {s["cluster_id"] for r in routes for s in r["stops"]}
        missed = self.table[(self.table["tier"] == "P1") & ~self.table.index.isin(list(reached))]
        change = new["risk_covered"] - base["risk_covered"]
        return {
            "scenario": {"crews_per_base": crews_per_base, "crews_at": crews_at, "bases": sorted(bases["name"]),
                         "day_hours": day_hours, "speed_kmh": speed_kmh, "hours_on_site": hours_on_site},
            "todays_plan": base, "scenario_plan": new,
            "change": {"risk_covered": round(change, 1),
                       "risk_covered_pct": round(100 * change / base["risk_covered"], 1) if base["risk_covered"] else None,
                       "p1_reached": new["p1_reached"] - base["p1_reached"],
                       "clusters_visited": new["clusters_visited"] - base["clusters_visited"]},
            "top_p1_not_reached": [{"cluster_id": cid, "risk": round(float(r["risk"]), 1),
                                    "nearest_base": self._nearest_base(r["lat"], r["lon"])}
                                   for cid, r in missed.sort_values("risk", ascending=False).head(8).iterrows()],
            "routes": [{"base": r["base"], "crew": r["crew"], "order": [s["cluster_id"] for s in r["stops"]],
                        "risk_covered": r["risk_covered"]} for r in routes[:6]],
        }


def _drivers_for(t: FireTools, cid: str) -> list[str]:
    t._score_cells()
    return _drivers(t.booster, t.X[t.cells["id"] == cid])


def check_answer(text: str, results: list[dict], known_ids: set[str]) -> list[str]:
    errors = []
    pool = allowed_numbers(results)
    tool_text = json.dumps(results)
    for cid in set(CLUSTER_ID.findall(text)):
        if cid not in known_ids or cid not in tool_text:
            errors.append(f"cluster {cid} is not in any tool output")
    clean = CLUSTER_ID.sub("", text)
    for m in NUMBER.finditer(clean):
        token = m.group(0).rstrip(".,")
        percent = clean[m.end():m.end() + 2].lstrip().startswith("%")
        if not grounded(token, pool, percent):
            errors.append(f"number {token}{'%' if percent else ''} is not in any tool output")
    return errors


def ask(date: str, messages: list[dict], use_cache: bool = True) -> dict:
    tools = ChatTools(date)
    llm = MetaLLM(use_cache=use_cache)
    convo = [{"role": "system", "content": SYSTEM.format(date=date)}] + [
        {"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
    results, trace, answer, errors, repaired = [], [], "", [], False
    for step in range(MAX_STEPS):
        reply = llm.chat(convo, tools=TOOLS, max_tokens=MAX_TOKENS, tag=f"chat {date} step {step}")
        msg = reply.choices[0].message
        if msg.tool_calls:
            convo.append({"role": "assistant", "content": msg.content or "",
                          "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                fn = getattr(tools, tc.function.name, None)
                try:
                    result = fn(**args) if fn and tc.function.name in {t["function"]["name"] for t in TOOLS} \
                        else {"error": f"unknown tool {tc.function.name}"}
                except TypeError as e:
                    result = {"error": f"bad arguments: {e}"}
                results.append(result)
                trace.append({"tool": tc.function.name, "args": args})
                convo.append({"role": "tool", "tool_call_id": tc.id,
                              "content": json.dumps(result, separators=(",", ":"), default=str)})
            continue
        answer = (msg.content or "").strip()
        errors = check_answer(answer, results, set(tools.table.index))
        if not errors or repaired:
            break
        repaired = True
        convo += [{"role": "assistant", "content": answer},
                  {"role": "user", "content": "Your answer has numbers or clusters that are not in "
                   "the tool results. Fix it using only tool numbers:\n- " + "\n- ".join(errors)}]
    scenario = None
    if tools.scenario_routes is not None:
        table = tools.table
        bases = tools.bases.set_index("name")
        scenario = [{"base": r["base"], "crew": r["crew"], "order": [s["cluster_id"] for s in r["stops"]],
                     "path": [[float(bases.loc[r["base"], "lon"]), float(bases.loc[r["base"], "lat"])]]
                     + [[float(table.loc[s["cluster_id"], "lon"]), float(table.loc[s["cluster_id"], "lat"])]
                        for s in r["stops"]]} for r in tools.scenario_routes]
    return {"answer": answer, "passed": bool(answer) and not errors, "errors": errors,
            "repaired": repaired, "tool_trace": trace, "scenario_routes": scenario,
            "llm_calls": llm.run_calls, "cost_usd": round(llm.run_cost, 6)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("question")
    args = parser.parse_args()
    out = ask(args.date, [{"role": "user", "content": args.question}])
    print(out["answer"])
    print(f"\npassed={out['passed']} repaired={out['repaired']} errors={out['errors']}")
    print("tools:", json.dumps(out["tool_trace"]))
    print(f"{len(out['llm_calls'])} calls, ${out['cost_usd']:.4f}")


if __name__ == "__main__":
    main()
