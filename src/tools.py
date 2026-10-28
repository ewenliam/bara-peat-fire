"""Deterministic tools the Muse Spark agent calls. Every number in a briefing comes from here.

v0 replays 2019 and 2023: the feature tables exist for both. 2019 was the early-stopping
year, so it is not a clean test; 2023 is the held-out test (src/test_2023.py).

Usage (offline check, no API calls):
    python src/tools.py --date 2019-09-15
"""
import argparse
import json
from functools import lru_cache
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from detect import KM_PER_DEG, cluster_hotspots, summarise_clusters
from dispatch import DAY_H, DETOUR, ON_SITE_H, SPEED_KMH, plan
from firms import load_years
from labels import WINDOW
from places import crew_bases, locate
from triage import TIER_RULE, add_tiers
from water import nearest_water

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
MODELS = ROOT / "models"
OUT = ROOT / "outputs"
REPLAY_YEARS = (2019, 2023)  # 2023 is the held-out test year (PREREGISTRATION_2023.md)
SPREAD_MODEL = "spread_y_2019_2019"
IGNITION_MODEL = "ignition_y_ign_2019"
FULL_SPEC = "LightGBM + peat + rain"
ASSIGN_KM = 4.0  # candidate cells lie within 2 cells (~2.2 km plus half a cell) of a detection

DRIVER_NAMES = {
    "n_today": "detections in this cell today",
    "n_today_r1": "detections in the 3x3 cells around it today",
    "n_today_r2": "detections in the 5x5 cells around it today",
    "n_7d": "detections in this cell, last 7 days",
    "n_7d_r1": "detections in the 3x3 cells, last 7 days",
    "n_7d_r2": "detections in the 5x5 cells, last 7 days",
    "frp_today_r1": "fire radiative power nearby today",
    "days_since_fire": "days since the cell last burned",
    "doy": "time of year",
    "peat_share": "peat share of the cell",
    "rain_1d": "rain 2 days ago",
    "rain_7d": "rain over 7 days",
    "rain_30d": "rain over 30 days",
    "rain_90d": "rain over 90 days",
    "dry_days_30d": "dry days in the last 30",
    "days_since_10mm": "days since 10 mm of rain",
    "hist_rate": "past fire rate of the area",
    "nb_7d": "fires in neighbouring 11 km cells, last 7 days",
    "nb_30d": "fires in neighbouring 11 km cells, last 30 days",
    "own_30d": "fires in this 11 km cell, last 30 days",
    "region_today": "detections in the whole region today",
}


@lru_cache(maxsize=2)
def _hotspots(year: int) -> pd.DataFrame:
    return load_years([year])


@lru_cache(maxsize=4)
def _model(tag: str) -> tuple[lgb.Booster, float]:
    booster = lgb.Booster(model_file=str(MODELS / f"lgbm_{tag}.txt"))
    results = json.loads((OUT / f"results_{tag}.json").read_text(encoding="utf-8"))
    return booster, float(results["metrics"][FULL_SPEC]["threshold"])


def _rows(name: str, date: pd.Timestamp) -> pd.DataFrame:
    return pd.read_parquet(PROC / f"{name}.parquet", filters=[("date", "==", date)])


def _km(lon: np.ndarray, lat: np.ndarray, lat0: float) -> np.ndarray:
    return np.column_stack([lon * KM_PER_DEG * np.cos(np.deg2rad(lat0)), lat * KM_PER_DEG])


def _drivers(booster: lgb.Booster, X: pd.DataFrame, n: int = 3) -> list[str]:
    """Top features by mean SHAP contribution (log-odds) over the given cells."""
    contrib = booster.predict(X, pred_contrib=True)[:, :-1].mean(axis=0)
    order = np.argsort(-np.abs(contrib))[:n]
    return [f"{'+' if contrib[i] > 0 else '-'} {DRIVER_NAMES.get(X.columns[i], X.columns[i])}"
            for i in order]


