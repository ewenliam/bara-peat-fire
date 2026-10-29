"""Tests for the Claude-written number check (src/agent.py grounded, src/chat.py check_answer).

No API calls and no data files: the tool results are written out by hand.

Usage:
    python tests/test_number_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent import grounded  # noqa: E402
from chat import check_answer  # noqa: E402

# what_if_dispatch(day_hours=8) on 2019-09-15, as the tool returned it (numbers only)
WHAT_IF = {
    "scenario": {"crews_per_base": 1, "day_hours": 8, "speed_kmh": 30, "hours_on_site": 2},
    "todays_plan": {"crews_used": 12, "clusters_visited": 20, "risk_covered": 562.8, "p1_reached": 13, "p1_total": 105},
    "scenario_plan": {"crews_used": 10, "clusters_visited": 14, "risk_covered": 476.8, "p1_reached": 10, "p1_total": 105},
    "change": {"risk_covered": -86.0, "risk_covered_pct": -15.3, "p1_reached": -3, "clusters_visited": -6},
    "top_p1_not_reached": [{"cluster_id": "C13", "risk": 33.4}, {"cluster_id": "C6", "risk": 21.0}],
}
# the answer that was wrongly marked CHECK FAILED on 2026-10-29
ANSWER = """If crews work 8 hours, assuming bases, 30 km per hour travel and 2 hours at each fire:

Risk covered is 476.8, change -86.0, change -15.3 percent.
Top priority reached is 10 of 105, change -3.
Fires visited is 14, change -6, using 10 crews.

You would miss C13 near Kualapesaguan, West Kalimantan and C6 near Mentangaik, Central Kalimantan."""
IDS = {"C6", "C13"}


def test_negative_changes_pass():
    assert check_answer(ANSWER, [WHAT_IF], IDS) == []


def test_magnitude_wording_passes():
    text = "Risk covered fell by 86.0, a drop of 15.3 percent; P1 reached fell by 3."
    assert check_answer(text, [WHAT_IF], IDS) == []


def test_invented_numbers_still_fail():
    errors = check_answer("Risk covered fell by 91.5, and 7 P1 clusters are lost.", [WHAT_IF], IDS)
    assert "number 91.5 is not in any tool output" in errors, errors
    assert "number 7 is not in any tool output" in errors, errors


def test_unknown_cluster_still_fails():
    assert "cluster C99 is not in any tool output" in check_answer("Send a crew to C99.", [WHAT_IF], IDS | {"C99"})


def test_grounded_signs_and_percent():
    assert grounded("86.0", [-86.0], False) and grounded("86", [-86.0], False)
    assert grounded("15.3", [-0.153], True)  # a share given as a percent
    assert not grounded("86.5", [-86.0], False)


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
