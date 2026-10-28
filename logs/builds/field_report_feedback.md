Review by Claude. The tests passed but the review found problems; new tests cover them:
1. Fail-open trust check: when check_grounding raised, extract set grounding_problems to [] and grounded to True. A crashing check must make extract raise. Call each check exactly once, with the arguments in the spec, never inside try/except.
2. Remove the fallback calls with guessed signatures (check_grounding(report, transcript, caption), check_grounding(report), missing_fields keyword retry).
3. Grounding text is the transcript plus a newline and the caption when given.
4. SYSTEM_PROMPT: null (not "unknown") for place_text, lat, lon, area_ha, people_at_risk, crew_on_site when not said; "unknown" only for smoke and access. Use the fire_state wording from src/reports.py PROMPT (for example 'peat burns below the surface', 'source on the ground is not visible').
5. No broad except Exception anywhere; parse_reply can distinguish not JSON with json.loads and ValueError, then validate. Keep the code short and plain.

reports.py categories for reference:
Categories:
- active_flames: open flames are visible
- smouldering_peat: smoke rising from the ground or peat, no open flame (peat burns below the surface)
- smoke_only: smoke or haze but its source on the ground is not visible
- burn_scar: burned ground or vegetation with no visible smoke or flame
- no_fire: no sign of fire, smoke or burning
- unclear: the photo is too dark, blurry or unrelated to judge

Test output:
```
pass  test_build_messages_layout
pass  test_build_messages_optional_parts_absent
FAIL  test_caption_quote_is_grounded: AssertionError: ["quote not in transcript: 'smoke behind the school'"]
FAIL  test_checks_called_once_with_spec_arguments: AssertionError: [('g', 'This is Andi from the Kalampangan patrol. Thick smoke near the canal behind Kalampangan village, flames about two hectares. We can reach it by motorbike. No houses close. We need water.'), ('m', True)]
FAIL  test_checks_fail_closed: AssertionError: a crashing check must raise, never mark the report grounded
pass  test_extract_bad_reply
pass  test_extract_empty_transcript_makes_no_call
pass  test_extract_grounded
pass  test_extract_ungrounded_and_missing_location
FAIL  test_module_rules: AssertionError:
pass  test_parse_reply_variants
FAIL  test_prompt_uses_photo_category_wording_and_null_rules: AssertionError:
pass  test_response_format
pass  test_system_prompt_rules
5 of 14 failed
```
