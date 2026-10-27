"""Step 1, Detect: group hotspots into fire clusters and mark each cluster's earliest detection.

The earliest detection is NOT the ignition point: a satellite passes only a few times
a day, so the real start is earlier and possibly elsewhere. Label it accordingly.

Usage:
    python src/detect.py --start 2019-09-01 --end 2019-09-30
"""
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

from firms import load_years

ROOT = Path(__file__).resolve().parents[1]
KM_PER_DEG = 111.32
EPS_KM = 2.5       # detections closer than this (space-time distance) join one cluster
KM_PER_DAY = 2.0   # one day apart counts like 2 km apart


def cluster_hotspots(df: pd.DataFrame) -> pd.DataFrame:
    """Add a cluster_id column using DBSCAN on (x_km, y_km, scaled time)."""
    lat0 = np.deg2rad(df["latitude"].mean())
    x = df["longitude"].to_numpy() * KM_PER_DEG * np.cos(lat0)
    y = df["latitude"].to_numpy() * KM_PER_DEG
    days = (df["acq_datetime"] - df["acq_datetime"].min()).dt.total_seconds() / 86400
    features = np.column_stack([x, y, days.to_numpy() * KM_PER_DAY])
    labels = DBSCAN(eps=EPS_KM, min_samples=1, algorithm="ball_tree").fit_predict(features)
    return df.assign(cluster_id=labels)


def summarise_clusters(df: pd.DataFrame) -> pd.DataFrame:
    """One row per cluster: earliest detection, size, duration, total FRP."""
    first = df.loc[df.groupby("cluster_id")["acq_datetime"].idxmin()]
    first = first.set_index("cluster_id")[["latitude", "longitude", "acq_datetime"]]
    first.columns = ["origin_lat", "origin_lon", "first_seen"]
    agg = df.groupby("cluster_id").agg(
        n_detections=("frp", "size"),
        last_seen=("acq_datetime", "max"),
        total_frp=("frp", "sum"),
    )
    out = first.join(agg)
    out["duration_days"] = (out["last_seen"] - out["first_seen"]).dt.total_seconds() / 86400
    # Clusters already burning when the window opens: their true first detection is unknown.
    out["started_before_window"] = out["first_seen"] < df["acq_datetime"].min() + pd.Timedelta(days=1)
    return out.sort_values("n_detections", ascending=False)


def plot_clusters(df: pd.DataFrame, clusters: pd.DataFrame, path: Path, top_n: int = 15) -> None:
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.scatter(df["longitude"], df["latitude"], s=0.3, c="#d95f02", alpha=0.4, label="hotspot")
    top = clusters.head(top_n)
    ax.scatter(top["origin_lon"], top["origin_lat"], marker="*", s=140, c="#1b1b1b",
               label=f"earliest detection, top {top_n} clusters")
    for rank, (cid, row) in enumerate(top.iterrows(), start=1):
        ax.annotate(str(rank), (row["origin_lon"], row["origin_lat"]), xytext=(4, 4),
                    textcoords="offset points", fontsize=8)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_aspect("equal")
    ax.legend(loc="lower left")
    ax.set_title(f"VIIRS hotspots and fire clusters, {df['acq_date'].min():%Y-%m-%d} to "
                 f"{df['acq_date'].max():%Y-%m-%d}")
    fig.tight_layout()
    fig.savefig(path, dpi=130)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    years = list(range(start.year, end.year + 1))
    df = load_years(years)
    df = df[df["acq_date"].between(start, end)].reset_index(drop=True)
    print(f"{len(df):,} detections between {start:%Y-%m-%d} and {end:%Y-%m-%d}")

    df = cluster_hotspots(df)
    clusters = summarise_clusters(df)
    multi = clusters[clusters["n_detections"] > 1]
    print(f"{len(clusters):,} clusters ({len(multi):,} with more than one detection)")
    print(f"{clusters['started_before_window'].sum():,} clusters were already burning at window start")
    print(clusters.head(10).round({"total_frp": 0, "duration_days": 1}).to_string())

    tag = f"{start:%Y%m%d}_{end:%Y%m%d}"
    (ROOT / "data" / "processed").mkdir(parents=True, exist_ok=True)
    (ROOT / "outputs").mkdir(exist_ok=True)
    df.to_parquet(ROOT / "data" / "processed" / f"hotspots_{tag}.parquet")
    clusters.to_parquet(ROOT / "data" / "processed" / f"clusters_{tag}.parquet")
    plot_clusters(df, clusters, ROOT / "outputs" / f"detect_{tag}.png")
    print(f"saved outputs/detect_{tag}.png")


if __name__ == "__main__":
    main()
