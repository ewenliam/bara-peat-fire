"""Print every headline result in the README from the committed result files.

No data download, no API calls: it only reads outputs/. Run from the repository root:
    python eval/summary.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "outputs"
MODEL, BASELINE = "LightGBM + peat + rain", "Yesterday's fires (3x3)"


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def ci(pair: list[float]) -> str:
    return f"[{pair[0]:.3f}, {pair[1]:.3f}]"


def test_2023() -> list[str]:
    r = load("test_2023_results.json")
    h1, d = r["forecast"]["H1"], r["dispatch"]
    noaa = r["sensor_noaa20"]["metrics"]
    gain = d["solver_reached"] / d["biggest_today_reached"] - 1
    return [
        "## 2023 season, pre-registered, run once (outputs/test_2023_results.json)",
        "",
        "| Test | Bara | Baseline | Result |",
        "|---|---|---|---|",
        f"| H1 forecast, average precision (S-NPP) | {h1['model_AP']:.3f} | {h1['baseline_AP']:.3f} "
        f"| gap 95% CI {ci(h1['diff_CI95'])}, {h1['n_boot']} bootstraps: "
        f"{'supported' if h1['supported'] else 'not supported'} |",
        f"| Same, NOAA-20 detections | {noaa[MODEL]['AP']:.3f} | {noaa[BASELINE]['AP']:.3f} | sensor check |",
        f"| H2 dispatch, next-day fire cells reached over {d['days']} days | {d['solver_reached']:,.0f} "
        f"| {d['biggest_today_reached']:,.0f} | {gain:+.1%}, "
        f"{'supported' if d['H2_supported'] else 'not supported'} |",
        f"| Crew visits needed | {d['solver_visits']:,} | {d['biggest_today_visits']:,} | fewer is better |",
        "",
    ]


def season_2026() -> list[str]:
    s = load("live_scorecard.json")
    season, recent, k = s["season"], s["recent"], s["top_k"]
    wins = sum(x["top_model"] > x["top_yesterday"] for x in recent)
    losses = sum(x["top_model"] < x["top_yesterday"] for x in recent)
    last = recent[-1]
    return [
        "## 2026 season so far, exploratory (outputs/live_scorecard.json)",
        "",
        f"- {season['from']} to {season['to']}, {season['days']} days, {season['cells']:,} cell-days, "
        f"{season['burned_next_day']:,} burned the next day.",
        f"- Average precision {season['ap_model']:.3f} against {season['ap_yesterday']:.3f} for yesterday's "
        f"fires; gap 95% CI {ci(season['ap_gap_ci95'])}.",
        f"- Last {len(recent)} days, top {k} spots: Bara ahead on {wins} days, behind on {losses}. "
        f"Last day ({last['date']}): {round(last['top_model'] * k)} vs {round(last['top_yesterday'] * k)} burned.",
        "",
    ]


def glimmer() -> list[str]:
    rows = [
        "## Muse Glimmer report reader, 100 held-out synthetic rows (outputs/glimmer/runs/*/summary_ours.json)",
        "",
        "| Run | Base valid JSON | Base all fields | Tuned valid JSON | Tuned all fields |",
        "|---|---|---|---|---|",
    ]
    for path in sorted((OUT / "glimmer" / "runs").glob("*/summary_ours.json")):
        r = json.loads(path.read_text(encoding="utf-8"))
        b, t = r["base"], r["tuned"]
        rows.append(f"| {r['run_id']} | {b['valid_json']:.2f} | {b['all_fields']:.2f} "
                    f"| {t['valid_json']:.2f} | {t['all_fields']:.2f} |")
    return rows + ["", "Caveat: rows were kept only when Muse Spark got them right, so this compares "
                       "Glimmer base against tuned, not Glimmer against Spark.", ""]


def main() -> int:
    print("\n".join(["# Bara evaluation summary", ""] + test_2023() + season_2026() + glimmer()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
