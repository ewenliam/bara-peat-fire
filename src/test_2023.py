"""Run the pre-registered 2023 held-out test (PREREGISTRATION_2023.md). Run it once.

Parts, in order:
- forecast: H1 (spread model AP vs Yesterday's fires (3x3), day bootstrap) plus the secondary
  metrics at the 2019 thresholds for the model and every baseline
- sensor: the same forecast metrics with NOAA-20 labels instead of S-NPP
- ignition: new-fire model against its baselines
- dispatch: H2, the Jul 1 to Oct 31 replay (solver vs biggest_today)

Nothing is fitted on the test year: the LightGBM models are the frozen files (hashes checked),
every threshold comes from the 2019 results files, and the logistic regression baseline is
re-fitted on the training years exactly as train_eval.py does.

Usage:
    python src/test_2023.py --check-2019 --boot 20   # code check on 2019 (validation year); writes nothing
    python src/test_2023.py                          # the one test run
    python src/test_2023.py --rerun "reason"         # only if a bug forces it; both runs stay in the log
"""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from firms import load_years
from labels import RES, to_keys
from places import crew_bases
from replay import score_day, summarise
from tools import FULL_SPEC
from train_eval import task_setup

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
MODELS = ROOT / "models"
OUT = ROOT / "outputs"
RESULTS = OUT / "test_2023_results.json"
LOG = OUT / "test_2023_log.md"

TEST_YEAR = 2023
FROZEN = {  # sha256 from PREREGISTRATION_2023.md
    "spread_y_2019_2019": "5d9236efcc2472897b985434efb7a43bc57477e526d4899871663adae9e7c512",
    "ignition_y_ign_2019": "029ad24793e1c78a4790b415dddf18add9714839fb28c0fce2a346493b5b7a03",
}
H1_BASELINE = "Yesterday's fires (3x3)"
N_BOOT, SEED = 1000, 0
REPLAY_DAYS = ("07-01", "10-31")
KEY_SPAN = 10**10  # day * KEY_SPAN + cell key stays unique (cell keys are below 2e9)


def check_frozen() -> dict[str, str]:
    for tag, digest in FROZEN.items():
        got = hashlib.sha256((MODELS / f"lgbm_{tag}.txt").read_bytes()).hexdigest()
        if got != digest:
            raise SystemExit(f"lgbm_{tag}.txt changed since the pre-registration ({got})")
    return FROZEN


def thresholds_2019(tag: str) -> dict[str, float]:
    metrics = json.loads((OUT / f"results_{tag}.json").read_text(encoding="utf-8"))["metrics"]
    return {name: m["threshold"] for name, m in metrics.items()}


def fixed_metrics(y: np.ndarray, score: np.ndarray, valid: pd.DataFrame, thr: float) -> dict[str, float]:
    """Same metrics as train_eval.metrics, but at a fixed threshold instead of the best F1."""
    pred = score >= thr
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    out = {"AP": average_precision_score(y, score), "F1": 2 * tp / max(2 * tp + fp + fn, 1),
           "IoU": tp / max(tp + fp + fn, 1), "area_ratio": pred.sum() / max(y.sum(), 1),
           "threshold": thr}
    ranked = valid[["date"]].assign(y=y, score=score)
    for k in (20, 50):
        top = ranked.sort_values("score", ascending=False).groupby("date").head(k)
        out[f"P@{k}"] = top["y"].mean()
    if "n_7d" in valid:
        new = valid["n_7d"].to_numpy() == 0
        out["AP_new"] = average_precision_score(y[new], score[new]) if y[new].sum() else float("nan")
    return out


