"""Replay Triage and Dispatch over a fire season and score them on what burned the next day.

For each day, plans are made from that day's data only; actual_new_cells (candidate cells
that had a detection the next day) is used only for scoring. Same caveat as the forecast:
2019 was the early-stopping year, so the 2023 replay is the real test.

Triage score: share of tomorrow's new fire cells that fall in P1 clusters, against a
persistence triage with the same tier sizes (ranked by detections today).
Dispatch score: tomorrow's new fire cells in the clusters crews visit, per method, plus an
oracle that knows tomorrow (upper bound for this crew setup).

Usage:
    python src/replay.py --start 2019-08-01 --end 2019-10-31
"""
import argparse
from pathlib import Path

import pandas as pd

from dispatch import METHODS, plan
from places import crew_bases
from tools import FireTools
from triage import persistence_tiers

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"


def score_day(date: pd.Timestamp, bases: pd.DataFrame) -> dict:
    t = FireTools(f"{date:%Y-%m-%d}").table()
    actual = t["actual_new_cells"]
    peat_actual = actual * (1 + t["peat_share"])
    row = {"date": date, "clusters": len(t), "actual_total": actual.sum(),
           "peat_actual_total": peat_actual.sum()}
    persist = persistence_tiers(t)
    for tier in ("P1", "P2"):
        row[f"n_{tier}"] = int((t["tier"] == tier).sum())
        row[f"actual_in_{tier}"] = actual[t["tier"] == tier].sum()
        row[f"actual_in_{tier}_persist"] = actual[persist == tier].sum()

    oracle = t.assign(risk=peat_actual, tier=(actual > 0).map({True: "P1", False: "P3"}))
    for method in (*METHODS, "oracle"):
        routes = plan(oracle, bases, "solver") if method == "oracle" else plan(t, bases, method)
        visited = [s["cluster_id"] for r in routes for s in r["stops"]]
        row[f"{method}_visits"] = len(visited)
        row[f"{method}_actual"] = actual.loc[visited].sum()
        row[f"{method}_peat_actual"] = peat_actual.loc[visited].sum()
    return row


def summarise(df: pd.DataFrame) -> str:
    tot = df["actual_total"].sum()
    clusters = df["clusters"].sum()
    lines = ["Triage (share of next-day new fire cells, all days pooled):", "",
             "| Tier | Share of clusters | Forecast triage | Persistence triage |", "|---|---|---|---|"]
    for tier in ("P1", "P2"):
        lines.append(f"| {tier} | {df[f'n_{tier}'].sum() / clusters:.1%} "
                     f"| {df[f'actual_in_{tier}'].sum() / tot:.1%} "
                     f"| {df[f'actual_in_{tier}_persist'].sum() / tot:.1%} |")
    lines += ["", "Dispatch (next-day new fire cells in visited clusters, all crews, all days):", "",
              "| Plan | Visits | New fire cells reached | Peat-weighted | Share of oracle (peat-weighted) |",
              "|---|---|---|---|---|"]
    oracle = df["oracle_peat_actual"].sum()
    for method in (*METHODS, "oracle"):
        lines.append(f"| {method} | {df[f'{method}_visits'].sum():,} | {df[f'{method}_actual'].sum():,.0f} "
                     f"| {df[f'{method}_peat_actual'].sum():,.0f} "
                     f"| {df[f'{method}_peat_actual'].sum() / oracle:.1%} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    bases = crew_bases()
    rows = []
    for date in pd.date_range(args.start, args.end):
        rows.append(score_day(date, bases))
        print(f"{date:%Y-%m-%d}: {rows[-1]['clusters']} clusters, solver reached "
              f"{rows[-1]['solver_actual']:.0f} of {rows[-1]['actual_total']:.0f} new fire cells",
              flush=True)
    df = pd.DataFrame(rows)
    tag = f"{pd.Timestamp(args.start):%Y%m%d}_{pd.Timestamp(args.end):%Y%m%d}"
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / f"replay_{tag}.csv", index=False)
    text = summarise(df)
    (OUT / f"replay_{tag}.md").write_text(text, encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    main()
