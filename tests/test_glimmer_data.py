"""Tests for the Claude-written grader (src/report_grader.py) and data sampler (src/glimmer_data.py).

No API calls and no data files.

Usage:
    python tests/test_glimmer_data.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from glimmer_data import EVAL_PLACES, STATES, sample  # noqa: E402
from report_grader import FIELDS, compare, grade, summarise  # noqa: E402

TRANSCRIPT = "this is the sungai lais patrol. smoke rising from the peat near sungai lais village, about two hectares. we need water."
GOLD = {"fire_state": "smouldering_peat", "place_text": "Sungai Lais", "lat": None, "lon": None, "area_ha": 2.0,
        "smoke": "unknown", "access": "unknown", "people_at_risk": None, "crew_on_site": None, "needs": ["water"]}
PRED = {**GOLD, "place_text": "near Sungai Lais village", "quotes": ["smoke rising from the peat", "we need water"]}


def test_compare_exact_and_wrapped_place():
    assert all(compare(PRED, GOLD).values())
    assert not compare({**PRED, "place_text": "Sungai Lalang"}, GOLD)["place"]
    assert not compare({**PRED, "place_text": None}, GOLD)["place"]
    assert compare({**PRED, "place_text": None}, {**GOLD, "place_text": None})["place"]


def test_compare_fields():
    c = compare({**PRED, "area_ha": None, "needs": ["water", "crew"], "crew_on_site": False}, GOLD)
    assert (c["area_ha"], c["needs"], c["crew_on_site"]) == (False, False, False)
    assert compare({**PRED, "needs": ["water", "none"]}, GOLD)["needs"]
    assert compare({**PRED, "area_ha": 2.004}, GOLD)["area_ha"]


def test_grade_valid_grounded_and_invalid():
    g = grade(json.dumps(PRED), GOLD, TRANSCRIPT)
    assert g["valid"] and g["all_fields"] and g["grounded"], g
    g = grade(json.dumps({**PRED, "quotes": ["the school is burning"]}), GOLD, TRANSCRIPT)
    assert g["all_fields"] and not g["grounded"]
    for bad in ["", "not json", json.dumps({"fire_state": "volcano"}), "```json\n{}\n```"]:
        g = grade(bad, GOLD, TRANSCRIPT)
        assert not g["valid"] and not g["all_fields"] and set(g["fields"]) == set(FIELDS), bad
    assert grade("```json\n" + json.dumps(PRED) + "\n```", GOLD, TRANSCRIPT)["valid"]


def test_summarise():
    s = summarise([grade(json.dumps(PRED), GOLD, TRANSCRIPT), grade("x", GOLD, TRANSCRIPT)])
    assert s["n"] == 2 and s["valid_json"] == 0.5 and s["all_fields"] == 0.5 and s["per_field"]["fire_state"] == 0.5


def test_sampler_deterministic_and_consistent():
    places = ["Sungai Lais", "Talio", "Pangkoh"]
    a, b = sample(7, 1, places), sample(7, 1, places)
    assert a == b and sample(8, 1, places) != a
    for i in range(300):
        s = sample(i, 1, places)
        g = s["gold"]
        assert g["smoke"] in STATES[g["fire_state"]][2]
        assert g["place_text"] in places + [None]
        assert g["lat"] is None and g["lon"] is None
        if g["fire_state"] == "no_fire":
            assert g["needs"] == []
        if g["fire_state"] == "unclear":
            assert g["area_ha"] is None
        if g["place_text"]:
            assert any(g["place_text"] in f for f in s["facts"])
        if g["area_ha"] is None:
            assert not any(f.startswith("Size") for f in s["facts"])


def test_eval_places_excluded_set():
    assert {"kalampangan", "bukittunggal", "palangkaraya"} <= EVAL_PLACES


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
