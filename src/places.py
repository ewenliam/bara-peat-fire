"""Place names and crew bases from GeoNames (CC BY 4.0, https://www.geonames.org/).

Files: https://download.geonames.org/export/dump/ID.zip (all Indonesian places) and
admin1CodesASCII.txt (province names), saved in data/raw/geonames/.

The province of a point is taken from its nearest populated place, so it can be wrong right
at a provincial border. Crew bases default to the provincial capitals (GeoNames feature
code PPLA) inside the study region: an assumption until real Manggala Agni Daops locations
are added. [[TODO: need source for Manggala Agni Daops base locations]]
"""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from detect import KM_PER_DEG
from firms import REGION_BBOX

ROOT = Path(__file__).resolve().parents[1]
GEONAMES = ROOT / "data" / "raw" / "geonames"
CACHE = ROOT / "data" / "processed" / "places.parquet"
COLUMNS = ["geonameid", "name", "asciiname", "alternatenames", "lat", "lon", "feature_class",
           "feature_code", "country", "cc2", "admin1", "admin2", "admin3", "admin4",
           "population", "elevation", "dem", "timezone", "modified"]
PAD = 1.0
PROVINCE_VOTES = 15  # province = most common among the 15 nearest places, robust near borders
# Sumatra and Kalimantan provinces (GeoNames names); the bbox also clips Java and Sulawesi.
STUDY_PROVINCES = {"Aceh", "North Sumatra", "West Sumatra", "Riau", "Riau Islands", "Jambi",
                   "Bengkulu", "South Sumatra", "Bangka-Belitung Islands", "Lampung",
                   "West Kalimantan", "Central Kalimantan", "South Kalimantan",
                   "East Kalimantan", "North Kalimantan"}
# Landmass of each study province, so a ground crew is never routed across the sea.
# (Bangka-Belitung is two islands; crews based in Pangkalpinang on Bangka may still be sent to
# Belitung. Found by the map self-check on 2019-09-15: a straight-line route from Pangkalpinang
# to mainland Sumatra crossed the Bangka Strait.)
ISLAND = {p: "Kalimantan" for p in ("West Kalimantan", "Central Kalimantan", "South Kalimantan",
                                     "East Kalimantan", "North Kalimantan")}
ISLAND |= {p: "Sumatra" for p in ("Aceh", "North Sumatra", "West Sumatra", "Riau", "Jambi",
                                   "Bengkulu", "South Sumatra", "Lampung")}
ISLAND |= {"Bangka-Belitung Islands": "Bangka-Belitung", "Riau Islands": "Riau Islands"}
PROVINCE_ID = {  # official Indonesian names; the grounding check accepts either spelling
    "Aceh": "Aceh", "North Sumatra": "Sumatera Utara", "West Sumatra": "Sumatera Barat",
    "Riau": "Riau", "Riau Islands": "Kepulauan Riau", "Jambi": "Jambi", "Bengkulu": "Bengkulu",
    "South Sumatra": "Sumatera Selatan", "Bangka-Belitung Islands": "Kepulauan Bangka Belitung",
    "Lampung": "Lampung", "West Kalimantan": "Kalimantan Barat",
    "Central Kalimantan": "Kalimantan Tengah", "South Kalimantan": "Kalimantan Selatan",
    "East Kalimantan": "Kalimantan Timur", "North Kalimantan": "Kalimantan Utara",
}


@lru_cache(maxsize=1)
def places() -> pd.DataFrame:
    """Populated places (feature class P) in and around the study region, with province names."""
    if CACHE.exists():
        return pd.read_parquet(CACHE)
    df = pd.read_csv(GEONAMES / "ID.txt", sep="\t", names=COLUMNS, quoting=3, low_memory=False,
                     usecols=["name", "lat", "lon", "feature_class", "feature_code", "admin1",
                              "population"], dtype={"admin1": str})
    b = REGION_BBOX
    df = df[(df["feature_class"] == "P")
            & df["lon"].between(b["lon_min"] - PAD, b["lon_max"] + PAD)
            & df["lat"].between(b["lat_min"] - PAD, b["lat_max"] + PAD)]
    admin = pd.read_csv(GEONAMES / "admin1CodesASCII.txt", sep="\t", quoting=3,
                        names=["code", "province", "province_ascii", "id"])
    admin = admin[admin["code"].str.startswith("ID.")]
    df = df.merge(admin.assign(admin1=admin["code"].str[3:])[["admin1", "province_ascii"]],
                  on="admin1", how="left").rename(columns={"province_ascii": "province"})
    df = df.drop(columns="feature_class").reset_index(drop=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE)
    return df


def km_xy(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    lat0 = (REGION_BBOX["lat_min"] + REGION_BBOX["lat_max"]) / 2
    return np.column_stack([np.asarray(lon) * KM_PER_DEG * np.cos(np.deg2rad(lat0)),
                            np.asarray(lat) * KM_PER_DEG])


@lru_cache(maxsize=1)
def _tree() -> cKDTree:
    p = places()
    return cKDTree(km_xy(p["lat"].to_numpy(), p["lon"].to_numpy()))


def locate(lat: np.ndarray, lon: np.ndarray) -> pd.DataFrame:
    """Nearest populated place and distance to it, and the majority province nearby."""
    dist, idx = _tree().query(km_xy(np.atleast_1d(lat), np.atleast_1d(lon)), k=PROVINCE_VOTES)
    p = places()
    names = p["name"].to_numpy()
    provinces = p["province"].to_numpy()[idx]
    return pd.DataFrame({
        "place": names[idx[:, 0]],
        "place_km": np.round(dist[:, 0], 1),
        "province": [pd.Series(row).mode().iloc[0] for row in provinces],
    })


def crew_bases() -> pd.DataFrame:
    """Provincial capitals inside the study region (default crew bases, an assumption)."""
    p = places()
    b = REGION_BBOX
    caps = p[(p["feature_code"] == "PPLA") & p["province"].isin(STUDY_PROVINCES)
             & p["lon"].between(b["lon_min"], b["lon_max"])
             & p["lat"].between(b["lat_min"], b["lat_max"])]
    return caps[["name", "province", "lat", "lon"]].reset_index(drop=True)


if __name__ == "__main__":
    print(f"{len(places()):,} populated places")
    print(crew_bases().to_string())
    print(locate(np.array([-1.649, -2.209, 0.75]), np.array([104.082, 113.819, 110.95])).to_string())
