"""Precompute one map frame per day for the season replay player in the web app.

Each frame holds the P1/P2 clusters (with where fire actually appeared the next day), Bara's
crew routes and the "biggest fire first" routes, so the player can step through a season
without recomputing. Totals per day come from the same plans as src/replay.py.

Usage:
    python src/replay_frames.py --start 2019-07-01 --end 2019-10-31
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from dispatch import plan
from places import crew_bases
from tools import FireTools

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"


def frame(day: pd.Timestamp, bases: pd.DataFrame) -> dict:
    t = FireTools(f"{day:%Y-%m-%d}")
    table = t.table()
    b = bases.set_index("name")

    def paths(routes: list[dict]) -> list[list[list[float]]]:
        return [[[round(float(b.loc[r["base"], "lon"]), 3), round(float(b.loc[r["base"], "lat"]), 3)]]
                + [[round(float(table.loc[s["cluster_id"], "lon"]), 3), round(float(table.loc[s["cluster_id"], "lat"]), 3)]
                   for s in r["stops"]] for r in routes]

    bara, biggest = plan(table, bases, "solver"), plan(table, bases, "biggest_today")
    reached = lambda routes: int(table.loc[[s["cluster_id"] for r in routes for s in r["stops"]], "actual_new_cells"].sum())
    shown = table[table["tier"].isin(["P1", "P2"]) | (table["actual_new_cells"] > 0)]
    return {
        "date": f"{day:%Y-%m-%d}",
        "clusters": [[round(float(r["lon"]), 3), round(float(r["lat"]), 3), {"P1": 1, "P2": 2, "P3": 3}[r["tier"]],
                      round(float(r["risk"]), 1), int(r["actual_new_cells"])] for _, r in shown.iterrows()],
        "bara": paths(bara), "biggest": paths(biggest),
        "next_day_cells": int(table["actual_new_cells"].sum()),
        "bara_reached": reached(bara), "biggest_reached": reached(biggest),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    bases = crew_bases()
    frames = []
    for day in pd.date_range(args.start, args.end):
        frames.append(frame(day, bases))
        print(f"{day:%Y-%m-%d}: {len(frames[-1]['clusters'])} clusters, Bara {frames[-1]['bara_reached']}, "
              f"biggest-first {frames[-1]['biggest_reached']} of {frames[-1]['next_day_cells']}", flush=True)
    path = OUT / f"replay_frames_{pd.Timestamp(args.start):%Y%m%d}_{pd.Timestamp(args.end):%Y%m%d}.json"
    path.write_text(json.dumps({"columns": ["lon", "lat", "tier", "risk", "next_day_cells"], "frames": frames},
                               separators=(",", ":")), encoding="utf-8")
    print(f"saved {path.name}, {path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
