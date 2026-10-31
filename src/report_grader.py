"""Deterministic grader for field reports: one predicted report against known facts.

Claude-written (PLAN.md, Muse Glimmer report reader). The same grader scores every model
(Muse Spark, Glimmer base, Glimmer tuned) so the comparison is fair, and it never asks a model
whether an answer is right. Quotes are not compared word for word (wording varies); they are
checked with check_grounding against the transcript instead.

Usage:
    from report_grader import grade, summarise
"""
import json

from geocode import normalize
from report_schema import FieldReport, check_grounding

FIELDS = ("fire_state", "place", "area_ha", "smoke", "access", "people_at_risk", "crew_on_site", "needs")


def _place_matches(pred: str | None, gold: str | None) -> bool:
    """Gold place said (or not); the prediction may wrap it in words like "behind ... village"."""
    if not gold:
        return not pred
    if not pred:
        return False
    want = normalize(gold).replace(" ", "")
    toks = normalize(pred).split()
    windows = {"".join(toks[i:j]) for i in range(len(toks)) for j in range(i + 1, len(toks) + 1)}
    return want in windows


def compare(pred: dict, gold: dict) -> dict[str, bool]:
    """Field by field. pred is a FieldReport dict; gold uses the same keys (quotes ignored)."""
    area_p, area_g = pred.get("area_ha"), gold.get("area_ha")
    return {
        "fire_state": pred.get("fire_state") == gold.get("fire_state"),
        "place": _place_matches(pred.get("place_text"), gold.get("place_text")),
        "area_ha": (area_p is None and area_g is None)
                   or (area_p is not None and area_g is not None and abs(area_p - area_g) < 0.01),
        "smoke": pred.get("smoke") == gold.get("smoke"),
        "access": pred.get("access") == gold.get("access"),
        "people_at_risk": pred.get("people_at_risk") == gold.get("people_at_risk"),
        "crew_on_site": pred.get("crew_on_site") == gold.get("crew_on_site"),
        "needs": set(pred.get("needs") or []) - {"none"} == set(gold.get("needs") or []) - {"none"},
    }


def grade(answer_text: str, gold: dict, transcript: str) -> dict:
    """Parse a model's answer, validate it against the schema, compare fields, check grounding."""
    text = (answer_text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        report = FieldReport.model_validate(json.loads(text))
    except ValueError:  # json.JSONDecodeError and pydantic.ValidationError are both ValueErrors
        return {"valid": False, "fields": {f: False for f in FIELDS}, "all_fields": False, "grounded": False}
    fields = compare(report.model_dump(), gold)
    return {"valid": True, "fields": fields, "all_fields": all(fields.values()),
            "grounded": not check_grounding(report, transcript)}


def summarise(grades: list[dict]) -> dict:
    n = len(grades)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "valid_json": round(sum(g["valid"] for g in grades) / n, 3),
        "all_fields": round(sum(g["all_fields"] for g in grades) / n, 3),
        "grounded": round(sum(g["grounded"] for g in grades) / n, 3),
        "per_field": {f: round(sum(g["fields"][f] for g in grades) / n, 3) for f in FIELDS},
    }
