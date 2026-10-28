"""Tests for the Claude-written report contract and checks in src/report_schema.py.

Usage:
    python tests/test_report_schema.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from report_schema import FieldReport, check_grounding, missing_fields  # noqa: E402

TRANSCRIPT = ("This is Andi from the Kalampangan patrol. Thick smoke near the canal behind "
              "Kalampangan village, flames about two hectares. We can reach it by motorbike. "
              "No houses close. We need water.")


def report(**overrides) -> FieldReport:
    base = dict(fire_state="active_flames", place_text="Kalampangan village", lat=None, lon=None,
                area_ha=2.0, smoke="thick", access="vehicle", people_at_risk=False,
                crew_on_site=True, needs=["water"],
                quotes=["Thick smoke near the canal", "flames about two hectares"])
    base.update(overrides)
    return FieldReport(**base)


def test_strict_schema_shape():
    s = FieldReport.model_json_schema()
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert set(s["required"]) == set(s["properties"])
    assert "$defs" not in s


def test_extra_keys_rejected():
    try:
        FieldReport(**report().model_dump(), extra_key=1)
    except ValueError:
        return
    raise AssertionError("extra keys must be rejected")


def test_grounded_report_passes():
    assert check_grounding(report(), TRANSCRIPT) == []


def test_quotes_match_case_and_whitespace_insensitively():
    r = report(quotes=["THICK  smoke\nnear the canal"])
    assert check_grounding(r, TRANSCRIPT) == []


def test_invented_quote_and_place_flagged():
    problems = check_grounding(report(quotes=["fire spreading to the school"],
                                      place_text="Palangka Raya"), TRANSCRIPT)
    assert any("quote not in transcript" in p for p in problems)
    assert any("place not spoken" in p for p in problems)


def test_no_quotes_flagged():
    assert "no supporting quote" in check_grounding(report(quotes=[]), TRANSCRIPT)


def test_coordinates_checked():
    assert any("outside" in p for p in check_grounding(report(lat=52.2, lon=21.0), TRANSCRIPT))
    assert any("only one" in p for p in check_grounding(report(lat=-2.2, lon=None), TRANSCRIPT))
    assert check_grounding(report(lat=-2.21, lon=113.82), TRANSCRIPT) == []


def test_area_must_be_positive():
    assert any("area" in p for p in check_grounding(report(area_ha=0.0), TRANSCRIPT))


def test_missing_fields():
    assert missing_fields(report()) == []
    assert missing_fields(report(place_text=None)) == ["location"]
    assert missing_fields(report(place_text=None), has_shared_location=True) == []
    assert missing_fields(report(place_text=None, fire_state="unclear")) == ["location", "fire_state"]


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
