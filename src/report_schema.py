"""Field report contract: the JSON schema Muse Spark fills from a voice note, plus our own checks.

Claude-written and deterministic (PLAN.md): the model extracts, this module decides whether
the extraction can be trusted. Every claim must be backed by a verbatim quote from the
transcript; quotes that are not in the transcript, impossible coordinates and missing
essentials are reported, never silently fixed. fire_state uses the same categories as the
photo classifier in reports.py so the two can be fused.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from firms import REGION_BBOX

FireState = Literal["active_flames", "smouldering_peat", "smoke_only", "burn_scar", "no_fire", "unclear"]
Need = Literal["crew", "water", "heavy_equipment", "medical", "none"]


class FieldReport(BaseModel):
    """Every field is required (strict structured output); unknown values are null or 'unknown'."""
    model_config = ConfigDict(extra="forbid")

    fire_state: FireState
    place_text: str | None = Field(description="Place names exactly as spoken, else null")
    lat: float | None = Field(description="Only if a latitude is spoken as a number, else null")
    lon: float | None = Field(description="Only if a longitude is spoken as a number, else null")
    area_ha: float | None = Field(description="Only if an area in hectares is spoken, else null")
    smoke: Literal["none", "light", "thick", "unknown"]
    access: Literal["vehicle", "boat", "foot", "unknown"]
    people_at_risk: bool | None
    crew_on_site: bool | None
    needs: list[Need]
    quotes: list[str] = Field(description="1 to 4 verbatim snippets from the transcript backing the fields")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def check_grounding(report: FieldReport, transcript: str) -> list[str]:
    """Return problems; an empty list means the report passes."""
    problems = []
    said = _norm(transcript)
    if not report.quotes:
        problems.append("no supporting quote")
    for q in report.quotes:
        if not _norm(q) or _norm(q) not in said:
            problems.append(f"quote not in transcript: {q!r}")
    if (report.lat is None) != (report.lon is None):
        problems.append("only one of lat/lon given")
    if report.lat is not None and report.lon is not None:
        b = REGION_BBOX
        if not (b["lat_min"] <= report.lat <= b["lat_max"] and b["lon_min"] <= report.lon <= b["lon_max"]):
            problems.append(f"coordinates outside Sumatra/Kalimantan: {report.lat}, {report.lon}")
    if report.area_ha is not None and report.area_ha <= 0:
        problems.append(f"area must be positive: {report.area_ha}")
    if report.place_text and _norm(report.place_text) not in said:
        problems.append(f"place not spoken as written: {report.place_text!r}")
    return problems


def missing_fields(report: FieldReport, has_shared_location: bool = False) -> list[str]:
    """What to ask the reporter next. A WhatsApp location pin counts as a location."""
    missing = []
    if not (has_shared_location or report.place_text or report.lat is not None):
        missing.append("location")
    if report.fire_state == "unclear":
        missing.append("fire_state")
    return missing
