# Build plan (Oct 27 - Nov 4)

Judged equally on Impact, Use of Meta AI tooling, Technical depth and execution, Demo quality.
Entry gate: the project must meaningfully use the Meta Model API or Meta open weights.
Submission due Nov 4, 9:00 PM ET (Nov 5, 03:00 in Warsaw). The repo must be public.

## Rules for every day

- Meta API budget is $150 total: log cost per call, cap spending, cache responses, keep numbers in code.

- Build in order: Detect, Forecast, Triage, Dispatch. Each step must run end to end before polishing.
- Never touch 2023 data until Nov 2 (held-out test).
- If a day slips: cut DINOv3 first, then SAM masks, then the live path. Never cut the 2023 test.

## Days

- [x] **Oct 27** Data (FIRMS VIIRS 2012-2025), repo scaffold, Detect v0 (`src/detect.py`)
- [x] **Oct 27** Meta API smoke test passes (`muse-spark-1.3`; needs a large `max_tokens` because reasoning counts toward it)
- [ ] **Oct 27** (you) Create a public GitHub repo `bara` and push
- [x] **Oct 27** (early) Labels and features: candidate cells, next-day labels, CHIRPS rain, GFW peat share. Drought code (CEMS) not added: needs an EWDS account
- [x] **Oct 27** (early) Baselines vs LightGBM on 2019: AP 0.308 vs 0.207 for the best persistence baseline
- [x] **Oct 30** (early, Oct 27) Muse Spark agent v0: tool calls for detect + forecast, JSON output, Bahasa briefing, schema and grounding checks, cost ledger, spend cap, cache (`src/agent.py`, `src/tools.py`, `src/llm.py`). First briefing: $0.0252
- [x] **Oct 31** (early, Oct 27) Triage tiers (P1/P2/P3) and Dispatch order (greedy and exact solver), replay on Jul-Oct 2019: solver reaches 15.8% more next-day fire cells than "biggest fire first". Agent now calls all five tools ($0.0364 per briefing)
- [x] **Nov 1** (early, Oct 27) SAM 3.1 water masks near crew stops (`src/water.py`): rivers yes, canals no (too narrow at 10 m). Agent calls it as a sixth tool
- [x] **Nov 1** (early, Oct 27) Renamed to Bara; English throughout. Web app replaces the Streamlit prototype: FastAPI (`src/server.py`) + MapLibre/deck.gl front end (`web/`)
- [x] **Oct 27** Ask Bara what-if chat (`src/chat.py`), season replay player (`src/replay_frames.py`), field photo reports (`src/reports.py`), map self-check (`src/selfcheck.py`); island rule in the solver
- [ ] **Nov 2** S-NPP ends today. Run the 2023 test once; freeze the results table
- [ ] **Nov 3** Live path on NOAA-20/21 NRT (needs `FIRMS_MAP_KEY`); check briefings against JSON
- [ ] **Nov 4** README, 3-minute demo video, submit before noon Warsaw time

## Open items

- [[TODO: verify]] VIIRS `type == 0` means "presumed vegetation fire".
- DBSCAN chains very large fires into one cluster (Sept 2019: one cluster has 10,412 detections). Tune `EPS_KM` / `KM_PER_DAY` or add a max-duration rule.
- Rain features are lagged 2 days. [[TODO: verify CHIRPS preliminary latency]]
- New-fire model added for fires outside the candidate set (`src/ignition.py`, AP 0.057 vs base rate 0.0055).
- Hydrology tested (Oct 27): small gain, larger at 7 days (+0.008 AP) and in quiet areas (+15-17%). 0.05 deg rain adds nothing; zero lag helps new fires only (0.057 to 0.063). Next option: Copernicus Drought Code (user needs an EWDS account).
- Drought Code (CEMS, EWDS): EWDS allows about 2 queued requests per user and 3,720 cost units per request, so only the Drought Code is downloaded (10 years per request). 2019 done; 2011 and 2012-2022 queued; 2023-2025 and 2026 follow via the background loop (`logs/drought_download.log`). Features and ablation wait for it. The 2023 test needs 2023 Drought Code, otherwise drop it from the model.
- Province names are grounded (GeoNames lookup in the tools, checked in the text). Numbers written as words are not checked; the prompts ask for digits.
- The chat number check cannot catch a number taken from the wrong scenario. Consider tagging each what-if result with a scenario id and checking the answer cites one scenario.
- Map self-check gave one false alarm on its first run (sea crossing on Bangka). Now advisory, with islands in the plan JSON; rerun once to see if the false alarm is gone.
- Demo polish: overlapping stop labels in dense areas (self-check also flagged this).
- Field reports: 4-photo smoke test only. If time allows, collect more labelled photos for a small accuracy table.
- Drought Code cut-off: Oct 31. If not all years have arrived, drop it and say so in the README.
- SAM 3.1 river search: tested on 7 chips only, judged by eye. A proper check would compare masks with a reference water layer (e.g. OpenStreetMap waterways; the Overpass API refused and then timed out on Oct 27).
- Crew bases are provincial capitals and speeds/times are assumptions. [[TODO: need source for Manggala Agni Daops locations and crew travel times]]
- Agent spend so far is in `logs/meta_usage.jsonl` (run `python src/llm.py`). The two smoke tests before the ledger existed are not in it.
- Live demo rain: final CHIRPS ends Sep 30, 2026. Need a low-latency feed (CHIRPS preliminary or IMERG Early). [[TODO: verify latencies]]
