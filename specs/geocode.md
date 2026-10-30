# Spec: src/geocode.py (spoken place name to candidate coordinates)

Builder: Muse Spark writes this module. Acceptance: `python tests/test_geocode.py` prints
`all passed` and exits 0. Tests use small synthetic GeoNames files, not the real 52 MB one.

## Context

A field report says where a fire is in words, e.g. `place_text = "Kalampangan village"`.
Bara needs coordinates to match the report to a satellite fire cluster. Source: the GeoNames
Indonesia dump `data/raw/geonames/ID.txt` (CC BY 4.0), tab separated, no header, 19 columns:
`geonameid, name, asciiname, alternatenames, latitude, longitude, feature_class,
feature_code, country_code, cc2, admin1_code, admin2_code, admin3_code, admin4_code,
population, elevation, dem, timezone, modification_date`. `alternatenames` is a comma
separated list (may be empty). Province names come from `admin1CodesASCII.txt`: tab
separated, no header, columns `code` (like `ID.13`), `name`, `name_ascii`, `geonameid`.

Checked on the real file (2026-10-30): villages are administrative rows with a prefix, e.g.
`Kelurahan Kalampangan` (feature A, ADM4, -2.28972, 114.01167) and two different
`Desa Tumbang Nusa` rows about 140 km apart. Towns can be spelled with or without a space
(`Palangkaraya`, `Kota Palangka Raya`).

Rules that protect crews from a wrong location:
- Exact matching on normalised names only. No fuzzy or edit-distance matching: a misheard
  name must return no candidates (the caller then asks the reporter), never a near miss.
- Return every distinct candidate, so the caller can ask "which one" when there are several.

## Interface

```python
STOP_WORDS: frozenset[str]
KINDS: dict[str, str]  # feature code -> kind

def normalize(text: str) -> str
def name_key(text: str) -> str
def build_gazetteer(geonames_txt: Path, admin1_txt: Path, bbox: dict[str, float], pad: float = 1.0) -> pd.DataFrame
def geocode(place_text: str | None, gazetteer: pd.DataFrame, max_candidates: int = 5) -> list[dict]
def load_gazetteer() -> pd.DataFrame
```

- `STOP_WORDS`: administrative prefixes and filler words, removed by `normalize`:
  `desa, kelurahan, kel, kecamatan, kec, kabupaten, kab, kota, kampung, dusun, dukuh,
  village, district, subdistrict, regency, city, town, area, the, of, near, behind, in, at,
  by, around`.
- `normalize`: Unicode NFKD, drop combining marks, lower case, replace every character that
  is not an ASCII letter or digit with a space, split on whitespace, drop `STOP_WORDS`, join
  with single spaces. `normalize("Desa  Tumbang-Nusa") == "tumbang nusa"`,
  `normalize("behind Kālampangan village") == "kalampangan"`.
- `name_key`: `normalize(text)` with the spaces removed (`"palangkaraya"` for both
  `Palangka Raya` and `Palangkaraya`).
- `KINDS`: `PPLA` `PPLA2` `PPLA3` `PPLA4` `PPLC` -> `"town"`; `PPL` `PPLX` `PPLL` `PPLF`
  `PPLS` -> `"place"`; `ADM4` -> `"village"`; `ADM3` -> `"district"`; `ADM2` -> `"regency"`.
  Rows whose feature code is not in `KINDS` are not in the gazetteer (rivers, hills,
  schools, airports, provinces).
- `build_gazetteer`: read both files with pandas (`quoting=3`, `dtype=str`, `keep_default_na=False`
  for text columns), keep rows with a feature code in `KINDS` whose latitude and longitude
  lie inside `bbox` widened by `pad` degrees on every side (`bbox` keys `lon_min, lon_max,
  lat_min, lat_max`). Province: the `name_ascii` of code `"ID." + admin1_code` (empty string
  if unknown). Output one row per (GeoNames row, distinct non-empty key), keys taken from
  `name`, `asciiname` and every alternate name. Columns exactly
  `geonameid, name, key, lat, lon, kind, province` with `lat` and `lon` as float and
  `geonameid` as int. No duplicate (geonameid, key) rows.
- `geocode`:
  - Return `[]` for None or a text whose `normalize` is empty.
  - Tokens = `normalize(place_text).split()`. Try contiguous token windows from the longest
    to the shortest (all windows of length n, left to right, before length n - 1). The first
    length that matches any gazetteer `key` (window joined without spaces) wins; candidates
    are all gazetteer rows matching any window of that length.
  - Merge candidates that are the same place: rows within 2.0 km of an already kept candidate
    (equirectangular distance, 111.32 km per degree, longitude scaled by cos of the mean
    latitude) are dropped. Visit rows in kind order `village, place, town, district,
    regency`, then by geonameid, so the most specific row survives.
  - Return at most `max_candidates` dicts in that same order, each
    `{"name", "lat", "lon", "kind", "province", "matched"}` where `matched` is the window
    text (with spaces) that matched, `lat` and `lon` rounded to 5 decimals.
- `load_gazetteer`: build from `data/raw/geonames/ID.txt` and `admin1CodesASCII.txt` (paths
  relative to the repository root, found from `__file__`) with `REGION_BBOX` from `firms`,
  cache it to `data/processed/gazetteer.parquet`, and read the cache when it exists. Wrap
  with `functools.lru_cache(maxsize=1)`.

## Rules
- Imports: standard library, `numpy`, `pandas`, and `REGION_BBOX` from `firms`. No network.
- No `except Exception`, no bare `except`, no printing. About 150 lines or fewer. Module
  docstring naming GeoNames and its CC BY 4.0 licence. Type hints, PEP 8, plain ASCII, no em
  dashes.
