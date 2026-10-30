"""Acceptance tests for src/geocode.py (written by Claude, see specs/geocode.md).

Uses small synthetic GeoNames files in a temporary folder.

Usage:
    python tests/test_geocode.py
"""
import inspect
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geocode as gc  # noqa: E402
from geocode import build_gazetteer, geocode, name_key, normalize  # noqa: E402

BBOX = {"lon_min": 95.0, "lon_max": 119.5, "lat_min": -6.5, "lat_max": 6.0}
ROWS = [  # geonameid, name, asciiname, alternatenames, lat, lon, class, code, admin1
    (1, "Kelurahan Kalampangan", "Kelurahan Kalampangan", "", -2.28972, 114.01167, "A", "ADM4", "13"),
    (2, "Palangkaraya", "Palangkaraya", "Palangka Raya,Palangka-Raya", -2.20833, 113.91667, "P", "PPLA", "13"),
    (3, "Kota Palangka Raya", "Kota Palangka Raya", "", -1.76979, 113.73126, "A", "ADM2", "13"),
    (4, "Desa Tumbang Nusa", "Desa Tumbang Nusa", "", -1.04861, 114.26556, "A", "ADM4", "13"),
    (5, "Desa Tumbang Nusa", "Desa Tumbang Nusa", "", -2.33194, 114.17417, "A", "ADM4", "13"),
    (6, "Tumbang Nusa", "Tumbang Nusa", "", -2.33500, 114.17600, "P", "PPL", "13"),  # same place as 5
    (7, "Sungai Kalampangan", "Sungai Kalampangan", "", -2.30000, 114.02000, "H", "STM", "13"),  # river
    (8, "Desa Sukamaju", "Desa Sukamaju", "", -8.20000, 110.40000, "A", "ADM4", "30"),  # outside bbox + pad
    (9, "Jabiren", "Jabiren", "", -2.52880, 114.17720, "P", "PPL", "13"),
    (10, "Kecamatan Jabiren Raya", "Kecamatan Jabiren Raya", "", -2.44167, 114.22694, "A", "ADM3", "13"),
    (11, "Desa Sebangau Jaya", "Desa Sebangau Jaya", "", -2.99806, 113.58667, "A", "ADM4", "99"),  # unknown admin1
]


def files() -> tuple[Path, Path]:
    d = Path(tempfile.mkdtemp())
    lines = []
    for gid, name, ascii_, alt, lat, lon, fcl, code, a1 in ROWS:
        cols = [str(gid), name, ascii_, alt, str(lat), str(lon), fcl, code, "ID", "", a1, "", "", "",
                "0", "", "10", "Asia/Pontianak", "2024-01-01"]
        lines.append("\t".join(cols))
    (d / "ID.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "admin1.txt").write_text("ID.13\tKalimantan Tengah\tCentral Kalimantan\t1643985\n"
                                  "ID.30\tJawa Barat\tWest Java\t1642672\n", encoding="utf-8")
    return d / "ID.txt", d / "admin1.txt"


GAZ = build_gazetteer(*files(), BBOX, pad=1.0)


def test_normalize_and_key():
    assert normalize("Desa  Tumbang-Nusa") == "tumbang nusa"
    assert normalize("behind Kālampangan village") == "kalampangan"
    assert normalize("Near the big canal, Kota Palangka Raya!") == "big canal palangka raya"
    assert normalize("village of the") == ""
    assert name_key("Palangka Raya") == name_key("Palangkaraya") == "palangkaraya"


def test_gazetteer_shape():
    assert list(GAZ.columns) == ["geonameid", "name", "key", "lat", "lon", "kind", "province"]
    assert set(GAZ["geonameid"]) == {1, 2, 3, 4, 5, 6, 9, 10, 11}, sorted(set(GAZ["geonameid"]))
    assert not GAZ.duplicated(["geonameid", "key"]).any()
    assert set(GAZ.loc[GAZ["geonameid"] == 2, "key"]) == {"palangkaraya"}
    assert GAZ.loc[GAZ["geonameid"] == 1, "kind"].iat[0] == "village"
    assert GAZ.loc[GAZ["geonameid"] == 2, "kind"].iat[0] == "town"
    assert GAZ.loc[GAZ["geonameid"] == 10, "kind"].iat[0] == "district"
    assert GAZ.loc[GAZ["geonameid"] == 1, "province"].iat[0] == "Central Kalimantan"
    assert GAZ.loc[GAZ["geonameid"] == 11, "province"].iat[0] == ""
    assert GAZ["lat"].dtype.kind == "f" and GAZ["geonameid"].dtype.kind == "i"


def test_spoken_village_with_filler_words():
    out = geocode("behind Kalampangan village near the big canal", GAZ)
    assert out == [{"name": "Kelurahan Kalampangan", "lat": -2.28972, "lon": 114.01167, "kind": "village",
                    "province": "Central Kalimantan", "matched": "kalampangan"}], out


def test_spacing_variants_and_order():
    out = geocode("Palangka Raya", GAZ)
    assert [c["name"] for c in out] == ["Palangkaraya", "Kota Palangka Raya"], out
    assert [c["kind"] for c in out] == ["town", "regency"]


def test_ambiguous_name_returns_both_and_merges_duplicates():
    out = geocode("Tumbang Nusa", GAZ)
    assert len(out) == 2, out
    assert {round(c["lat"], 2) for c in out} == {-1.05, -2.33}
    assert all(c["kind"] == "village" for c in out)  # the PPL row 0.4 km from row 5 is merged away


def test_longest_window_wins():
    out = geocode("Jabiren Raya", GAZ)
    assert [c["name"] for c in out] == ["Kecamatan Jabiren Raya"] and out[0]["matched"] == "jabiren raya"
    assert [c["name"] for c in geocode("Jabiren", GAZ)] == ["Jabiren"]


def test_no_match_no_fuzzy():
    for text in [None, "", "village", "Baratas", "Kalampangn", "Sukamaju", "Sungai"]:
        assert geocode(text, GAZ) == [], text


def test_max_candidates():
    assert len(geocode("Tumbang Nusa", GAZ, max_candidates=1)) == 1


def test_module_rules():
    src = inspect.getsource(gc)
    assert "except Exception" not in src and "except:" not in src and "print(" not in src
    for word in ("difflib", "fuzz", "levenshtein", "get_close_matches"):
        assert word not in src.lower(), word
    assert "CC BY 4.0" in (gc.__doc__ or "")
    assert chr(0x2014) not in src  # no em dashes


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"pass  {name}")
        except Exception as e:  # report every failure, not just the first
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} of {len(tests)} failed")
    sys.exit(1 if failed else 0)
