# Build plan (Oct 28 - Nov 4)

Judged equally on Impact, Use of Meta AI tooling, Technical depth and execution, Demo quality.
Entry gate: the project must meaningfully use the Meta Model API or Meta open weights.
Submission due Nov 4, 9:00 PM ET (Nov 5, 03:00 in Warsaw). The repo must be public.
Research behind this plan: `C:\Users\ewenl\reports\Bara global impact upgrade plan.md`.

## The locked direction (Oct 28)

**Pitch:** Bara plans fire crews from the satellite at dawn, and re-plans from the ground by noon when anyone on site sends a voice note or photo.

1. **Proof at scale first.** A pre-registered 2023 held-out test, a 2023 dispatch replay, a live NOAA-20/21 run, and a season-wide grounding pass rate. Never cut.
2. **WhatsApp field loop is the headline.** Voice note or photo in, structured report out, hotspot state changes, the solver re-plans, the crew phone gets the new route. Meta models are on the decision path: remove them and a crew goes somewhere different.
3. **English first.** Demo voice notes are in English. Indonesian and regional languages are a later step.
4. **API models only in the core.** Open weights (Omnilingual ASR, Muse Glimmer) are a stretch goal. Glimmer's smallest build (~17 GB) does not fit this laptop (8 GB VRAM, 15.3 GB RAM).
5. **Dropped:** DINOv3 (gated access not available).

## Hackathon rules that shape the build (read Oct 28)

- New work only, Oct 26 to Nov 4. Every project file was created on Oct 27 or later, so the whole app counts.
- Judges must be able to tell what the team built, or the project is disqualified. The README gets a "built during the event" section with dates, plus a "who built what" table (you, Muse Spark modules, Claude prompts and review).
- No code, data or assets without the rights to use them. The README gets a data and licence table (FIRMS, CHIRPS, GFW peat, CEMS Drought Code, basemap tiles) with each licence checked, not assumed.
- Avoid list includes simple image analyzers: photo classification only appears inside the field loop, never as a feature on its own.
- Theme: Build for Your World. Connection (for the video and README): "I'm Indonesian, living in Poland. My country's peatlands have been burning for months, and watching from far away made me sad. Bara is how I can help from here, with what I know how to build."
- Indie Developer track (solo). Public repo, 3-minute video, submit at https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit
- The rules do not restrict development tools.

## Meta models and their jobs

| Job | Model | Tier |
|---|---|---|
| Voice note to text | Muse Voice Transcribe ($0.18/hr) | Standard |
| Transcript plus photo to report JSON | Muse Spark 1.3 | Standard only: reporter data must not become training data |
| Briefings, what-if chat, map self-check | Muse Spark 1.3 | Standard |
| Water near crew stops | SAM 3.1 | Standard |
| Writing code modules (Claude supervises) | Muse Spark 1.3 Standard (Contributor tier not confirmed; the first module cost $0.05, so the saving is negligible) | Never put `.env` values in a prompt. Prompts and replies are kept in `logs/builds/` |
| Hard one-off tasks only | Muse Wonder | After Spark fails twice on the same task |

## Rules for every day

- Meta API budget is $150 total. Spend logged so far: $0.77 on Oct 28 (`python src/llm.py`). Split: builder up to $50, app runs and evals up to $50, $50 reserve. Raise the code cap (now $20) only when needed.
- The LLM never decides a route. Meta models perceive and write; the forecast, fusion rules, solver and number check stay deterministic and hand-written.
- Builder loop: Claude writes the spec and acceptance tests, Muse Spark writes one module, Claude and you read the diff, tests pass, `git diff --check` is clean, no secrets. You must be able to explain every line.
- Never touch 2023 data before the pre-registration file is committed.
- Commit or push only when you say so.
- If a day slips, cut in this order: second-region retrain, SAM canal mapping, video reports, Omnilingual ASR, Glimmer offline mode. Never cut the 2023 test, the live run, or the grounding rate.

## Days

