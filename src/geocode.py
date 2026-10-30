"""Spoken place names to coordinates using GeoNames (CC BY 4.0)."""

import math
import unicodedata
from functools import lru_cache
from pathlib import Path

import pandas as pd
from firms import REGION_BBOX

STOP_WORDS = frozenset([
    "desa", "kelurahan", "kel", "kecamatan", "kec", "kabupaten",
    "kab", "kota", "kampung", "dusun", "dukuh", "village",
    "district", "subdistrict", "regency", "city", "town", "area",
    "the", "of", "near", "behind", "in", "at", "by", "around",
])

KINDS = {
    "PPLA": "town", "PPLA2": "town", "PPLA3": "town",
    "PPLA4": "town", "PPLC": "town",
    "PPL": "place", "PPLX": "place", "PPLL": "place",
    "PPLF": "place", "PPLS": "place",
    "ADM4": "village", "ADM3": "district", "ADM2": "regency",
}

_KIND_ORDER = {
    "village": 0, "place": 1, "town": 2, "district": 3, "regency": 4,
}


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(
        c for c in decomposed if not unicodedata.combining(c)
    )
    lowered = stripped.lower()
    cleaned = "".join(
        c if "a" <= c <= "z" or "0" <= c <= "9" else " "
        for c in lowered
    )
    parts = cleaned.split()
    kept = [p for p in parts if p not in STOP_WORDS]
    return " ".join(kept)


def name_key(text: str) -> str:
    return normalize(text).replace(" ", "")


def build_gazetteer(
    geonames_txt: Path, admin1_txt: Path,
    bbox: dict[str, float], pad: float = 1.0,
) -> pd.DataFrame:
    cols = ["geonameid", "name", "key", "lat", "lon", "kind", "province"]
    admin_df = pd.read_csv(
        admin1_txt, sep="\t", header=None, quoting=3,
        dtype=str, keep_default_na=False,
    )
    prov = {}
    for _, r in admin_df.iterrows():
        prov[str(r[0])] = str(r[2]) if len(r) > 2 else ""
    geo_df = pd.read_csv(
        geonames_txt, sep="\t", header=None, quoting=3,
        dtype=str, keep_default_na=False,
    )
    lat = pd.to_numeric(geo_df[4], errors="coerce")
    lon = pd.to_numeric(geo_df[5], errors="coerce")
    keep_kind = geo_df[7].isin(set(KINDS))
    keep_lat = (lat >= bbox["lat_min"] - pad) & (lat <= bbox["lat_max"] + pad)
    keep_lon = (lon >= bbox["lon_min"] - pad) & (lon <= bbox["lon_max"] + pad)
    mask = keep_kind & keep_lat & keep_lon
    filt = geo_df[mask]
    flat = lat[mask]
    flon = lon[mask]
    records = []
    for idx in filt.index:
        r = filt.loc[idx]
        code = str(r[7])
        kind = KINDS.get(code, "")
        if not kind:
            continue
        names = [str(r[1]), str(r[2])]
        alt = str(r[3])
        if alt:
            names.extend(alt.split(","))
        keys = set()
        for s in names:
            k = name_key(s)
            if k:
                keys.add(k)
        province = prov.get("ID." + str(r[10]), "")
        gid = int(str(r[0]))
        gname = str(r[1])
        glat = float(flat.loc[idx])
        glon = float(flon.loc[idx])
        for k in keys:
            records.append({
                "geonameid": gid, "name": gname, "key": k,
                "lat": glat, "lon": glon,
                "kind": kind, "province": province,
            })
    if not records:
        df = pd.DataFrame({c: [] for c in cols})
        df = df.astype({
            "geonameid": "int64", "name": "object", "key": "object",
            "lat": "float64", "lon": "float64",
            "kind": "object", "province": "object",
        })
        return df[cols]
    df = pd.DataFrame(records, columns=cols)
    df = df.drop_duplicates(["geonameid", "key"])
    df["geonameid"] = df["geonameid"].astype("int64")
    df["lat"] = df["lat"].astype("float64")
    df["lon"] = df["lon"].astype("float64")
    return df[cols]


def _dist_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    mean = math.radians((lat1 + lat2) / 2.0)
    dx = (lon1 - lon2) * 111.32 * math.cos(mean)
    dy = (lat1 - lat2) * 111.32
    return math.hypot(dx, dy)


def geocode(
    place_text: str | None, gazetteer: pd.DataFrame,
    max_candidates: int = 5,
) -> list[dict]:
    if place_text is None:
        return []
    norm = normalize(place_text)
    if not norm:
        return []
    tokens = norm.split()
    n = len(tokens)
    if gazetteer.empty:
        return []
    key_set = set(gazetteer["key"].tolist())
    win_len = 0
    win_texts: list[str] = []
    win_keys: list[str] = []
    for size in range(n, 0, -1):
        texts = [" ".join(tokens[i:i + size]) for i in range(n - size + 1)]
        keys = ["".join(tokens[i:i + size]) for i in range(n - size + 1)]
        hit = any(k in key_set for k in keys)
        if hit:
            win_len = size
            win_texts = texts
            win_keys = keys
            break
    if win_len == 0:
        return []
    key_to_text: dict[str, str] = {}
    for t, k in zip(win_texts, win_keys):
        if k in key_set and k not in key_to_text:
            key_to_text[k] = t
    cand = gazetteer[gazetteer["key"].isin(set(key_to_text))].copy()
    cand["matched"] = cand["key"].map(key_to_text)
    cand["_rank"] = cand["kind"].map(_KIND_ORDER).fillna(99)
    cand = cand.sort_values(["_rank", "geonameid"], kind="stable")
    kept_idx: list = []
    kept_lat: list[float] = []
    kept_lon: list[float] = []
    for idx, row in cand.iterrows():
        dup = False
        for la, lo in zip(kept_lat, kept_lon):
            if _dist_km(float(row["lat"]), float(row["lon"]), la, lo) <= 2.0:
                dup = True
                break
        if not dup:
            kept_idx.append(idx)
            kept_lat.append(float(row["lat"]))
            kept_lon.append(float(row["lon"]))
    cand = cand.loc[kept_idx].head(max_candidates)
    out = []
    for _, row in cand.iterrows():
        out.append({
            "name": str(row["name"]),
            "lat": round(float(row["lat"]), 5),
            "lon": round(float(row["lon"]), 5),
            "kind": str(row["kind"]),
            "province": str(row["province"]),
            "matched": str(row["matched"]),
        })
    return out


@lru_cache(maxsize=1)
def load_gazetteer() -> pd.DataFrame:
    root = Path(__file__).resolve().parents[1]
    geo_path = root / "data" / "raw" / "geonames" / "ID.txt"
    admin_path = root / "data" / "raw" / "geonames" / "admin1CodesASCII.txt"
    if not admin_path.is_file():
        admin_path = root / "data" / "raw" / "admin1CodesASCII.txt"
    cache_path = root / "data" / "processed" / "gazetteer.parquet"
    if cache_path.is_file():
        return pd.read_parquet(cache_path)
    df = build_gazetteer(geo_path, admin_path, REGION_BBOX)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    return df