class FireTools:
    """All tools for one forecast date. Features on date D forecast detections on D + 1."""

    def __init__(self, date: str):
        self.date = pd.Timestamp(date)
        if self.date.year not in REPLAY_YEARS:
            raise ValueError(f"v0 replays {REPLAY_YEARS} only, got {date}")
        start = self.date - pd.Timedelta(days=WINDOW - 1)
        df = _hotspots(self.date.year)
        if start.year != self.date.year:
            df = pd.concat([_hotspots(start.year), df])
        df = df[df["acq_date"].between(start, self.date)].reset_index(drop=True)
        df = cluster_hotspots(df)

        clusters = summarise_clusters(df)
        today = df[df["acq_date"] == self.date].groupby("cluster_id").size()
        clusters["n_today"] = today.reindex(clusters.index, fill_value=0)
        centre = df.groupby("cluster_id")[["latitude", "longitude"]].mean()
        clusters = clusters.join(centre.rename(columns={"latitude": "centre_lat",
                                                        "longitude": "centre_lon"}))
        # stable, readable ids: C1 is the cluster with the most detections in the window
        clusters["id"] = [f"C{i}" for i in range(1, len(clusters) + 1)]
        self.window_start = start
        self.hotspots = df.assign(id=clusters["id"].reindex(df["cluster_id"]).to_numpy())
        self.clusters = clusters.set_index("id")
        self.lat0 = float(df["latitude"].mean())
        self.water_overlays: dict[str, str] = {}  # cluster id -> SAM overlay PNG, for the UI

    def _place(self, cid: str) -> dict:
        c = self.clusters.loc[cid]
        loc = locate(c["centre_lat"], c["centre_lon"]).iloc[0]
        return {"nearest_place": loc["place"], "place_km": float(loc["place_km"]),
                "province": loc["province"]}

    def _cluster_record(self, cid: str) -> dict:
        c = self.clusters.loc[cid]
        return {
            "cluster_id": cid,
            "location": self._place(cid),
            "earliest_detection": {"lat": round(c["origin_lat"], 4), "lon": round(c["origin_lon"], 4),
                                   "time_utc": f"{c['first_seen']:%Y-%m-%d %H:%M}",
                                   "already_burning_at_window_start": bool(c["started_before_window"])},
            "centre": {"lat": round(c["centre_lat"], 3), "lon": round(c["centre_lon"], 3)},
            "detections_7d": int(c["n_detections"]),
            "detections_today": int(c["n_today"]),
            "total_frp_mw": int(round(c["total_frp"])),
        }

    def detect_clusters(self, top_n: int = 8) -> dict:
        active = self.clusters[self.clusters["n_today"] > 0]
        top = active.sort_values(["n_today", "n_detections"], ascending=False).head(top_n)
        return {
            "date": f"{self.date:%Y-%m-%d}",
            "window": f"{self.window_start:%Y-%m-%d} to {self.date:%Y-%m-%d}",
            "detections_today": int((self.hotspots["acq_date"] == self.date).sum()),
            "clusters_in_window": len(self.clusters),
            "clusters_active_today": len(active),
            "note": "earliest_detection is the first satellite detection, not the ignition point",
            "clusters": [self._cluster_record(cid) for cid in top.index],
        }

    def _score_cells(self) -> None:
        """Spread probabilities for every candidate cell, each tagged with its nearest cluster."""
        if hasattr(self, "cells"):
            return
        booster, threshold = _model(SPREAD_MODEL)
        cells = _rows(f"samples_{self.date.year}_{self.date.year}_feat", self.date)
        self.X = cells[booster.feature_name()]
        cells["p"] = booster.predict(self.X)
        tree = cKDTree(_km(self.hotspots["longitude"].to_numpy(),
                           self.hotspots["latitude"].to_numpy(), self.lat0))
        dist, idx = tree.query(_km(cells["lon"].to_numpy(), cells["lat"].to_numpy(), self.lat0),
                               distance_upper_bound=ASSIGN_KM)
        ok = np.isfinite(dist)
        cells["id"] = None
        cells.loc[ok, "id"] = self.hotspots["id"].to_numpy()[idx[ok]]
        self.cells, self.booster, self.threshold = cells, booster, threshold

    def table(self) -> pd.DataFrame:
        """One row per cluster: forecast, location of today's activity and tier.

        actual_new_cells (tomorrow's real detections) is for replay scoring only and is never
        passed to the agent.
        """
        if hasattr(self, "_table"):
            return self._table
        self._score_cells()
        thr = self.threshold
        per = self.cells.dropna(subset=["id"]).groupby("id").agg(
            expected_new_cells=("p", "sum"), max_probability=("p", "max"),
            cells_above_threshold=("p", lambda p: int((p >= thr).sum())),
            candidate_cells=("p", "size"), peat_share=("peat_share", "mean"),
            rain_30d_mm=("rain_30d", "mean"), days_since_10mm=("days_since_10mm", "mean"),
            actual_new_cells=("y", "sum"))
        today = self.hotspots[self.hotspots["acq_date"] == self.date].groupby("id")[["latitude", "longitude"]].mean()
        t = self.clusters.join(per, how="inner").join(today)
        t["lat"] = t["latitude"].fillna(t["centre_lat"])  # where it burns today, else window centre
        t["lon"] = t["longitude"].fillna(t["centre_lon"])
        self._table = add_tiers(t.drop(columns=["latitude", "longitude"]))
        return self._table

    def _forecast_record(self, cid: str) -> dict:
        r = self.table().loc[cid]
        rec = self._cluster_record(cid)
        rec.update({
            "expected_new_cells": round(float(r["expected_new_cells"]), 1),
            "max_probability": round(float(r["max_probability"]), 3),
            "cells_above_threshold": int(r["cells_above_threshold"]),
            "candidate_cells": int(r["candidate_cells"]),
            "peat_share": round(float(r["peat_share"]), 2),
            "rain_30d_mm": int(round(r["rain_30d_mm"])),
            "days_since_10mm": int(round(r["days_since_10mm"])),
            "risk": round(float(r["risk"]), 1),
            "tier": r["tier"],
        })
        return rec

    def forecast_spread(self, top_n: int = 8) -> dict:
        t = self.table()
        top = t.sort_values("expected_new_cells", ascending=False).head(top_n)
        out = []
        for cid in top.index:
            rec = self._forecast_record(cid)
            rec["top_drivers"] = _drivers(self.booster, self.X[self.cells["id"] == cid])
            out.append(rec)
        return {
            "forecast_for": f"{self.date + pd.Timedelta(days=1):%Y-%m-%d}",
            "model": "LightGBM, 1.1 km cells near fires of the last 7 days, 2019 AP 0.308 "
                     "vs 0.207 for the best persistence baseline",
            "threshold": round(self.threshold, 3),
            "candidate_cells": len(self.cells),
            "unassigned_cells": int(self.cells["id"].isna().sum()),
            "expected_new_cells_total": round(float(self.cells["p"].sum()), 1),
            "clusters": out,
        }

    def triage_clusters(self, top_n: int = 8) -> dict:
        t = self.table()
        counts = t["tier"].value_counts()
        p1 = t[t["tier"] == "P1"].sort_values("risk", ascending=False).head(top_n)
        return {
            "forecast_for": f"{self.date + pd.Timedelta(days=1):%Y-%m-%d}",
            "rule": TIER_RULE,
            "tiers": {"P1": "brigade now", "P2": "village fire watch", "P3": "monitor"},
            "clusters_per_tier": {k: int(counts.get(k, 0)) for k in ("P1", "P2", "P3")},
            "risk_per_tier": {k: round(float(t.loc[t["tier"] == k, "risk"].sum()), 1)
                              for k in ("P1", "P2", "P3")},
            "top_p1": [{"cluster_id": cid, "risk": round(float(r["risk"]), 1),
                        "expected_new_cells": round(float(r["expected_new_cells"]), 1),
                        "peat_share": round(float(r["peat_share"]), 2)} for cid, r in p1.iterrows()],
        }

    def _routes(self) -> list[dict]:
        if not hasattr(self, "_all_routes"):
            routes = plan(self.table(), crew_bases(), "solver")
            self._all_routes = sorted(routes, key=lambda r: -r["risk_covered"])
        return self._all_routes

    def find_water_sources(self, top_n: int = 3) -> dict:
        """Nearest river (SAM 3.1 on Sentinel-2) for each stop of the top crew routes."""
        stops = []
        for r in self._routes()[:top_n]:
            for s in r["stops"]:
                c = self.clusters.loc[s["cluster_id"]]
                w = nearest_water(float(c["centre_lat"]), float(c["centre_lon"]),
                                  tag=f"sam {self.date:%Y-%m-%d} {s['cluster_id']}")
                self.water_overlays[s["cluster_id"]] = w.pop("overlay")
                stops.append({"cluster_id": s["cluster_id"], "crew_base": r["base"], **w})
        return {
            "method": "SAM 3.1 (Meta) masks for 'river' and 'waterway' on a 10 x 10 km Sentinel-2 "
                      "true-colour chip centred on the cluster; distance from the cluster centre",
            "limits": "rivers only: drainage canals are too narrow at 10 m pixels and are not "
                      "detected; small rivers can be missed",
            "stops": stops,
        }

    def plan_dispatch(self, top_n: int = 8) -> dict:
        all_routes = self._routes()
        routes = all_routes[:top_n]
        for r in routes:
            for s in r["stops"]:
                s["location"] = self._place(s["cluster_id"])
        return {
            "assumptions": {"crews": "one per provincial capital (assumed base)",
                            "speed_kmh": SPEED_KMH, "route_factor": DETOUR,
                            "hours_on_site": ON_SITE_H, "hours_per_day": DAY_H},
            "method": "exact best order per crew (dynamic programming), maximising peat-weighted "
                      "risk covered; visiting is not extinguishing",
            "risk_total_p1_p2": round(float(self.table().query("tier in ['P1', 'P2']")["risk"].sum()), 1),
            "risk_covered_all_crews": round(sum(r["risk_covered"] for r in all_routes), 1),
            "routes": routes,
        }

    def forecast_new_fires(self, top_n: int = 8) -> dict:
        booster, threshold = _model(IGNITION_MODEL)
        cells = _rows(f"ign_{self.date.year}", self.date)
        X = cells[booster.feature_name()]
        cells["p"] = booster.predict(X)
        top = cells.sort_values("p", ascending=False).head(top_n)
        return {
            "forecast_for": f"{self.date + pd.Timedelta(days=1):%Y-%m-%d}",
            "model": "LightGBM, 11 km cells with no fire in the last 7 days, 2019 AP 0.057 "
                     "vs base rate 0.0055",
            "threshold": round(threshold, 3),
            "quiet_cells_scored": len(cells),
            "cells_above_threshold": int((cells["p"] >= threshold).sum()),
            "cells": [{"lat": round(float(r["lat"]), 2), "lon": round(float(r["lon"]), 2),
                       "probability": round(float(r["p"]), 3), "peat_share": round(float(r["peat_share"]), 2),
                       "days_since_10mm": int(round(r["days_since_10mm"])),
                       "fires_nearby_7d": int(r["nb_7d"])} for _, r in top.iterrows()],
            "top_drivers": _drivers(booster, X.loc[top.index]),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    t = FireTools(args.date)
    for name in ("detect_clusters", "forecast_spread", "forecast_new_fires", "triage_clusters",
                 "plan_dispatch"):
        result = getattr(t, name)(top_n=3)
        print(f"== {name}: {len(json.dumps(result)):,} chars")
        print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
