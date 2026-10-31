"""Live mode: bring 2026 up to the latest full day so Bara forecasts from this week's data.

Claude-written. It prepares the same inputs the 2019 and 2023 seasons use, then the usual builders
(labels.py, features.py, ignition.py) turn them into the tables FireTools reads.

Detections: NASA FIRMS area API, VIIRS on NOAA-20 (S-NPP, the training satellite, is being retired, so
live mode uses NOAA-20 throughout and the app says so). Standard processing (VIIRS_NOAA20_SP) up to its last
date, near-real-time (VIIRS_NOAA20_NRT) after it. NRT rows carry no `type`, so an NRT detection in any 0.01
deg cell that standard processing ever flagged as volcano, static land source or offshore (type 1 to 3) is
dropped; the rest count as vegetation fires. Detections inside neighbouring countries are dropped, to match
NASA's Indonesia files used in training. Written to data/raw/viirs-jpss1_2026_Indonesia.csv.

Rain: CHIRPS final, then CHIRPS preliminary, then IMERG Early (NASA GES DISC, averaged onto the CHIRPS
0.25 deg grid) for days no CHIRPS product covers yet. Written to data/raw/chirps/live_2026_p25.nc, with the
product used for every day in live_2026_rain_sources.json. The model was trained on CHIRPS final only.
The build stops if rain does not reach the live day minus the 2-day lag (missing rain would read as 0 mm).

The live day is the last complete UTC day of detections; its forecast is for the next day.

Needs FIRMS_MAP_KEY and EARTHDATA_TOKEN in .env. Usage:
    python src/live.py            # fetch, check, build the 2026 tables
"""
import io
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr
from dotenv import dotenv_values

from features import RAIN_LAG_DAYS
from firms import RAW_DIR, REGION_BBOX, load_years

ROOT = Path(__file__).resolve().parents[1]
YEAR = 2026
FIRMS_API = "https://firms.modaps.eosdis.nasa.gov/api"
CACHE = RAW_DIR / "live"
DETECTIONS = RAW_DIR / f"viirs-jpss1_{YEAR}_Indonesia.csv"
RAIN = RAW_DIR / "chirps" / f"live_{YEAR}_p25.nc"
RAIN_SOURCES = RAW_DIR / "chirps" / f"live_{YEAR}_rain_sources.json"
CHIRPS_FINAL = RAW_DIR / "chirps" / f"chirps-v2.0.{YEAR}.days_p25.nc"
CHIRPS_PRELIM = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/prelim/global_daily/netcdf/p25/chirps-v2.0.{y}.{m:02d}.days_p25.nc"
IMERG = ("https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGDE.07/{d:%Y}/{d:%m}/"
         "3B-DAY-E.MS.MRG.3IMERG.{d:%Y%m%d}-S000000-E235959.V07C.nc4")
COLUMNS = ["latitude", "longitude", "acq_date", "acq_time", "frp", "confidence", "daynight", "type"]
NEIGHBOURS_FILE = RAW_DIR / "naturalearth" / "ne_10m_admin_0_countries.geojson"
NEIGHBOURS = ["MYS", "BRN", "SGP", "THA", "TLS", "PNG", "PHL"]
RES = 0.01
PAD = 1.0  # degrees of margin around the study box for rain


def _env(key: str) -> str:
    value = (dotenv_values(ROOT / ".env").get(key) or "").strip()
    if not value:
        raise SystemExit(f"{key} is not in .env")
    return value


def availability() -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    text = requests.get(f"{FIRMS_API}/data_availability/csv/{_env('FIRMS_MAP_KEY')}/ALL", timeout=60).text
    df = pd.read_csv(io.StringIO(text))
    return {r.data_id: (pd.Timestamp(r.min_date), pd.Timestamp(r.max_date)) for r in df.itertuples()}


def fetch(source: str, start: pd.Timestamp, end: pd.Timestamp, refresh_after: pd.Timestamp) -> pd.DataFrame:
    """Detections for [start, end] in 5-day chunks; chunks are cached unless they reach refresh_after."""
    b = REGION_BBOX
    area = f"{b['lon_min']},{b['lat_min']},{b['lon_max']},{b['lat_max']}"
    CACHE.mkdir(parents=True, exist_ok=True)
    parts = []
    for day in pd.date_range(start, end, freq="5D"):
        n = min(5, (end - day).days + 1)
        path = CACHE / f"{source}_{day:%Y%m%d}_{n}.csv"
        if not path.exists() or day + pd.Timedelta(days=n - 1) >= refresh_after:
            url = f"{FIRMS_API}/area/csv/{_env('FIRMS_MAP_KEY')}/{source}/{area}/{n}/{day:%Y-%m-%d}"
            r = requests.get(url, timeout=120)
            if r.status_code != 200 or not r.text.startswith("latitude"):
                raise SystemExit(f"FIRMS {source} {day:%Y-%m-%d}: HTTP {r.status_code}: {r.text[:120]}")
            path.write_text(r.text, encoding="utf-8")
            time.sleep(0.5)
        parts.append(pd.read_csv(path))
    return pd.concat(parts, ignore_index=True)


