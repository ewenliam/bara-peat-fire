"""Baselines vs LightGBM for next-day hotspots, scored on a validation year.

Every model scores the same candidate cells. Metrics:
- AP (average precision), the field standard
- F1, IoU and predicted-area ratio at the threshold that maximises F1 on this split
- precision@k: share of the k highest-scored cells per day that burn tomorrow (patrol view)
- AP on "new" cells (no detection in the cell in the last 7 days), where persistence is blind

Usage:
    python src/train_eval.py --train samples_train_feat --valid samples_2019_2019_feat --target y3
    python src/train_eval.py --task ignition --train ign_train --valid ign_2019
"""
import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
OUT = ROOT / "outputs"
MODELS = ROOT / "models"

HOTSPOT = ["n_today", "n_today_r1", "n_today_r2", "n_7d", "n_7d_r1", "n_7d_r2",
           "frp_today_r1", "days_since_fire", "doy"]
PEAT = ["peat_share"]
RAIN = ["rain_1d", "rain_7d", "rain_30d", "rain_90d", "dry_days_30d", "days_since_10mm"]
ALL = HOTSPOT + PEAT + RAIN
IGN_HIST = ["hist_rate", "nb_7d", "nb_30d", "own_30d", "region_today", "doy"]
DANGER = ["dc", "dc_change_7d"]


def metrics(y: np.ndarray, score: np.ndarray, valid: pd.DataFrame, target: str) -> dict[str, float]:
    ap = average_precision_score(y, score)
    prec, rec, thr = precision_recall_curve(y, score)
    f1 = 2 * prec * rec / np.clip(prec + rec, 1e-12, None)
    best = int(np.nanargmax(f1[:-1]))
    pred = score >= thr[best]
    tp = int((pred & (y == 1)).sum())
    iou = tp / max(int((pred | (y == 1)).sum()), 1)
    out = {"AP": ap, "F1": f1[best], "IoU": iou, "area_ratio": pred.sum() / max(y.sum(), 1),
           "threshold": float(thr[best])}

    ranked = valid[["date"]].assign(y=y, score=score)
    for k in (20, 50):
        top = ranked.sort_values("score", ascending=False).groupby("date").head(k)
        out[f"P@{k}"] = top["y"].mean()

    if "n_7d" in valid:
        new = valid["n_7d"].to_numpy() == 0
        out["AP_new"] = average_precision_score(y[new], score[new]) if y[new].sum() else float("nan")
    return out


def lgb_model(train: pd.DataFrame, valid: pd.DataFrame, cols: list[str], target: str) -> lgb.Booster:
    params = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 63,
              "min_data_in_leaf": 200, "feature_fraction": 0.9, "bagging_fraction": 0.8,
              "bagging_freq": 1, "verbose": -1, "num_threads": 0, "seed": 0}
    dtrain = lgb.Dataset(train[cols], train[target], weight=train["weight"])
    dvalid = lgb.Dataset(valid[cols], valid[target], reference=dtrain)
    return lgb.train(params, dtrain, num_boost_round=1000, valid_sets=[dvalid],
                     callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)])


def task_setup(task: str, valid: pd.DataFrame) -> tuple[dict, list, list[str]]:
    """Baseline scores, (name, feature list) model specs, and logistic-regression features."""
    if task == "spread":
        baselines = {
            "Yesterday's fires (cell)": valid["n_today"].to_numpy(),
            "Yesterday's fires (3x3)": valid["n_today_r1"].to_numpy(),
            "Last 7 days (5x5)": valid["n_7d_r2"].to_numpy(),
            "Dryness only (-rain 30d)": -valid["rain_30d"].to_numpy(),
        }
        specs = [("LightGBM hotspots only", HOTSPOT), ("LightGBM + peat + rain", ALL)]
        if "dc" in valid:
            baselines["Drought Code only"] = valid["dc"].fillna(0).to_numpy()
            specs.append(("LightGBM + peat + rain + DC", ALL + DANGER))
        return baselines, specs, ALL
    baselines = {
        "Climatology (past fire rate)": valid["hist_rate"].to_numpy(),
        "Nearby fires, last 7 days": valid["nb_7d"].to_numpy(),
        "Dryness only (-rain 30d)": -valid["rain_30d"].to_numpy(),
        "Peat x dry days": (valid["peat_share"] * valid["dry_days_30d"]).to_numpy(),
    }
    specs = [("LightGBM history only", IGN_HIST), ("LightGBM + peat + rain", IGN_HIST + PEAT + RAIN)]
    if "dc" in valid:
        baselines["Drought Code only"] = valid["dc"].fillna(0).to_numpy()
        specs.append(("LightGBM + peat + rain + DC", IGN_HIST + PEAT + RAIN + DANGER))
    return baselines, specs, IGN_HIST + PEAT + RAIN


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=["spread", "ignition"], default="spread")
    parser.add_argument("--train", required=True)
    parser.add_argument("--valid", required=True)
    parser.add_argument("--target", default="y", help="y, y3 or y7 (spread task)")
    args = parser.parse_args()

    train = pd.read_parquet(PROC / f"{args.train}.parquet")
    valid = pd.read_parquet(PROC / f"{args.valid}.parquet")
    y = valid[args.target].to_numpy()
    print(f"{args.task} / {args.target}: train {len(train):,} rows, valid {len(valid):,} rows, "
          f"valid positives {int(y.sum()):,}")

    scores, specs, logit_cols = task_setup(args.task, valid)
    sub = train.sample(n=min(len(train), 1_000_000), random_state=0)
    logit = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500))
    logit.fit(np.log1p(sub[logit_cols].clip(lower=0)), sub[args.target],
              logisticregression__sample_weight=sub["weight"])
    scores["Logistic regression (all)"] = logit.predict_proba(
        np.log1p(valid[logit_cols].clip(lower=0)))[:, 1]

    MODELS.mkdir(exist_ok=True)
    boosters = {}
    for name, cols in specs:
        booster = lgb_model(train, valid, cols, args.target)
        boosters[name] = (booster, cols)
        scores[name] = booster.predict(valid[cols], num_iteration=booster.best_iteration)
        print(f"{name}: best iteration {booster.best_iteration}")

    table = pd.DataFrame({name: metrics(y, s, valid, args.target) for name, s in scores.items()}).T
    cols = [c for c in ["AP", "AP_new", "F1", "IoU", "area_ratio", "P@20", "P@50"] if c in table]
    print("\n" + table[cols].round(3).to_string())

    full, full_cols = boosters["LightGBM + peat + rain"]
    tag = f"{args.task}_{args.target}_" + args.valid.replace("samples_", "").replace("_feat", "")
    full.save_model(str(MODELS / f"lgbm_{tag}.txt"), num_iteration=full.best_iteration)
    imp = pd.Series(full.feature_importance("gain"), index=full_cols)
    imp = (imp / imp.sum()).sort_values(ascending=False)
    print("\nfeature importance (gain share):\n" + imp.round(3).to_string())

    OUT.mkdir(exist_ok=True)
    (OUT / f"results_{tag}.md").write_text(table[cols].round(3).to_markdown() + "\n", encoding="utf-8")
    (OUT / f"results_{tag}.json").write_text(json.dumps(
        {"metrics": table.to_dict(orient="index"), "importance": imp.to_dict(),
         "best_iteration": full.best_iteration}, indent=2, default=float), encoding="utf-8")
    print(f"\nsaved outputs/results_{tag}.md and models/lgbm_{tag}.txt")


if __name__ == "__main__":
    main()