def all_scores(task: str, train_name: str, valid: pd.DataFrame, tag: str) -> dict[str, np.ndarray]:
    """Baselines, the logistic regression re-fitted on the training years, and the frozen model."""
    scores, _, logit_cols = task_setup(task, valid)
    train = pd.read_parquet(PROC / f"{train_name}.parquet", columns=logit_cols + ["y", "weight"])
    sub = train.sample(n=min(len(train), 1_000_000), random_state=0)
    logit = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500))
    logit.fit(np.log1p(sub[logit_cols].clip(lower=0)), sub["y"],
              logisticregression__sample_weight=sub["weight"])
    scores["Logistic regression (all)"] = logit.predict_proba(
        np.log1p(valid[logit_cols].clip(lower=0)))[:, 1]
    booster = lgb.Booster(model_file=str(MODELS / f"lgbm_{tag}.txt"))
    scores[FULL_SPEC] = booster.predict(valid[booster.feature_name()])
    return scores


def sorted_for_ap(score: np.ndarray, y: np.ndarray, day: np.ndarray) -> tuple:
    """Sort once by score; ends marks the last row of each tied score, as sklearn's AP steps."""
    order = np.argsort(-score, kind="stable")
    s = score[order]
    ends = np.r_[np.flatnonzero(s[1:] != s[:-1]), len(s) - 1]
    return y[order].astype(np.float64), day[order], ends


def weighted_ap(ys: np.ndarray, ws: np.ndarray, ends: np.ndarray) -> float:
    tp = np.cumsum(ys * ws)[ends]
    fp = np.cumsum((1 - ys) * ws)[ends]
    precision = tp / np.maximum(tp + fp, 1e-12)
    recall = tp / tp[-1]
    return float(np.sum(np.diff(recall, prepend=0.0) * precision))


def bootstrap_h1(y: np.ndarray, model: np.ndarray, base: np.ndarray, dates: pd.Series,
                 n_boot: int) -> dict:
    """95% intervals for model AP, baseline AP and their difference, resampling days."""
    day, uniques = pd.factorize(dates)
    a, b = sorted_for_ap(model, y, day), sorted_for_ap(base, y, day)
    rng = np.random.default_rng(SEED)
    res = np.empty((n_boot, 2))
    for i in range(n_boot):
        w_day = np.bincount(rng.integers(0, len(uniques), len(uniques)), minlength=len(uniques))
        w_day = w_day.astype(np.float64)
        res[i] = weighted_ap(a[0], w_day[a[1]], a[2]), weighted_ap(b[0], w_day[b[1]], b[2])
    ones = np.ones(len(uniques))
    ci = lambda x: [float(np.percentile(x, 2.5)), float(np.percentile(x, 97.5))]  # noqa: E731
    return {"model_AP": weighted_ap(a[0], ones[a[1]], a[2]),
            "baseline_AP": weighted_ap(b[0], ones[b[1]], b[2]),
            "model_AP_CI95": ci(res[:, 0]), "baseline_AP_CI95": ci(res[:, 1]),
            "diff_CI95": ci(res[:, 0] - res[:, 1]), "n_boot": n_boot, "seed": SEED}


def sensor_labels(valid: pd.DataFrame, year: int, sensor: str) -> np.ndarray:
    """Next-day label for each candidate cell from one satellite's detections."""
    df = load_years([year, year + 1], sensor=sensor)
    key = to_keys(np.floor(df["longitude"] / RES).astype(int).to_numpy(),
                  np.floor(df["latitude"] / RES).astype(int).to_numpy())
    epoch = pd.Timestamp("1970-01-01")
    fired = np.unique((df["acq_date"] - epoch).dt.days.to_numpy().astype(np.int64) * KEY_SPAN + key)
    tomorrow = (valid["date"] + pd.Timedelta(days=1) - epoch).dt.days.to_numpy().astype(np.int64)
    return np.isin(tomorrow * KEY_SPAN + to_keys(valid["ix"].to_numpy(), valid["iy"].to_numpy()),
                   fired).astype(np.int8)


def forecast_block(y: np.ndarray, scores: dict, valid: pd.DataFrame, thr: dict,
                   n_boot: int) -> dict:
    metrics = {name: fixed_metrics(y, s, valid, thr[name]) for name, s in scores.items()}
    h1 = bootstrap_h1(y, scores[FULL_SPEC], scores[H1_BASELINE], valid["date"], n_boot)
    h1["supported"] = bool(metrics[FULL_SPEC]["AP"] > metrics[H1_BASELINE]["AP"])
    h1["diff_CI_excludes_0"] = bool(h1["diff_CI95"][0] > 0 or h1["diff_CI95"][1] < 0)
    return {"positives": int(y.sum()), "rows": len(y), "metrics": metrics, "H1": h1}


