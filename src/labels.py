"""Step 2 data: next-day hotspot labels and hotspot features on a ~1.1 km daily grid.

For each day d, candidate cells are those within RADIUS cells of any detection in the
last WINDOW days (d-WINDOW+1 .. d). Label y = 1 if the cell has a detection on day d+1;
y3 / y7 = any detection in the next 3 / 7 days.
Negatives are sampled on y, and "weight" (1 / keep probability) is valid for every horizon.
Features use only days <= d, so there is no look-ahead.

Fires that start tomorrow far from any recent fire are outside the candidate set; the
script reports that share ("coverage") so the gap is visible, not hidden.

Usage:
    python src/labels.py --years 2019            # one year, all candidates
    python src/labels.py --years 2012-2018 2020-2022 --neg-frac 0.1   # training years
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from firms import load_years

ROOT = Path(__file__).resolve().parents[1]
RES = 0.01        # grid size in degrees (~1.1 km)
WINDOW = 7        # days of recent fire that define candidate cells
RADIUS = 2        # candidate dilation in cells (5x5 neighbourhood, ~2.2 km)
HORIZONS = (1, 3, 7)  # label horizons in days: y, y3 (any of next 3 days), y7
SHIFT = 100_000   # key = ix * SHIFT + iy (iy offset to stay positive)
IY_OFFSET = 50_000


def to_keys(ix: np.ndarray, iy: np.ndarray) -> np.ndarray:
    return ix.astype(np.int64) * SHIFT + (iy.astype(np.int64) + IY_OFFSET)


def from_keys(keys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return keys // SHIFT, keys % SHIFT - IY_OFFSET


def offsets(radius: int) -> np.ndarray:
    r = np.arange(-radius, radius + 1)
    dx, dy = np.meshgrid(r, r)
    return (dx.ravel().astype(np.int64) * SHIFT + dy.ravel().astype(np.int64))


class DayCounts:
    """Sorted per-day cell keys with detection counts and FRP sums, for fast lookups."""

    def __init__(self, daily: pd.DataFrame):
        self.days: dict[pd.Timestamp, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        for day, g in daily.groupby("acq_date", sort=True):
            g = g.sort_values("key")
            self.days[day] = (g["key"].to_numpy(), g["n"].to_numpy(), g["frp"].to_numpy())

    def keys(self, day: pd.Timestamp) -> np.ndarray:
        return self.days.get(day, (np.empty(0, np.int64),))[0]

    def lookup(self, day: pd.Timestamp, query: np.ndarray, what: int = 1) -> np.ndarray:
        """Count (what=1) or FRP (what=2) for each query key on a day; 0 if absent."""
        if day not in self.days:
            return np.zeros(len(query))
        keys, *vals = self.days[day]
        values = vals[what - 1]
        pos = np.searchsorted(keys, query)
        pos = np.clip(pos, 0, len(keys) - 1)
        hit = keys[pos] == query
        return np.where(hit, values[pos], 0)

    def window_sum(self, day: pd.Timestamp, query: np.ndarray, n_days: int,
                   radius: int = 0, what: int = 1) -> np.ndarray:
        total = np.zeros(len(query))
        offs = offsets(radius)
        for k in range(n_days):
            d = day - pd.Timedelta(days=k)
            if d not in self.days:
                continue
            for off in offs:
                total += self.lookup(d, query + off, what)
        return total


def build_samples(years: list[int], neg_frac: float, seed: int = 0) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    # Load one extra year before (window history) and after (labels for Dec 31).
    load = sorted(set(years) | {y - 1 for y in years} | {y + 1 for y in years})
    load = [y for y in load if 2012 <= y <= 2025]
    df = load_years(load)
    df["key"] = to_keys(np.floor(df["longitude"] / RES).astype(int).to_numpy(),
                        np.floor(df["latitude"] / RES).astype(int).to_numpy())
    daily = df.groupby(["acq_date", "key"]).agg(n=("frp", "size"), frp=("frp", "sum")).reset_index()
    dc = DayCounts(daily)

    dil = offsets(RADIUS)
    last_seen: dict[int, pd.Timestamp] = {}
    rows, stats = [], {"next_day_active": 0, "next_day_covered": 0, "candidates": 0, "positives": 0}
    all_days = pd.date_range(f"{min(load)}-01-01", f"{max(years)}-12-31", freq="D")

    for day in all_days:
        today_keys = dc.keys(day)
        for k in today_keys:
            last_seen[k] = day
        if day.year not in years:
            continue

        recent = [dc.keys(day - pd.Timedelta(days=k)) for k in range(WINDOW)]
        recent = np.unique(np.concatenate(recent)) if any(len(r) for r in recent) else np.empty(0, np.int64)
        if len(recent) == 0:
            continue
        cand = np.unique((recent[:, None] + dil[None, :]).ravel())

        tomorrow = day + pd.Timedelta(days=1)
        y = (dc.lookup(tomorrow, cand) > 0).astype(np.int8)
        ahead = np.zeros(len(cand))
        y3 = np.zeros(len(cand), dtype=np.int8)
        for h in range(1, max(HORIZONS) + 1):
            ahead += dc.lookup(day + pd.Timedelta(days=h), cand)
            if h == 3:
                y3 = (ahead > 0).astype(np.int8)
        y7 = (ahead > 0).astype(np.int8)
        n_next = len(dc.keys(tomorrow))
        stats["next_day_active"] += n_next
        stats["next_day_covered"] += int(y.sum())
        stats["candidates"] += len(cand)
        stats["positives"] += int(y.sum())

        if neg_frac < 1.0:
            keep = (y == 1) | (rng.random(len(cand)) < neg_frac)
            cand, y, y3, y7 = cand[keep], y[keep], y3[keep], y7[keep]

        ix, iy = from_keys(cand)
        seen = pd.Series(cand).map(last_seen)
        days_since = (day - seen).dt.days.fillna(999).clip(upper=365).to_numpy()
        rows.append(pd.DataFrame({
            "date": day,
            "ix": ix.astype(np.int32),
            "iy": iy.astype(np.int32),
            "n_today": dc.window_sum(day, cand, 1, 0),
            "n_today_r1": dc.window_sum(day, cand, 1, 1),
            "n_today_r2": dc.window_sum(day, cand, 1, 2),
            "n_7d": dc.window_sum(day, cand, WINDOW, 0),
            "n_7d_r1": dc.window_sum(day, cand, WINDOW, 1),
            "n_7d_r2": dc.window_sum(day, cand, WINDOW, 2),
            "frp_today_r1": dc.window_sum(day, cand, 1, 1, what=2),
            "days_since_fire": days_since,
            "doy": day.day_of_year,
            "y": y,
            "y3": y3,
            "y7": y7,
            "weight": np.where(y == 1, 1.0, 1.0 / neg_frac).astype(np.float32),
        }))

    out = pd.concat(rows, ignore_index=True)
    out["lon"] = (out["ix"] + 0.5) * RES
    out["lat"] = (out["iy"] + 0.5) * RES
    return out, stats


def parse_years(specs: list[str]) -> list[int]:
    years = []
    for s in specs:
        a, _, b = s.partition("-")
        years += list(range(int(a), int(b or a) + 1))
    return sorted(set(years))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", required=True, help="e.g. 2019 or 2012-2018")
    parser.add_argument("--neg-frac", type=float, default=1.0,
                        help="keep this share of negatives (training only); weight corrects for it")
    parser.add_argument("--name", default=None)
    args = parser.parse_args()

    years = parse_years(args.years)
    samples, stats = build_samples(years, args.neg_frac)
    name = args.name or f"samples_{years[0]}_{years[-1]}"
    path = ROOT / "data" / "processed" / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    samples.to_parquet(path)

    cov = stats["next_day_covered"] / max(stats["next_day_active"], 1)
    rate = stats["positives"] / max(stats["candidates"], 1)
    print(f"years {years}: {len(samples):,} rows saved to {path.name}")
    print(f"candidates {stats['candidates']:,}, positive rate {rate:.4f}")
    print(f"coverage: {cov:.1%} of next-day active cells were candidates "
          f"({1 - cov:.1%} are new fires far from recent ones)")


if __name__ == "__main__":
    main()
