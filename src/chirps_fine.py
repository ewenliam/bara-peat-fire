"""Download CHIRPS v2.0 daily 0.05 deg rainfall for the study region only.

Reads the global yearly netCDF over HTTP byte ranges and keeps the region window,
so each year is a few MB locally instead of 1.2 GB.

Usage:
    python src/chirps_fine.py 2011 2026
"""
import sys
import time
from pathlib import Path

import xarray as xr

from firms import REGION_BBOX

ROOT = Path(__file__).resolve().parents[1]
URL = ("https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/netcdf/p05/"
       "chirps-v2.0.{year}.days_p05.nc#mode=bytes")
OUT_DIR = ROOT / "data" / "raw" / "chirps_p05"
PAD = 0.5


def fetch(year: int) -> Path:
    out = OUT_DIR / f"chirps_p05_{year}_region.nc"
    if out.exists():
        return out
    b = REGION_BBOX
    for attempt in range(4):
        try:
            with xr.open_dataset(URL.format(year=year), engine="netcdf4") as ds:
                sub = ds["precip"].sel(
                    latitude=slice(b["lat_min"] - PAD, b["lat_max"] + PAD),
                    longitude=slice(b["lon_min"] - PAD, b["lon_max"] + PAD),
                ).load()
            break
        except OSError as err:
            print(f"{year}: attempt {attempt + 1} failed ({err}); retrying")
            time.sleep(10 * (attempt + 1))
    else:
        raise RuntimeError(f"{year}: download failed")
    tmp = out.with_suffix(".tmp")
    sub.to_dataset().to_netcdf(tmp, encoding={"precip": {"zlib": True, "complevel": 4}})
    tmp.replace(out)
    return out


def main() -> None:
    first, last = int(sys.argv[1]), int(sys.argv[2])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for year in range(first, last + 1):
        t = time.time()
        path = fetch(year)
        print(f"{year}: {path.stat().st_size / 1e6:.1f} MB in {time.time() - t:.0f} s")


if __name__ == "__main__":
    main()
