"""Map self-check: Muse Spark 1.3 looks at the map the commander sees and checks it against the plan.

The front end sends a snapshot of the live map (clusters, crew routes, labels). Muse Spark gets
the image plus the plan data and reports problems a solver cannot see from numbers alone, for
example a route drawn across open sea or a high-risk cluster that no route comes near. Cluster
ids and bases in the reply must exist in the plan, or the finding is dropped.

The review is advisory and can be wrong: on 2019-09-15, before the plan JSON carried islands,
it flagged a Pangkalpinang route as crossing the Bangka Strait, but every stop was on Bangka.
"""
import json
import re
from typing import Literal

import numpy as np
from pydantic import BaseModel, ValidationError

from llm import MetaLLM
from places import ISLAND, crew_bases, locate
from tools import FireTools

MAX_TOKENS = 8000
CLUSTER_RE = r"\bC\d+\b"

PROMPT = """You review the map of a peat fire crew plan before it goes to a commander.
The image is the live map on a light paper-coloured basemap: dark red dots are P1 clusters
(brigade now), ochre-brown dots P2, dark green lines are crew routes from a base (pale dot with a
dark outline) through numbered stops labelled like "1 C4". The JSON below is
the plan the map was drawn from.

Check the map against the plan and against the geography you can see:
1. Do the drawn routes match the plan (same bases, same stop order)?
2. Does any route cross open sea or a large water body, which a ground crew cannot drive? The
   plan JSON gives the island of every base and stop; a route whose stops are all on the
   base's island is not a sea crossing, even if the straight line passes near a coast.
3. Are there large red P1 clusters on the map that no route comes near? Name them only by ids
   from the plan JSON.
4. Anything else on the map that looks wrong or confusing to a commander.

Reply with ONE JSON object only:
{"findings": [{"check": "routes_match" | "crosses_water" | "uncovered_p1" | "other",
               "status": "ok" | "warning", "detail": "one sentence", "ids": [cluster ids or base names]}],
 "summary": "one sentence"}

Plan JSON:
"""


class Finding(BaseModel):
    check: Literal["routes_match", "crosses_water", "uncovered_p1", "other"]
    status: Literal["ok", "warning"]
    detail: str
    ids: list[str] = []


class Review(BaseModel):
    findings: list[Finding]
    summary: str


def plan_json(t: FireTools, routes_shown: int) -> dict:
    table = t.table()
    routes = t._routes()[:routes_shown]
    visited = {s["cluster_id"] for r in routes for s in r["stops"]}
    p1 = table[table["tier"] == "P1"].sort_values("risk", ascending=False).head(15)
    bases = crew_bases().set_index("name")

    def where(lat: float, lon: float) -> dict:
        prov = locate(np.array([lat]), np.array([lon]))["province"].iat[0]
        return {"lat": round(float(lat), 3), "lon": round(float(lon), 3), "island": ISLAND.get(prov, prov)}

    return {"routes": [{"base": r["base"], **where(bases.loc[r["base"], "lat"], bases.loc[r["base"], "lon"]),
                        "stops": [{"id": s["cluster_id"], **where(table.loc[s["cluster_id"], "lat"],
                                                                   table.loc[s["cluster_id"], "lon"])}
                                  for s in r["stops"]]} for r in routes],
            "top_p1": [{"id": cid, "risk": round(float(c["risk"]), 1), "visited": cid in visited,
                        "lat": round(float(c["lat"]), 3), "lon": round(float(c["lon"]), 3)} for cid, c in p1.iterrows()]}


def review(date: str, image_data_url: str, routes_shown: int = 12, use_cache: bool = True) -> dict:
    t = FireTools(date)
    plan = plan_json(t, routes_shown)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT + json.dumps(plan)},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]}]
    llm = MetaLLM(use_cache=use_cache)
    reply = llm.chat(messages, max_tokens=MAX_TOKENS, tag=f"selfcheck {date}")
    text = (reply.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        rv = Review.model_validate_json(text)
    except ValidationError as e:
        return {"review": None, "error": f"schema: {e.errors()[0]['msg']}", "llm_calls": llm.run_calls,
                "cost_usd": round(llm.run_cost, 6)}
    known = {r["base"] for r in plan["routes"]} | set(t.table().index)
    dropped = []
    for f in rv.findings:
        bad = [i for i in f.ids + re.findall(CLUSTER_RE, f.detail) if i not in known]
        if bad:
            dropped.append({"finding": f.model_dump(), "unknown_ids": bad})
    kept = [f.model_dump() for f in rv.findings
            if not any(i not in known for i in f.ids + re.findall(CLUSTER_RE, f.detail))]
    return {"review": {"findings": kept, "summary": rv.summary}, "dropped": dropped, "plan": plan,
            "llm_calls": llm.run_calls, "cost_usd": round(llm.run_cost, 6)}
