"""Add peat share, rainfall-history and fire-danger features to a samples table.

Rainfall: CHIRPS v2.0 daily, 0.25 deg ("p25") or 0.05 deg region subset ("p05", from
chirps_fine.py). Features are lagged by --lag days so a live forecast only uses rain data
that would already be published. [[TODO: verify CHIRPS preliminary product latency; 2 days
is an assumption]]

Fire danger (--danger): Copernicus CEMS Drought Code, Duff Moisture Code, Fire Weather
Index and Keetch-Byram index, 0.25 deg, from drought.py, lagged like rain.

Usage:
    python src/features.py samples_2019_2019                     # p25, lag 2 (default)
    python src/features.py samples_2019_2019 --rain p05 --lag 0  # -> ..._feat_p05_lag0
    python src/features.py samples_2019_2019 --danger            # -> ..._feat_dc
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from firms import REGION_BBOX

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
CHIRPS = {
    "p25": ROOT / "data" / "raw" / "chirps" / "chirps-v2.0.{year}.days_p25.nc",
    "p05": ROOT / "data" / "raw" / "chirps_p05" / "chirps_p05_{year}_region.nc",
}
RAIN_LAG_DAYS = 2
CEMS_DIR = ROOT / "data" / "raw" / "cems_fire"
DANGER_VARS = {"drtcode": "dc"}  # only the Drought Code fits the EWDS request limits (drought.py)


def grid_res(cube: xr.Dataset) -> float:
    return float(round(abs(cube["latitude"].values[1] - cube["latitude"].values[0]), 4))


def rain_cube(first: pd.Timestamp, last: pd.Timestamp, source: str = "p25",
              lag: int = RAIN_LAG_DAYS) -> xr.Dataset:
    """Daily rainfall features on the CHIRPS grid, indexed by the forecast day."""
    start = first - pd.Timedelta(days=90 + lag + 5)
    years = range(start.year, last.year + 1)
    b = REGION_BBOX
    parts = []
    for year in years:
        with xr.open_dataset(str(CHIRPS[source]).format(year=year)) as ds:
            parts.append(ds["precip"].sel(
                latitude=slice(b["lat_min"] - 0.5, b["lat_max"] + 0.5),
                longitude=slice(b["lon_min"] - 0.5, b["lon_max"] + 0.5),
            ).load())
    p = xr.concat(parts, dim="time").sel(time=slice(start, last)).fillna(0)

    wet10 = (p >= 10).astype("float32")
    # days since the last day with >= 10 mm, capped at 90
    since = np.full(p.shape, 90.0, dtype="float32")
    run = np.full(p.shape[1:], 90.0, dtype="float32")
    for t in range(p.shape[0]):
        run = np.where(wet10.values[t] > 0, 0.0, np.minimum(run + 1, 90.0))
        since[t] = run

    feats = xr.Dataset({
        "rain_1d": p,
        "rain_7d": p.rolling(time=7, min_periods=1).sum(),
        "rain_30d": p.rolling(time=30, min_periods=1).sum(),
        "rain_90d": p.rolling(time=90, min_periods=1).sum(),
        "dry_days_30d": (p < 1).astype("float32").rolling(time=30, min_periods=1).sum(),
        "days_since_10mm": (p.dims, since),
    })
    # value used on forecast day d comes from day d - lag
    feats = feats.assign_coords(time=feats["time"] + np.timedelta64(lag, "D"))
    return feats


def danger_cube(first: pd.Timestamp, last: pd.Timestamp, lag: int = RAIN_LAG_DAYS) -> xr.Dataset:
    """CEMS fire danger indices indexed by forecast day; consolidated files, else intermediate."""
    start = first - pd.Timedelta(days=7 + lag + 2)
    parts = []
    for year in range(start.year, last.year + 1):
        path = CEMS_DIR / f"cems_fire_{year}_con.nc"
        if not path.exists():
            path = CEMS_DIR / f"cems_fire_{year}_int.nc"
        with xr.open_dataset(path) as ds:
            parts.append(ds[list(DANGER_VARS)].rename(DANGER_VARS).load())
    d = xr.concat(parts, dim="valid_time").rename(valid_time="time").sortby("latitude")
    d = d.drop_vars([c for c in d.coords if c not in ("time", "latitude", "longitude")])
    d = d.sel(time=slice(start, last))
    d["dc_change_7d"] = d["dc"] - d["dc"].shift(time=7)
    return d.assign_coords(time=d["time"] + np.timedelta64(lag, "D"))


def lookup_grid(part: pd.DataFrame, cube: xr.Dataset, year: int) -> tuple[np.ndarray, ...]:
    """Time, lat and lon indices into a cube for each sample row (nearest grid cell)."""
    res = grid_res(cube)
    ti = pd.Index(cube["time"].values).get_indexer(part["date"].values)
    if (ti < 0).any():
        raise ValueError(f"{year}: {(ti < 0).sum()} rows have no date in the cube")
    lat = cube["latitude"].values
    lon = cube["longitude"].values
    li = np.clip(np.round((part["lat"].values - lat[0]) / res).astype(int), 0, len(lat) - 1)
    lo = np.clip(np.round((part["lon"].values - lon[0]) / res).astype(int), 0, len(lon) - 1)
    return ti, li, lo


def fill_from_cube(samples: pd.DataFrame, make_cube, **kwargs) -> pd.DataFrame:
    """Add every cube variable as a column, one year at a time to bound memory."""
    for year, rows in samples.groupby(samples["date"].dt.year).indices.items():
        part = samples.iloc[rows]
        cube = make_cube(part["date"].min(), part["date"].max(), **kwargs)
        ti, li, lo = lookup_grid(part, cube, year)
        for name in cube.data_vars:
            if name not in samples:
                samples[name] = np.float32(np.nan)
            samples.iloc[rows, samples.columns.get_loc(name)] = cube[name].values[ti, li, lo]
    return samples


def add_features(samples: pd.DataFrame, source: str = "p25", lag: int = RAIN_LAG_DAYS) -> pd.DataFrame:
    peat = pd.read_parquet(PROC / "peat_share.parquet")
    samples = samples.merge(peat, on=["ix", "iy"], how="left")
    samples["peat_share"] = samples["peat_share"].fillna(0).astype("float32")
    return fill_from_cube(samples, rain_cube, source=source, lag=lag)


def add_danger(samples: pd.DataFrame, lag: int = RAIN_LAG_DAYS) -> pd.DataFrame:
    return fill_from_cube(samples, danger_cube, lag=lag)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--rain", choices=list(CHIRPS), default="p25")
    parser.add_argument("--lag", type=int, default=RAIN_LAG_DAYS)
    parser.add_argument("--danger", action="store_true", help="add CEMS Drought Code etc.")
    args = parser.parse_args()
    samples = pd.read_parquet(PROC / f"{args.name}.parquet")
    out = add_features(samples, args.rain, args.lag)
    if args.danger:
        out = add_danger(out, args.lag)
    default = args.rain == "p25" and args.lag == RAIN_LAG_DAYS
    suffix = ("" if default else f"_{args.rain}_lag{args.lag}") + ("_dc" if args.danger else "")
    path = PROC / f"{args.name}_feat{suffix}.parquet"
    out.to_parquet(path)
    print(f"{len(out):,} rows, {out.shape[1]} columns -> {path.name}")
    print(out[["peat_share", "rain_30d", "rain_90d", "dry_days_30d", "days_since_10mm", "y"]]
          .describe().loc[["mean", "min", "max"]].round(2).to_string())


if __name__ == "__main__":
    main()