def static_cells(sp: pd.DataFrame) -> set[tuple[int, int]]:
    """0.01 deg cells flagged as volcano, static land source or offshore in recent standard data."""
    hist = pd.concat([sp] + [pd.read_csv(RAW_DIR / f"viirs-snpp_{y}_Indonesia.csv", usecols=["latitude", "longitude", "type"])
                             for y in (YEAR - 3, YEAR - 2, YEAR - 1)])
    flagged = hist[hist["type"].isin([1, 2, 3])]
    return set(zip(np.floor(flagged["longitude"] / RES).astype(int), np.floor(flagged["latitude"] / RES).astype(int)))


def in_neighbour_country(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """True inside Malaysia, Brunei, Singapore, Thailand, Timor-Leste, PNG or the Philippines.

    The area API returns everything in the study box, but training used NASA's Indonesia country
    files. Dropping detections in neighbouring countries (Natural Earth 10m boundaries, public domain)
    matches those files: on the 2023 Indonesia file it drops 0 of 133,681 detections, while keeping
    only points inside Indonesia's own polygon would wrongly drop 1.0% (its coastline is generalised).
    """
    from matplotlib.path import Path as MPath
    with NEIGHBOURS_FILE.open(encoding="utf-8") as f:
        countries = json.load(f)["features"]
    paths = []
    for feat in countries:
        if feat["properties"].get("ADM0_A3") in NEIGHBOURS:
            g = feat["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            paths += [MPath(np.array(p[0])) for p in polys]
    pts = np.column_stack([lon, lat])
    out = np.zeros(len(pts), dtype=bool)
    for path in paths:
        (x0, y0), (x1, y1) = path.vertices.min(0), path.vertices.max(0)
        m = ~out & (pts[:, 0] >= x0) & (pts[:, 0] <= x1) & (pts[:, 1] >= y0) & (pts[:, 1] <= y1)
        if m.any():
            out[m] = path.contains_points(pts[m])
    return out


def build_detections() -> pd.Timestamp:
    """Write the 2026 NOAA-20 file; return the live day (last complete UTC day)."""
    av = availability()
    sp_end = min(av["VIIRS_NOAA20_SP"][1], pd.Timestamp(f"{YEAR}-12-31"))
    nrt_end = av["VIIRS_NOAA20_NRT"][1]
    live_day = nrt_end - pd.Timedelta(days=1)  # nrt_end is still filling in
    sp = fetch("VIIRS_NOAA20_SP", pd.Timestamp(f"{YEAR}-01-01"), sp_end, refresh_after=sp_end + pd.Timedelta(days=1))
    nrt = fetch("VIIRS_NOAA20_NRT", sp_end + pd.Timedelta(days=1), live_day, refresh_after=live_day - pd.Timedelta(days=2))
    static = static_cells(sp)
    cell = list(zip(np.floor(nrt["longitude"] / RES).astype(int), np.floor(nrt["latitude"] / RES).astype(int)))
    dropped = np.array([c in static for c in cell])
    nrt = nrt[~dropped].assign(type=0)
    out = pd.concat([sp[COLUMNS], nrt[COLUMNS]], ignore_index=True)
    out = out[pd.to_datetime(out["acq_date"]) <= live_day]
    abroad = in_neighbour_country(out["longitude"].to_numpy(), out["latitude"].to_numpy())
    out = out[~abroad]
    out.to_csv(DETECTIONS, index=False)
    print(f"detections: {len(sp):,} standard to {sp_end:%Y-%m-%d}, {len(nrt):,} near-real-time to "
          f"{live_day:%Y-%m-%d} ({int(dropped.sum())} NRT rows dropped in static-source cells, "
          f"{int(abroad.sum()):,} rows dropped in neighbouring countries)", flush=True)
    return live_day


def _region(da: xr.DataArray) -> xr.DataArray:
    b = REGION_BBOX
    return da.sel(latitude=slice(b["lat_min"] - PAD, b["lat_max"] + PAD),
                  longitude=slice(b["lon_min"] - PAD, b["lon_max"] + PAD))


def _imerg_on_grid(day: pd.Timestamp, grid: xr.DataArray) -> xr.DataArray:
    """One IMERG Early day (0.1 deg), averaged into the CHIRPS 0.25 deg cells of `grid`."""
    path = RAW_DIR / "imerg_early" / IMERG.format(d=day).rsplit("/", 1)[1]
    if not path.exists():
        r = requests.get(IMERG.format(d=day), headers={"Authorization": f"Bearer {_env('EARTHDATA_TOKEN')}"}, timeout=180)
        if r.status_code != 200 or r.content[:4] != b"\x89HDF":
            raise SystemExit(f"IMERG Early {day:%Y-%m-%d} not available yet (HTTP {r.status_code})")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    with xr.open_dataset(path) as ds:
        p = ds["precipitation"].isel(time=0).transpose("lat", "lon").load()
    lat0, lon0 = float(grid.latitude[0]), float(grid.longitude[0])
    ny, nx = grid.sizes["latitude"], grid.sizes["longitude"]
    iy = np.rint((p["lat"].values - lat0) / 0.25).astype(int)
    ix = np.rint((p["lon"].values - lon0) / 0.25).astype(int)
    ok_y, ok_x = (iy >= 0) & (iy < ny), (ix >= 0) & (ix < nx)
    vals = p.values[np.ix_(ok_y, ok_x)]
    cy, cx = np.meshgrid(iy[ok_y], ix[ok_x], indexing="ij")
    total, count = np.zeros((ny, nx)), np.zeros((ny, nx))
    good = ~np.isnan(vals)
    np.add.at(total, (cy[good], cx[good]), vals[good])
    np.add.at(count, (cy[good], cx[good]), 1)
    out = np.where(count > 0, total / np.maximum(count, 1), np.nan).astype("float32")
    return xr.DataArray(out[None], dims=("time", "latitude", "longitude"),
                        coords={"time": [day], "latitude": grid.latitude, "longitude": grid.longitude})


def build_rain(live_day: pd.Timestamp) -> None:
    """CHIRPS final, then preliminary, then IMERG Early, up to live_day - lag; stop if a day is missing."""
    need = live_day - pd.Timedelta(days=RAIN_LAG_DAYS)
    with xr.open_dataset(CHIRPS_FINAL) as ds:
        final = _region(ds["precip"]).load()
    parts, sources = [final], {f"{t:%Y-%m-%d}": "CHIRPS final" for t in pd.to_datetime(final.time.values)}
    last = pd.Timestamp(final.time.values[-1])
    for month in pd.period_range(last + pd.Timedelta(days=1), need, freq="M"):  # preliminary monthly files
        path = RAW_DIR / "chirps_prelim" / f"chirps-v2.0.{month.year}.{month.month:02d}.days_p25.nc"
        r = requests.get(CHIRPS_PRELIM.format(y=month.year, m=month.month), timeout=180)
        if r.status_code == 200:  # refresh: CHC extends the current month every pentad
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        if not path.exists():
            break
        with xr.open_dataset(path) as ds:
            pre = _region(ds["precip"]).load().sel(time=slice(last + pd.Timedelta(days=1), need))
        if not pre.sizes["time"]:
            break
        parts.append(pre)
        sources |= {f"{t:%Y-%m-%d}": "CHIRPS preliminary" for t in pd.to_datetime(pre.time.values)}
        last = pd.Timestamp(pre.time.values[-1])
    for day in pd.date_range(last + pd.Timedelta(days=1), need):
        parts.append(_imerg_on_grid(day, final))
        sources[f"{day:%Y-%m-%d}"] = "IMERG Early (regridded)"
    rain = xr.concat(parts, dim="time")
    days = pd.to_datetime(rain.time.values)
    missing = pd.date_range(f"{YEAR}-01-01", need).difference(days)
    if len(missing) or days.max() < need:
        raise SystemExit(f"rain does not cover every day to {need:%Y-%m-%d}; missing: {list(missing[:5])}")
    xr.Dataset({"precip": rain}).to_netcdf(RAIN)
    RAIN_SOURCES.write_text(json.dumps(sources, indent=1), encoding="utf-8")
    counts = {k: int(v) for k, v in pd.Series(sources).value_counts().items()}
    print(f"rain to {need:%Y-%m-%d} (live day {live_day:%Y-%m-%d} minus {RAIN_LAG_DAYS}-day lag): {counts}", flush=True)


def build_tables(live_day: pd.Timestamp) -> None:
    """The same builders as for 2019 and 2023, for 2026 up to the live day."""
    from features import add_features
    from ignition import build as build_ignition
    from labels import build_samples
    proc = ROOT / "data" / "processed"
    samples, stats = build_samples([YEAR], neg_frac=1.0)
    samples.to_parquet(proc / f"samples_{YEAR}_{YEAR}.parquet")
    add_features(samples).to_parquet(proc / f"samples_{YEAR}_{YEAR}_feat.parquet")
    build_ignition([YEAR], neg_frac=1.0, end=live_day).to_parquet(proc / f"ign_{YEAR}.parquet")
    days = samples["date"]
    print(f"tables: {len(samples):,} candidate cell-days, {days.min():%Y-%m-%d} to {days.max():%Y-%m-%d}", flush=True)


def main() -> None:
    live_day = build_detections()
    build_rain(live_day)
    build_tables(live_day)
    (ROOT / "data" / "processed" / f"live_{YEAR}.json").write_text(json.dumps({
        "live_day": f"{live_day:%Y-%m-%d}", "forecast_for": f"{live_day + pd.Timedelta(days=1):%Y-%m-%d}",
        "built_utc": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M"), "satellite": "VIIRS NOAA-20",
        "rain_sources": {k: int(v) for k, v in
                         pd.Series(json.loads(RAIN_SOURCES.read_text(encoding="utf-8"))).value_counts().items()},
    }, indent=1), encoding="utf-8")
    print(f"live day {live_day:%Y-%m-%d}; forecast for {live_day + pd.Timedelta(days=1):%Y-%m-%d}", flush=True)


if __name__ == "__main__":
    main()
