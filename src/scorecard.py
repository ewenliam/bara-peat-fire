"""Live scorecard: the frozen forecast scored on 2026, a season it never saw.

Claude-written, exploratory (not pre-registered, unlike the 2023 test). The model was trained on
2012-2018 and 2020-2022 S-NPP data and frozen before 2026; here its inputs are NOAA-20 detections
and live rain (live.py), so this also tests that switch. Every day with a known next day is scored:
average precision against "yesterday's fires (3x3)" (the 2023 test's main baseline), and, in plain
words for the app, how many of each method's 200 most likely spots burned the next day.

Writes outputs/live_scorecard.json. No API calls.

Usage:
    python src/scorecard.py
"""
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
OUT = ROOT / "outputs" / "live_scorecard.json"
MODEL = ROOT / "models" / "lgbm_spread_y_2019_2019.txt"  # the frozen spread model (PREREGISTRATION_2023.md)
TOP_K = 200
N_BOOT = 200  # fewer than the 2023 test (1,000): 11M rows; enough for an exploratory 95% interval


def main() -> None:
    live = json.loads((PROC / "live_2026.json").read_text(encoding="utf-8"))
    last_scored = pd.Timestamp(live["live_day"]) - pd.Timedelta(days=1)  # its next day is the live day
    booster = lgb.Booster(model_file=str(MODEL))
    cols = booster.feature_name()
    extra = [c for c in ("date", "y", "n_today_r1") if c not in cols]  # n_today_r1 is also a model input
    df = pd.read_parquet(PROC / "samples_2026_2026_feat.parquet", columns=cols + extra)
    df = df[df["date"] <= last_scored]
    df["model"] = booster.predict(df[cols])
    rows = []
    for day, g in df.groupby("date"):
        y = g["y"].to_numpy()
        if y.sum() == 0:
            continue
        top = lambda s: float(y[np.argsort(-s.to_numpy(), kind="stable")[:TOP_K]].mean())  # noqa: E731
        rows.append({"date": f"{day:%Y-%m-%d}", "cells": len(g), "burned_next_day": int(y.sum()),
                     "ap_model": average_precision_score(y, g["model"]),
                     "ap_yesterday": average_precision_score(y, g["n_today_r1"]),
                     "top_model": top(g["model"]), "top_yesterday": top(g["n_today_r1"])})
    days = pd.DataFrame(rows)
    y_all = df["y"].to_numpy()
    season = {"from": days["date"].iloc[0], "to": days["date"].iloc[-1], "days": len(days),
              "cells": len(df), "burned_next_day": int(y_all.sum()),
              "ap_model": average_precision_score(y_all, df["model"]),
              "ap_yesterday": average_precision_score(y_all, df["n_today_r1"])}
    # day bootstrap of the AP gap, as in the 2023 test (seed 0), with N_BOOT resamples
    rng = np.random.default_rng(0)
    groups = [g.index.to_numpy() for _, g in df.groupby("date")]
    gaps = []
    for _ in range(N_BOOT):
        idx = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        s = df.loc[idx]
        gaps.append(average_precision_score(s["y"], s["model"]) - average_precision_score(s["y"], s["n_today_r1"]))
    season["ap_gap_ci95"] = [float(np.percentile(gaps, 2.5)), float(np.percentile(gaps, 97.5))]
    out = {"built_from_live_day": live["live_day"], "model": MODEL.name, "top_k": TOP_K,
           "note": "Exploratory: not pre-registered. Inputs are NOAA-20 and live rain; the model was trained on S-NPP.",
           "n_boot": N_BOOT,
           "season": season, "recent": days.tail(14).round(4).to_dict(orient="records")}
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    last = days.iloc[-1]
    print(f"2026 season ({season['from']} to {season['to']}, {season['days']} days): AP {season['ap_model']:.3f} vs "
          f"{season['ap_yesterday']:.3f} for yesterday's fires (3x3); gap 95% CI {season['ap_gap_ci95'][0]:.3f} to "
          f"{season['ap_gap_ci95'][1]:.3f}")
    print(f"last scored day {last['date']}: top {TOP_K} spots burned next day {last['top_model']:.0%} (Bara) vs "
          f"{last['top_yesterday']:.0%} (yesterday's fires); {last['burned_next_day']} cells burned")


if __name__ == "__main__":
    main()