def dispatch_block(year: int, dates: pd.DatetimeIndex) -> tuple[pd.DataFrame, str]:
    bases = crew_bases()
    rows = []
    for date in dates:
        rows.append(score_day(date, bases))
        print(f"{date:%Y-%m-%d}: solver {rows[-1]['solver_actual']:.0f}, "
              f"biggest_today {rows[-1]['biggest_today_actual']:.0f}", flush=True)
    df = pd.DataFrame(rows)
    return df, summarise(df)


def table_md(metrics: dict) -> str:
    cols = ["AP", "AP_new", "F1", "IoU", "area_ratio", "P@20", "P@50"]
    t = pd.DataFrame(metrics).T
    return t[[c for c in cols if c in t]].astype(float).round(3).to_markdown()


def check_2019(n_boot: int) -> None:
    """Run every part on 2019 and compare with the saved 2019 results. Writes nothing."""
    year = 2019
    valid = pd.read_parquet(PROC / f"samples_{year}_{year}_feat.parquet")
    y = valid["y"].to_numpy()
    same = np.array_equal(sensor_labels(valid, year, "snpp"), y)
    print(f"S-NPP labels rebuilt by sensor_labels match labels.py: {same}")

    saved = json.loads((OUT / "results_spread_y_2019_2019.json").read_text(encoding="utf-8"))["metrics"]
    scores = all_scores("spread", "samples_train_feat", valid, "spread_y_2019_2019")
    block = forecast_block(y, scores, valid, thresholds_2019("spread_y_2019_2019"), n_boot)
    for name, m in block["metrics"].items():
        gaps = {k: abs(m[k] - saved[name][k]) for k in ("AP", "F1", "IoU", "P@20", "AP_new")}
        print(f"{name}: largest gap to saved 2019 metrics {max(gaps.values()):.2e}")
    h1 = block["H1"]
    print(f"weighted AP with unit weights vs sklearn: {h1['model_AP']:.6f} vs "
          f"{block['metrics'][FULL_SPEC]['AP']:.6f}; H1 CI {h1['diff_CI95']} ({n_boot} resamples)")
    del valid, scores

    valid = pd.read_parquet(PROC / f"ign_{year}.parquet")
    saved = json.loads((OUT / "results_ignition_y_ign_2019.json").read_text(encoding="utf-8"))["metrics"]
    scores = all_scores("ignition", "ign_train", valid, "ignition_y_ign_2019")
    thr = thresholds_2019("ignition_y_ign_2019")
    for name, s in scores.items():
        m = fixed_metrics(valid["y"].to_numpy(), s, valid, thr[name])
        print(f"ignition {name}: AP {m['AP']:.4f} vs saved {saved[name]['AP']:.4f}")

    saved = pd.read_csv(OUT / "replay_20190701_20191031.csv", parse_dates=["date"]).set_index("date")
    df, _ = dispatch_block(year, pd.to_datetime(["2019-08-15", "2019-09-15", "2019-10-01"]))
    for _, r in df.iterrows():
        old = saved.loc[r["date"]]
        print(f"replay {r['date']:%Y-%m-%d}: solver {r['solver_actual']:.0f} vs saved "
              f"{old['solver_actual']:.0f}, biggest_today {r['biggest_today_actual']:.0f} vs saved "
              f"{old['biggest_today_actual']:.0f}")


def git_state() -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT)
    dirty = subprocess.run(["git", "status", "--porcelain", "src"], capture_output=True, text=True, cwd=ROOT)
    return {"commit": head.stdout.strip(), "src_uncommitted": bool(dirty.stdout.strip())}


