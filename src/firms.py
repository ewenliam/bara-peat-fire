"""Load NASA FIRMS VIIRS yearly country CSVs into one clean hotspot table."""
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
URL = "https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/{year}/viirs-snpp_{year}_Indonesia.csv"

# Sumatra + Kalimantan (Indonesian side), the main peat fire regions.
REGION_BBOX = {"lon_min": 95.0, "lon_max": 119.5, "lat_min": -6.5, "lat_max": 6.0}


def load_years(years: list[int], bbox: dict[str, float] = REGION_BBOX) -> pd.DataFrame:
    """Return vegetation-fire detections for the given years inside bbox.

    Filters:
    - type == 0: assumed "presumed vegetation fire" (types 1-3 are volcano, static
      land source, offshore). [[TODO: verify against the FIRMS VIIRS attribute docs]]
    - confidence != "l": low-confidence day pixels are mostly sun glint (FIRMS FAQ).
    """
    frames = []
    for year in years:
        path = RAW_DIR / f"viirs-snpp_{year}_Indonesia.csv"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing; download from {URL.format(year=year)}")
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
