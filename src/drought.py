"""Download the Copernicus CEMS Drought Code for the study region.

Dataset: cems-fire-historical-v1 on the Copernicus Early Warning Data Store (EWDS),
reanalysis, 0.25 deg, daily, CC-BY-4.0. "consolidated_dataset" covers 1940 to Jul 2026;
"intermediate_dataset" covers Oct 2022 to Oct 2026 and is used for the newest months.

Limits found on Oct 27 that shape this script:
- The data comes from the ECMWF MARS tape archive, whose queue held 65 running and 3,279
  queued requests, so one request can wait for hours.
- EWDS keeps only about 2 queued requests per user for this dataset and rejects the rest
  ("Maximum number queued requests ... reached"). `submit` stops at MAX_QUEUED.
- A request may cost at most 3,720 units. One year of the Drought Code costs 365, one year of
  the four indices 1,460. So only the Drought Code is downloaded: 10 years per request.
  (2011 and 2019 were downloaded earlier with DMC, FWI and KBDI as well.)

Requests are submitted without waiting; their ids are kept in data/raw/cems_fire/jobs.json and
`fetch` downloads whatever has finished and splits it into one file per year.

Needs in .env:
    EWDS_KEY=<personal access token from your EWDS profile page>
and the dataset's licence accepted once.

Usage:
    python src/drought.py submit 2011 2025                  # up to MAX_QUEUED requests
    python src/drought.py submit 2026 2026 --intermediate
    python src/drought.py track <job id> 2011               # add a job submitted elsewhere
    python src/drought.py fetch                             # download finished jobs
"""
import argparse
import json
import os
import zipfile
from pathlib import Path

import xarray as xr
from dotenv import load_dotenv
from ecmwf.datastores import Client

from firms import REGION_BBOX

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "raw" / "cems_fire"
JOBS = OUT_DIR / "jobs.json"
EWDS_URL = "https://ewds.climate.copernicus.eu/api"
DATASET = "cems-fire-historical-v1"
VARIABLES = ["drought_code"]
PAD = 0.5
YEARS_PER_REQUEST = 10
MAX_QUEUED = 2


def request(years: list[int], dataset_type: str) -> dict:
    b = REGION_BBOX
    return {
        "product_type": "reanalysis",
        "variable": VARIABLES,
        "dataset_type": dataset_type,
        "system_version": ["4_1"],
        "year": [str(y) for y in years],
        "month": [f"{m:02d}" for m in range(1, 13)],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "grid": "0.25/0.25",
        "data_format": "netcdf",
        "area": [b["lat_max"] + PAD, b["lon_min"] - PAD, b["lat_min"] - PAD, b["lon_max"] + PAD],
    }


def client() -> Client:
    load_dotenv(ROOT / ".env")
    if not os.getenv("EWDS_KEY"):
        raise SystemExit("EWDS_KEY is missing from .env")
    return Client(url=EWDS_URL, key=os.environ["EWDS_KEY"])


def year_path(year: int, tag: str) -> Path:
    return OUT_DIR / f"cems_fire_{year}_{tag}.nc"


def load_jobs() -> dict:
    return json.loads(JOBS.read_text(encoding="utf-8")) if JOBS.exists() else {}


def save_jobs(jobs: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JOBS.write_text(json.dumps(jobs, indent=1), encoding="utf-8")


def submit(c: Client, first: int, last: int, intermediate: bool) -> None:
    tag = "int" if intermediate else "con"
    dataset_type = "intermediate_dataset" if intermediate else "consolidated_dataset"
    jobs = load_jobs()
    pending = {y for j in jobs.values() if j["tag"] == tag for y in j["years"]}
    todo = [y for y in range(first, last + 1) if not year_path(y, tag).exists() and y not in pending]
    for i in range(0, len(todo), YEARS_PER_REQUEST):
        if len(jobs) >= MAX_QUEUED:
            print(f"{len(jobs)} jobs pending, not submitting {todo[i:]} yet")
            break
        years = todo[i:i + YEARS_PER_REQUEST]
        cost = c.estimate_costs(DATASET, request(years, dataset_type))
        if cost["cost"] > cost["limit"]:
            raise SystemExit(f"{years}: cost {cost['cost']} over limit {cost['limit']}")
        remote = c.submit(DATASET, request(years, dataset_type))
        jobs[remote.request_id] = {"years": years, "tag": tag}
        save_jobs(jobs)
        print(f"submitted {years} ({tag}): {remote.request_id}")


def split_years(paths: list[Path], years: list[int], tag: str) -> None:
    """Write one file per year from a downloaded file (or the files of an unzipped archive)."""
    # xr.merge instead of open_mfdataset: no dask in the venv
    with xr.merge([xr.open_dataset(p) for p in paths]) as ds:
        for year in years:
            part = ds.sel(valid_time=ds["valid_time"].dt.year == year)
            part.load().to_netcdf(year_path(year, tag))
            print(f"{year}: {part.sizes['valid_time']} days -> {year_path(year, tag).name}")


def fetch(c: Client) -> None:
    jobs = load_jobs()
    for job_id, job in list(jobs.items()):
        status = c.get_remote(job_id).status
        print(f"{job_id} {job['years']} {job['tag']}: {status}")
        if status in ("failed", "rejected", "dismissed", "deleted"):
            del jobs[job_id]  # resubmit with `submit`
            continue
        if status != "successful":
            continue
        raw = OUT_DIR / f"{job_id}.download"
        c.download_results(job_id, str(raw))
        if zipfile.is_zipfile(raw):  # some requests come back zipped, one file per variable
            folder = OUT_DIR / job_id
            with zipfile.ZipFile(raw) as z:
                z.extractall(folder)
            paths = sorted(folder.glob("*.nc"))
        else:
            paths = [raw.replace(raw.with_suffix(".nc"))]  # replace: rename fails on Windows if the .nc exists
        split_years(paths, job["years"], job["tag"])
        del jobs[job_id]
        save_jobs(jobs)
    save_jobs(jobs)
    print(f"{len(jobs)} jobs still pending")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("submit")
    p.add_argument("first", type=int)
    p.add_argument("last", type=int)
    p.add_argument("--intermediate", action="store_true")
    p = sub.add_parser("track")
    p.add_argument("job_id")
    p.add_argument("years", type=int, nargs="+")
    p.add_argument("--intermediate", action="store_true")
    sub.add_parser("fetch")
    args = parser.parse_args()

    c = client()
    if args.cmd == "submit":
        submit(c, args.first, args.last, args.intermediate)
    elif args.cmd == "track":
        jobs = load_jobs()
        jobs[args.job_id] = {"years": args.years, "tag": "int" if args.intermediate else "con"}
        save_jobs(jobs)
    else:
        fetch(c)


if __name__ == "__main__":
    main()