def run_test(reason: str) -> None:
    year = TEST_YEAR
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = {"started_utc": started, "reason": reason, "git": git_state(),
               "frozen_models": check_frozen()}

    valid = pd.read_parquet(PROC / f"samples_{year}_{year}_feat.parquet")
    scores = all_scores("spread", "samples_train_feat", valid, "spread_y_2019_2019")
    thr = thresholds_2019("spread_y_2019_2019")
    results["forecast"] = forecast_block(valid["y"].to_numpy(), scores, valid, thr, N_BOOT)
    print("forecast done", flush=True)
    results["sensor_noaa20"] = forecast_block(sensor_labels(valid, year, "noaa20"), scores, valid,
                                              thr, N_BOOT)
    print("sensor check done", flush=True)
    del valid, scores

    valid = pd.read_parquet(PROC / f"ign_{year}.parquet")
    scores = all_scores("ignition", "ign_train", valid, "ignition_y_ign_2019")
    thr = thresholds_2019("ignition_y_ign_2019")
    y = valid["y"].to_numpy()
    results["ignition"] = {"positives": int(y.sum()), "rows": len(y),
                           "metrics": {n: fixed_metrics(y, s, valid, thr[n]) for n, s in scores.items()}}
    print("ignition done", flush=True)
    del valid, scores

    dates = pd.date_range(f"{year}-{REPLAY_DAYS[0]}", f"{year}-{REPLAY_DAYS[1]}")
    df, replay_text = dispatch_block(year, dates)
    tag = f"{dates[0]:%Y%m%d}_{dates[-1]:%Y%m%d}"
    df.to_csv(OUT / f"replay_{tag}.csv", index=False)
    (OUT / f"replay_{tag}.md").write_text(replay_text, encoding="utf-8")
    results["dispatch"] = {"days": len(df), "solver_reached": float(df["solver_actual"].sum()),
                           "biggest_today_reached": float(df["biggest_today_actual"].sum()),
                           "solver_visits": int(df["solver_visits"].sum()),
                           "biggest_today_visits": int(df["biggest_today_visits"].sum())}
    results["dispatch"]["H2_supported"] = bool(
        results["dispatch"]["solver_reached"] > results["dispatch"]["biggest_today_reached"])

    h1 = results["forecast"]["H1"]
    text = "\n".join([
        f"## Run started {started} ({reason})", "",
        f"Commit {results['git']['commit']}, uncommitted changes in src: {results['git']['src_uncommitted']}", "",
        f"**H1** spread model AP {h1['model_AP']:.3f} vs {H1_BASELINE} {h1['baseline_AP']:.3f}; "
        f"difference 95% CI {h1['diff_CI95'][0]:.3f} to {h1['diff_CI95'][1]:.3f}; "
        f"supported: {h1['supported']}", "",
        f"**H2** solver reached {results['dispatch']['solver_reached']:,.0f} next-day fire cells vs "
        f"biggest_today {results['dispatch']['biggest_today_reached']:,.0f}; "
        f"supported: {results['dispatch']['H2_supported']}", "",
        "Forecast, S-NPP labels, 2019 thresholds:", "", table_md(results["forecast"]["metrics"]), "",
        "Forecast, NOAA-20 labels:", "", table_md(results["sensor_noaa20"]["metrics"]), "",
        "New-fire model:", "", table_md(results["ignition"]["metrics"]), "",
        replay_text, ""])
    if RESULTS.exists():  # a rerun: keep the earlier results file next to the new one
        RESULTS.rename(OUT / f"test_2023_results_before_{started.replace(':', '')}.json")
    RESULTS.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(text + "\n")
    print("\n" + text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-2019", action="store_true", help="code check on 2019; writes nothing")
    parser.add_argument("--boot", type=int, default=N_BOOT, help="bootstrap resamples (check only)")
    parser.add_argument("--rerun", metavar="REASON", help="rerun after a bug; the reason is logged")
    args = parser.parse_args()
    if args.check_2019:
        check_frozen()
        check_2019(args.boot)
        return
    if RESULTS.exists() and not args.rerun:
        raise SystemExit(f"{RESULTS.name} exists: the test runs once. Use --rerun \"reason\" for a bug fix.")
    run_test(args.rerun or "first run")


if __name__ == "__main__":
    main()
