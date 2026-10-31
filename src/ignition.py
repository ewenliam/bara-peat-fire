"""New-ignition data: where will fire appear tomorrow in areas that are quiet today?

The spread model (labels.py) only scores cells near recent fires. This covers the rest on a
coarse 0.1 deg (~11 km) grid. Rows are fire-prone cells with no detection in the last 7 days;
label y = 1 if the cell has a detection tomorrow. This is a fire-danger problem, so dryness
and peat should matter more here than in the spread model.

Fire-prone cells: any detection in 2012-2018, or any peat. History features only use
years before the sample year, so there is no look-ahead.

Usage:
    python src/ignition.py --years 2019 --name ign_2019
    python src/ignition.py --years 2013-2018 2020-2022 --neg-frac 0.05 --name ign_train
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from features import CHIRPS, RAIN_LAG_DAYS, add_danger, grid_res, rain_cube
from firms import REGION_BBOX, load_years
from labels import RES, parse_years

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
CRES = 0.1
B = REGION_BBOX
NX = int(round((B["lon_max"] - B["lon_min"]) / CRES))
NY = int(round((B["lat_max"] - B["lat_min"]) / CRES))
QUIET_DAYS = 7


def coarse_index(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cx = np.floor((lon - B["lon_min"]) / CRES).astype(int)
    cy = np.floor((lat - B["lat_min"]) / CRES).astype(int)
    return np.clip(cx, 0, NX - 1), np.clip(cy, 0, NY - 1)


def daily_counts(days: pd.DatetimeIndex) -> np.ndarray:
    """Detections per (day, cy, cx) for the given days."""
    df = load_years(sorted({d.year for d in days}))
    df = df[df["acq_date"].isin(days)]
    cx, cy = coarse_index(df["longitude"].to_numpy(), df["latitude"].to_numpy())
    t = days.get_indexer(df["acq_date"])
    counts = np.zeros((len(days), NY, NX), dtype=np.int32)
    np.add.at(counts, (t, cy, cx), 1)
    return counts


def peat_coarse() -> np.ndarray:
    peat = pd.read_parquet(PROC / "peat_share.parquet")
    lon = (peat["ix"].to_numpy() + 0.5) * RES
    lat = (peat["iy"].to_numpy() + 0.5) * RES
    cx, cy = coarse_index(lon, lat)
    grid = np.zeros((NY, NX))
    np.add.at(grid, (cy, cx), peat["peat_share"].to_numpy())
    return grid / (CRES / RES) ** 2  # mean share over the 100 fine cells


def neighbour_sum(a: np.ndarray) -> np.ndarray:
    """3x3 sum over the last two axes, zero-padded."""
    p = np.pad(a, [(0, 0)] * (a.ndim - 2) + [(1, 1), (1, 1)])
    return sum(p[..., 1 + dy:p.shape[-2] - 1 + dy, 1 + dx:p.shape[-1] - 1 + dx]
               for dy in (-1, 0, 1) for dx in (-1, 0, 1))


def build(years: list[int], neg_frac: float, source: str = "p25", lag: int = RAIN_LAG_DAYS,
          seed: int = 0, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """end: last day with data (live year); rows stop there and the day after counts as no detections."""
    rng = np.random.default_rng(seed)
    last = pd.Timestamp(f"{max(years) + 1}-01-07")
    if end is not None:
        last = min(last, end + pd.Timedelta(days=1))
    hist_days = pd.date_range("2012-01-20", last, freq="D")
    counts = daily_counts(hist_days)
    year_of = hist_days.year.to_numpy()

    prone = counts[year_of <= 2018].sum(axis=0) > 0
    peat = peat_coarse()
    prone |= peat > 0
    cy_grid, cx_grid = np.nonzero(prone)

    frames = []
    for year in years:
        idx = np.nonzero((year_of == year) & ((hist_days <= end) if end is not None else True))[0]
        prior = counts[year_of < year].sum(axis=0)          # fire history before this year
        n_prior_years = max(year - 2012, 1)
        start = idx[0] - QUIET_DAYS - 365
        sub = counts[max(start, 0): idx[-1] + 2]
        offset = idx[0] - max(start, 0)

        cum = np.cumsum(np.concatenate([np.zeros((1, NY, NX), np.int64), sub]), axis=0)
        last7 = cum[QUIET_DAYS:] - cum[:-QUIET_DAYS]          # last7[t'] = sum sub[t'-6 .. t']
        last7 = np.concatenate([np.zeros((QUIET_DAYS - 1, NY, NX), np.int64), last7])
        last30 = np.concatenate([np.zeros((29, NY, NX), np.int64), cum[30:] - cum[:-30]])
        nb7 = neighbour_sum(last7)
        nb30 = neighbour_sum(last30)

        days = hist_days[idx]
        cube = rain_cube(days.min(), days.max(), source, lag)
        res = grid_res(cube)
        lat_c = B["lat_min"] + (cy_grid + 0.5) * CRES
        lon_c = B["lon_min"] + (cx_grid + 0.5) * CRES
        li = np.round((lat_c - cube["latitude"].values[0]) / res).astype(int)
        lo = np.round((lon_c - cube["longitude"].values[0]) / res).astype(int)
        t_rain = pd.Index(cube["time"].values).get_indexer(days)

        for k, t in enumerate(range(offset, offset + len(idx))):
            quiet = last7[t, cy_grid, cx_grid] == 0
            y = (sub[t + 1, cy_grid, cx_grid] > 0).astype(np.int8)
            keep = quiet & ((y == 1) | (rng.random(len(y)) < neg_frac))
            cy, cx = cy_grid[keep], cx_grid[keep]
            row = {
                "date": days[k],
                "cx": cx.astype(np.int16), "cy": cy.astype(np.int16),
                "lon": lon_c[keep], "lat": lat_c[keep],
                "hist_rate": prior[cy, cx] / n_prior_years,
                "nb_7d": nb7[t, cy, cx], "nb_30d": nb30[t, cy, cx],
                "own_30d": last30[t, cy, cx],
                "region_today": sub[t].sum(),
                "doy": days[k].day_of_year,
                "peat_share": peat[cy, cx].astype(np.float32),
                "y": y[keep],
                "weight": np.where(y[keep] == 1, 1.0, 1.0 / neg_frac).astype(np.float32),
            }
            for name in cube.data_vars:
                row[name] = cube[name].values[t_rain[k], li[keep], lo[keep]].astype(np.float32)
            frames.append(pd.DataFrame(row))
        print(f"{year}: done")
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", required=True)
    parser.add_argument("--neg-frac", type=float, default=1.0)
    parser.add_argument("--name", required=True)
    parser.add_argument("--rain", choices=list(CHIRPS), default="p25")
    parser.add_argument("--lag", type=int, default=RAIN_LAG_DAYS)
    parser.add_argument("--danger", action="store_true", help="add CEMS Drought Code etc.")
    args = parser.parse_args()
    out = build(parse_years(args.years), args.neg_frac, args.rain, args.lag)
    if args.danger:
        out = add_danger(out, args.lag)
    out.to_parquet(PROC / f"{args.name}.parquet")
    print(f"{len(out):,} rows, positives {int(out['y'].sum()):,}, "
          f"weighted rate {np.average(out['y'], weights=out['weight']):.5f}")


if __name__ == "__main__":
    main()
