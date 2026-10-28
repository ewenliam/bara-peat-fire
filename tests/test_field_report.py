"""Acceptance tests for src/field_report.py (written by Claude, see specs/field_report.md).

No API calls: a fake client stands in for MetaLLM.

Usage:
    python tests/test_field_report.py
"""
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import field_report  # noqa: E402
from field_report import build_messages, extract, parse_reply, response_format  # noqa: E402
from report_schema import FieldReport  # noqa: E402

TRANSCRIPT = ("This is Andi from the Kalampangan patrol. Thick smoke near the canal behind "
              "Kalampangan village, flames about two hectares. We can reach it by motorbike. "
              "No houses close. We need water.")
GOOD = {"fire_state": "active_flames", "place_text": "Kalampangan village", "lat": None,
        "lon": None, "area_ha": 2.0, "smoke": "thick", "access": "vehicle",
        "people_at_risk": False, "crew_on_site": None, "needs": ["water"],
        "quotes": ["Thick smoke near the canal", "flames about two hectares"]}


class FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = []
        self.run_cost = 0.0012345678
        self.run_calls = [{"tag": "report extract"}]

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content),
                                                        finish_reason="stop")])


def test_response_format():
    rf = response_format()
    assert rf == {"type": "json_schema", "json_schema": {
        "name": "FieldReport", "strict": True, "schema": FieldReport.model_json_schema()}}


def test_build_messages_layout():
    msgs = build_messages(TRANSCRIPT, caption="smoke behind the school", place_hints=["Kalampangan", "Sebangau"])
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == field_report.SYSTEM_PROMPT
    assert TRANSCRIPT not in msgs[0]["content"]
    user = msgs[1]["content"]
    assert isinstance(user, str)
    lines = user.splitlines()
    assert "<transcript>" in lines and "</transcript>" in lines
    assert lines.index("<transcript>") < lines.index("</transcript>")
    assert TRANSCRIPT in user
    assert "<caption>" in lines and "smoke behind the school" in user
    assert "Known place names: Kalampangan, Sebangau" in lines


def test_build_messages_optional_parts_absent():
    user = build_messages(TRANSCRIPT)[1]["content"]
    assert "<caption>" not in user and "Known place names" not in user
    assert "Known place names" not in build_messages(TRANSCRIPT, place_hints=[])[1]["content"]


def test_system_prompt_rules():
    p = field_report.SYSTEM_PROMPT.lower()
    for word in ["verbatim", "null", "unknown", "instructions", "coordinates",
                 "active_flames", "smouldering_peat", "smoke_only", "burn_scar", "no_fire", "unclear"]:
        assert word in p, word


def test_parse_reply_variants():
    r, err = parse_reply(json.dumps(GOOD))
    assert err is None and r.fire_state == "active_flames"
    r, err = parse_reply("```json\n" + json.dumps(GOOD) + "\n```\n")
    assert err is None and r is not None
    r, err = parse_reply("```\n" + json.dumps(GOOD) + "\n```")
    assert err is None and r is not None
    r, err = parse_reply("sorry, I cannot")
    assert r is None and err == "not JSON"
    r, err = parse_reply(json.dumps({**GOOD, "fire_state": "volcano"}))
    assert r is None and err.startswith("schema: ")


def test_extract_grounded():
    llm = FakeLLM(json.dumps(GOOD))
    out = extract(TRANSCRIPT, place_hints=["Kalampangan"], llm=llm)
    assert set(out) == {"report", "error", "grounding_problems", "missing", "grounded", "cost_usd", "llm_calls"}
    assert out["error"] is None and out["grounded"] is True and out["missing"] == []
    assert out["report"]["needs"] == ["water"] and out["cost_usd"] == 0.001235
    messages, kwargs = llm.calls[0]
    assert kwargs == {"max_tokens": field_report.MAX_TOKENS, "tag": "report extract",
                      "response_format": response_format()}
    assert messages == build_messages(TRANSCRIPT, None, ["Kalampangan"])


def test_extract_ungrounded_and_missing_location():
    bad = {**GOOD, "place_text": None, "quotes": ["the fire reached the school"]}
    out = extract(TRANSCRIPT, llm=FakeLLM(json.dumps(bad)))
    assert out["grounded"] is False and out["grounding_problems"]
    assert out["missing"] == ["location"]
    out = extract(TRANSCRIPT, has_shared_location=True, llm=FakeLLM(json.dumps(bad)))
    assert out["missing"] == []


def test_extract_bad_reply():
    out = extract(TRANSCRIPT, llm=FakeLLM("not json at all"))
    assert out["report"] is None and out["error"] == "not JSON" and out["grounded"] is False
    assert out["grounding_problems"] == [] and out["missing"] == []


def test_extract_empty_transcript_makes_no_call():
    llm = FakeLLM(json.dumps(GOOD))
    out = extract("   \n", llm=llm)
    assert llm.calls == [] and out["error"] == "empty transcript"
    assert out["cost_usd"] == 0.0 and out["llm_calls"] == [] and out["report"] is None


def test_checks_fail_closed():
    original = field_report.check_grounding

    def broken(*args, **kwargs):
        raise RuntimeError("check crashed")

    field_report.check_grounding = broken
    try:
        extract(TRANSCRIPT, llm=FakeLLM(json.dumps(GOOD)))
    except RuntimeError:
        return
    finally:
        field_report.check_grounding = original
    raise AssertionError("a crashing check must raise, never mark the report grounded")


def test_checks_called_once_with_spec_arguments():
    seen = []
    original_g, original_m = field_report.check_grounding, field_report.missing_fields
    field_report.check_grounding = lambda r, text: seen.append(("g", text)) or []
    field_report.missing_fields = lambda r, shared: seen.append(("m", shared)) or []
    try:
        extract(TRANSCRIPT, caption="smoke behind the school", has_shared_location=True,
                llm=FakeLLM(json.dumps(GOOD)))
    finally:
        field_report.check_grounding, field_report.missing_fields = original_g, original_m
    assert seen == [("g", TRANSCRIPT + "\n" + "smoke behind the school"), ("m", True)], seen


def test_caption_quote_is_grounded():
    report = {**GOOD, "quotes": ["smoke behind the school"]}
    out = extract(TRANSCRIPT, caption="smoke behind the school", llm=FakeLLM(json.dumps(report)))
    assert out["grounded"] is True, out["grounding_problems"]


def test_prompt_uses_photo_category_wording_and_null_rules():
    p = field_report.SYSTEM_PROMPT
    assert "peat burns below the surface" in p
    assert "source on the ground is not visible" in p
    assert "place_text" in p and "smoke" in p and "access" in p


def test_module_rules():
    src = inspect.getsource(field_report)
    assert "os.environ" not in src and "getenv" not in src and "open(" not in src
    assert "def check_grounding" not in src and "def missing_fields" not in src
    assert "except Exception" not in src and "except:" not in src
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
