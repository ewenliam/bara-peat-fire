# Evaluation

Every result Bara claims, where it comes from, and how to check it.

```bash
python eval/summary.py
```

prints all the headline numbers straight from the committed result files in `outputs/`. It needs
no data download and no API key, and CI runs it on every push.

## What is evaluated

| Evaluation | Question | Status | Result file | Produced by |
|---|---|---|---|---|
| 2023 held-out test | Does the forecast beat "yesterday's fires" (H1), and do crews following the solver reach more next-day fire (H2)? | Pre-registered in [`PREREGISTRATION_2023.md`](../PREREGISTRATION_2023.md) before 2023 data was touched; run once | `outputs/test_2023_results.json`, log in `outputs/test_2023_log.md` | `src/test_2023.py` |
| 2026 live scorecard | Is the frozen forecast still ahead on this year's data, with a different satellite and near-real-time rain? | Exploratory, not pre-registered | `outputs/live_scorecard.json` | `src/scorecard.py` |
| Muse Glimmer report reader | Can a small open-weight Meta model learn the transcript-to-report job? | Experiment on synthetic rows | `outputs/glimmer/runs/*/summary_ours.json` | `src/glimmer_grade.py` with the deterministic grader `src/report_grader.py` |
| Unit and pipeline tests | Do the field-report pipeline, fusion rules, number checks and WhatsApp handling behave as specified? | 12 test files, no API calls | CI log | `tests/test_*.py` |

## Metrics

- **Average precision (AP)** over all candidate 0.01 degree cells, for "fire in this cell
  tomorrow". The baseline ranks cells by fire in and around them yesterday (3x3). 95% intervals come from day-level bootstrap resamples (1,000 for 2023, 200 for 2026).
- **Dispatch (H2)**: next-day fire cells reached by the crews' planned stops over Jul 1 to Oct 31,
  2023, against sending each crew to the biggest fire of the day; plus the number of visits.
- **Top 200**: of the 200 spots each method rated highest, how many burned the next day (the
  "CHECKED" line in the app).
- **Glimmer**: share of valid JSON reports, and share with every field correct, on 100 held-out
  synthetic transcripts whose places are not in training.

## Reproduce

The processed tables are not in the repository (see the README, "Run it"). With them in `data/`:

```bash
python src/test_2023.py --check-2019 --boot 20   # code check on 2019; writes nothing
python src/scorecard.py                          # rescore 2026 after src/live.py
python src/glimmer_grade.py <run-id> --meta data/glimmer/split/eval_e2_meta.jsonl
```

`src/test_2023.py` without flags is the one pre-registered run; running it again is only allowed
with `--rerun "reason"`, and both runs stay in the log.

## Limits

- Reaching a fire cell is checking it, not putting it out: no result here is hectares, CO2 or lives.
- The 2026 scorecard is a check, not a test: it was not pre-registered.
- On the synthetic Glimmer set, Muse Spark is correct by construction, so it compares Glimmer base
  against tuned only. The real voice notes (`docs/recording_scripts.md`) are not scored yet.