| Date | Build | Done when |
|---|---|---|
| **Wed Oct 28** | (you, done) Read the rules. (you, done) Theme: Build for Your World. (you) Create the WhatsApp Cloud API test number and add your phone as a recipient. Write and commit the 2023 pre-registration (metrics, baselines, threshold chosen on 2019). Queue 2023 FIRMS (S-NPP and NOAA-20) and CHIRPS. Expose the webhook through a public HTTPS tunnel and pass the GET challenge. (done) Webhook routes in `src/whatsapp_routes.py`, signature check built by Muse Spark (`src/whatsapp_webhook.py`). (you) cloudflared tunnel and Meta Configuration. | Prereg committed; downloads queued; webhook verified |
| **Thu Oct 29** | Intake (Muse Spark builder tasks): media download (spec after a real voice note and photo payload are in the inbox), ffmpeg OGG to WAV (Voice Transcribe accepts only mono 16-bit PCM WAV at 16 or 24 kHz; install ffmpeg first), Voice Transcribe (`muse-voice-transcribe-1.0`, place names as `keywords`), Muse Spark extraction (spec and tests ready: `specs/field_report.md`; schema and grounding checks are Claude-owned in `src/report_schema.py`). | An English voice note from your phone becomes a validated JSON row |
| **Fri Oct 30** | Fusion and re-plan: match report to hotspot and cell, state machine (unverified, confirmed, smouldering, dismissed), tier change, solver rerun, route diff, reply to the crew number, map update in the UI, follow-up question when location is missing. | Phone to map to crew phone works twice in a row |
| **Sat Oct 31** | Proof: run the 2023 test once; 2023 dispatch replay; season grounding batch. Drought Code: decided Oct 28, not in the headline model. All years arrived, but the pre-registration froze the 2023 models without it; adding it would mean the test no longer checks the committed model. At most one exploratory ablation after the test, labelled as such. | Results table frozen |
| **Sun Nov 1** | S-NPP delivery ends 13:00 UTC. Live daily run on NOAA-20/21 with timestamps (needs `FIRMS_MAP_KEY`). Record 20 to 50 scripted English voice notes; score transcription and extraction. | Live plan renders; field-loop eval table |
| **Mon Nov 2** | README: results, portability table (data swaps per region), users, impact chain, limitations, "who built what" table, how to rebuild `data/`. Feature freeze at midnight. | README complete |
| **Tue Nov 3** | Script and record the 3-minute video plus a 60-second backup capture; rehearse "what breaks first". | Final video under 3:00 |
| **Wed Nov 4** | Buffer for blocking bugs only. Make the repo public. Submit before noon Warsaw time. | Submitted |

Stretch, only if ahead by Nov 1: Omnilingual ASR side by side with Voice Transcribe; one Indonesian voice note; Glimmer offline mode on a rented GPU.

## Demo storyline (3:00)

| Time | On screen | Criterion |
|---|---|---|
| 0:00 to 0:15 | Tomorrow's forecast animating over Kalimantan; one-line pitch | Demo |
| 0:15 to 0:35 | Why: haze health cost, peat fire hides from satellites, few crews | Impact |
| 0:35 to 1:15 | Live morning run: NOAA-20/21 timestamp, P1/P2/P3, routes, briefing with green number check | Technical depth, Meta tooling |
| 1:15 to 2:10 | Phone sends a voice note and photo; transcript, JSON, hotspot flips, route changes, crew phone buzzes; a second report dismisses a hotspot and frees a crew | Meta tooling, Demo |
| 2:10 to 2:40 | Evidence card: 2023 held-out vs persistence, dispatch replay, grounding rate, extraction accuracy, cost per day | Technical depth |
| 2:40 to 2:55 | Same pipeline elsewhere: Indonesia, Brazil, South Africa, retrained per region | Impact |
| 2:55 to 3:00 | One limitation: reaching a fire is not putting it out | Credibility |

## Done before Oct 28

- Data (FIRMS VIIRS 2012-2025), Detect, labels and features (CHIRPS rain, GFW peat share), LightGBM forecast: AP 0.308 vs 0.207 for the best persistence baseline on 2019.
- Triage tiers and exact dispatch solver; 2019 Jul-Oct replay: 13,460 vs 11,632 next-day fire cells reached (+15.7%) on fewer visits.
- Muse Spark agent with tool calls, grounding checks, cost ledger, spend cap and cache; SAM 3.1 water masks; what-if chat, season replay, field photo reports, map self-check.
- FastAPI + MapLibre/deck.gl web app, Bara logo, repo pushed to github.com/ewenliam/bara-peat-fire.
- Drought Code (CEMS): 2011-2025 plus 2026 interim on disk Oct 28. Not used in the frozen models (see Oct 31).

## Open items

- [[TODO: verify]] VIIRS `type == 0` means "presumed vegetation fire".
- DBSCAN chains very large fires into one cluster (Sept 2019: one cluster has 10,412 detections). Tune `EPS_KM` / `KM_PER_DAY` or add a max-duration rule.
- Live rain feed (checked Oct 28). The frozen model uses CHIRPS 0.25 deg with a 2-day lag; final CHIRPS on disk ends 2026-09-30. CHIRPS preliminary (`products/CHIRPS-2.0/prelim/global_daily/netcdf/p25/`, monthly files) had Oct 1 to 25 on Oct 28 and updates in pentads, so on Nov 1 it likely still ends Oct 25; CHIRP also ends Oct 25. IMERG Early daily V07 (`GPM_3IMERGDE.07`) had Oct 27 on Oct 28 but needs a free NASA Earthdata login. Plan: CHIRPS prelim, gap days from IMERG Early averaged to 0.25 deg, and disclose that live rain mixes products the model was not trained on (training used CHIRPS final).
- The chat number check cannot catch a number taken from the wrong scenario. Consider tagging each what-if result with a scenario id.
- Map self-check gave one false alarm on its first run (sea crossing on Bangka). Rerun once.
- Demo polish: overlapping stop labels in dense areas.
- SAM 3.1 river search tested on 7 chips only, judged by eye.
- Crew bases are provincial capitals and speeds are assumptions. [[TODO: need source for Manggala Agni Daops locations and crew travel times]]
- The two smoke tests before the spend ledger existed are not in `logs/meta_usage.jsonl`.
