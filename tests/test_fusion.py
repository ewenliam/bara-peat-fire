"""Tests for the Claude-written fusion rules in src/fusion.py.

Synthetic plan table and gazetteer; the solver is replaced by a fake. No API calls.

Usage:
    python tests/test_fusion.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fusion  # noqa: E402
from fusion import apply, decide, resolve_location, route_diff  # noqa: E402

TABLE = pd.DataFrame({"lat": [-2.30, -2.20, -1.00, -2.35], "lon": [114.00, 113.90, 110.00, 114.15],
                      "risk": [3.0, 8.0, 0.4, 0.2], "tier": ["P2", "P1", "P3", "P3"], "n_today": [5, 9, 1, 1]},
                     index=pd.Index(["C4", "C2", "C9", "C7"], name="id"))
GAZ = pd.DataFrame([
    (1, "Kelurahan Kalampangan", "kalampangan", -2.28972, 114.01167, "village", "Central Kalimantan"),
    (4, "Desa Tumbang Nusa", "tumbangnusa", -1.04861, 114.26556, "village", "Central Kalimantan"),
    (5, "Desa Tumbang Nusa", "tumbangnusa", -2.33194, 114.17417, "village", "Central Kalimantan"),
    (6, "Desa Sumber Jaya", "sumberjaya", -0.50000, 111.00000, "village", "West Kalimantan"),
    (7, "Desa Sumber Jaya", "sumberjaya", -3.50000, 104.00000, "village", "South Sumatra"),
    (8, "Jambi", "jambi", -5.45500, 119.99670, "place", "South Sulawesi"),
    (9, "Desa Hutan Baru", "hutanbaru", -3.00000, 112.50000, "village", "Central Kalimantan"),
    (10, "Palangkaraya", "palangkaraya", -2.20833, 113.91667, "town", "Central Kalimantan"),
    (11, "Pontianak", "pontianak", -0.03194, 109.32500, "town", "West Kalimantan"),
    (12, "Palembang", "palembang", -2.91673, 104.74580, "town", "South Sumatra"),
], columns=["geonameid", "name", "key", "lat", "lon", "kind", "province"])


def rep(state="smouldering_peat", place="Kalampangan village"):
    return {"report": {"fire_state": state, "place_text": place}}


def test_single_match_located():
    loc = resolve_location(rep(), TABLE, GAZ)
    assert loc["status"] == "located" and loc["place"] == "Kelurahan Kalampangan", loc


def test_ambiguous_resolved_by_nearby_fire():
    loc = resolve_location(rep(place="Tumbang Nusa"), TABLE, GAZ)
    assert loc["status"] == "located" and loc["lat"] == -2.33194, loc  # the other one is 140 km from any fire
    assert "within 15 km" in loc["why"]


def test_ambiguous_without_nearby_fire_asks():
    loc = resolve_location(rep(place="Sumber Jaya"), TABLE, GAZ)
    assert loc["status"] == "ask" and len(loc["options"]) == 2 and "Which one" in loc["question"], loc
    assert len(set(loc["options"])) == 2 and "Pontianak" in loc["options"][0] and "Palembang" in loc["options"][1], loc


def test_unknown_or_outside_study_area_asks():
    for place in ["Baratas", None, "Jambi"]:  # Jambi here is only in Sulawesi
        loc = resolve_location(rep(place=place), TABLE, GAZ)
        assert loc["status"] == "ask" and "nearest village" in loc["question"], (place, loc)


def located(lat, lon, place="X"):
    return {"status": "located", "lat": lat, "lon": lon, "place": place}


def test_decisions():
    near_c4 = located(-2.29, 114.01)
    assert decide(rep("smouldering_peat"), near_c4, TABLE)["kind"] == "confirmed"
    d = decide(rep("active_flames"), near_c4, TABLE)
    assert (d["cluster_id"], d["tier_before"], d["tier_after"]) == ("C4", "P2", "P1")
    assert decide(rep("smoke_only"), located(-1.0, 110.01), TABLE)["tier_after"] == "P2"  # C9 P3 -> P2
    assert decide(rep("burn_scar"), located(-2.2, 113.9), TABLE)["tier_after"] == "P1"  # C2 stays P1
    assert decide(rep("no_fire"), located(-2.2, 113.9), TABLE)["kind"] == "reported_clear"
    far = located(-3.0, 112.5)
    assert decide(rep("active_flames"), far, TABLE)["kind"] == "new_fire"
    assert decide(rep("smoke_only"), far, TABLE)["kind"] == "no_change"
    assert decide(rep("unclear"), near_c4, TABLE)["kind"] == "no_change"
    assert decide(rep(), {"status": "ask"}, TABLE)["kind"] == "no_change"


def test_apply_changes_copy_only():
    locs = [located(-2.29, 114.01), located(-2.2, 113.9), located(-3.0, 112.5)]
    ds = [decide(rep("smouldering_peat"), locs[0], TABLE), decide(rep("no_fire"), locs[1], TABLE),
          decide(rep("active_flames"), locs[2], TABLE)]
    t = apply(TABLE, ds, locs)
    assert t.at["C4", "tier"] == "P1" and t.at["C4", "risk"] >= fusion.P1_MIN
    assert t.at["C2", "tier"] == "P3" and t.at["C2", "risk"] < fusion.P2_MIN
    assert t.at["R3", "tier"] == "P1" and ds[2]["cluster_id"] == "R3"
    assert TABLE.at["C4", "tier"] == "P2" and "R3" not in TABLE.index  # original untouched


def test_route_diff():
    before = [{"base": "Palangkaraya", "crew": 1, "stops": [{"cluster_id": "C2"}]},
              {"base": "Jambi", "crew": 1, "stops": [{"cluster_id": "C5"}]}]
    after = [{"base": "Palangkaraya", "crew": 1, "stops": [{"cluster_id": "C4"}, {"cluster_id": "C2"}]},
             {"base": "Jambi", "crew": 1, "stops": [{"cluster_id": "C5"}]}]
    assert route_diff(before, after) == [{"base": "Palangkaraya", "crew": 1, "before": ["C2"],
                                          "after": ["C4", "C2"], "added": ["C4"], "dropped": []}]


def test_fuse_day_end_to_end_with_fake_solver():
    fusion.load_gazetteer = lambda: GAZ

    def fake_plan(table, bases, method):
        p12 = table[table["tier"].isin(["P1", "P2"])].sort_values("risk", ascending=False)
        return [{"base": "Palangkaraya", "crew": 1, "risk_covered": float(p12["risk"].sum()),
                 "stops": [{"cluster_id": c} for c in p12.index]}]

    fusion.plan = fake_plan
    fusion.crew_bases = lambda: None
    before = fake_plan(TABLE, None, "solver")
    row = {"id": "wamid.X", "received_at": "2026-10-30T10:00:00", "type": "audio",
           "transcript": "smoke rising from the peat behind Kalampangan village",
           "extraction": {"report": rep()["report"] | {"quotes": []}, "grounded": True}}
    out = fusion.fuse_day(TABLE, before, [row])
    r = out["reports"][0]
    assert r["decision"]["kind"] == "confirmed" and r["decision"]["tier_after"] == "P1"
    assert out["route_diff"] == []  # C4 was already on the route; only its tier and risk changed
    assert "C4" in r["reply"] and "P2 to P1" in r["reply"] and "Palangkaraya crew already goes there" in r["reply"], r["reply"]
    assert out["risk_covered_after"] > out["risk_covered_before"]
    clear = dict(row, id="wamid.Y", extraction={"report": rep("no_fire", "Kalampangan")["report"], "grounded": True})
    out = fusion.fuse_day(TABLE, before, [clear])
    assert out["route_diff"][0]["dropped"] == ["C4"], out["route_diff"]
    assert "no longer goes there" in out["reports"][0]["reply"], out["reports"][0]["reply"]


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
