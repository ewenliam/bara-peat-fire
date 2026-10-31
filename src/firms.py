"""Load NASA FIRMS VIIRS yearly country CSVs into one clean hotspot table."""
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
URL = "https://firms.modaps.eosdis.nasa.gov/data/country/{product}/{year}/{product}_{year}_Indonesia.csv"
# FIRMS archive folder per satellite; NOAA-20 is filed as "jpss1"
PRODUCTS = {"snpp": "viirs-snpp", "noaa20": "viirs-jpss1"}
# Satellite per year when none is asked for: the live year comes from NOAA-20 (live.py), since S-NPP,
# the training satellite, is being retired. Every other year is S-NPP, as in training and testing.
YEAR_SENSOR = {2026: "noaa20"}
LAST_YEAR = max(YEAR_SENSOR)

# Sumatra + Kalimantan (Indonesian side), the main peat fire regions.
REGION_BBOX = {"lon_min": 95.0, "lon_max": 119.5, "lat_min": -6.5, "lat_max": 6.0}


def load_years(years: list[int], bbox: dict[str, float] = REGION_BBOX,
               sensor: str | None = None) -> pd.DataFrame:
    """Return vegetation-fire detections for the given years inside bbox, from one satellite.

    Filters:
    - type == 0: assumed "presumed vegetation fire" (types 1-3 are volcano, static
      land source, offshore). [[TODO: verify against the FIRMS VIIRS attribute docs]]
    - confidence != "l": low-confidence day pixels are mostly sun glint (FIRMS FAQ).
    """
    frames = []
    for year in years:
        product = PRODUCTS[sensor or YEAR_SENSOR.get(year, "snpp")]
        path = RAW_DIR / f"{product}_{year}_Indonesia.csv"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing; download from {URL.format(product=product, year=year)}")
        frames.append(pd.read_csv(path))
    df = pd.concat(frames, ignore_index=True)

    df = df[(df["type"] == 0) & (df["confidence"] != "l")]
    df = df[
        df["longitude"].between(bbox["lon_min"], bbox["lon_max"])
        & df["latitude"].between(bbox["lat_min"], bbox["lat_max"])
    ]
    time = df["acq_time"].astype(str).str.zfill(4)
    df = df.assign(
        acq_datetime=pd.to_datetime(df["acq_date"] + " " + time, format="%Y-%m-%d %H%M"),
        acq_date=pd.to_datetime(df["acq_date"]),
    )
    cols = ["latitude", "longitude", "acq_date", "acq_datetime", "frp", "confidence", "daynight"]
    return df[cols].sort_values("acq_datetime").reset_index(drop=True)
