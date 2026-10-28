# Pre-registration: 2023 held-out test

Written Oct 28, 2026, before any 2023 samples, features or scores exist. The commit that adds this file is the timestamp. Everything below is fixed before the test runs; the test runs once.

## Why 2023 is the real test

2019 was used for early stopping, threshold choice and every design decision (rain lag, peat weight, tier cut-offs, crew settings). So the 2019 numbers (AP 0.308 vs 0.207) are validation results, not test results. 2023 has not been used for anything.

What has touched 2023 so far:

- Raw files were downloaded in bulk on Oct 27 (S-NPP `viirs-snpp_2023_Indonesia.csv`, CHIRPS 2023) and on Oct 28 (NOAA-20 `viirs-jpss1_2023_Indonesia.csv`). No 2023 samples, features, predictions or plots exist in `data/processed/` or `outputs/`.
- One known overlap: `labels.py` loads one year after the training years to label Dec 31, so the label for training rows dated 2022-12-31 uses detections on 2023-01-01. That is one day of labels and no 2023 features. The test starts on 2023-01-01, so no test row shares a label day with training.

## Frozen artifacts

| Item | Value |
|---|---|
| Next-day spread model | `models/lgbm_spread_y_2019_2019.txt`, sha256 `5d9236efcc2472897b985434efb7a43bc57477e526d4899871663adae9e7c512` |
| New-fire (ignition) model | `models/lgbm_ignition_y_ign_2019.txt`, sha256 `029ad24793e1c78a4790b415dddf18add9714839fb28c0fce2a346493b5b7a03` |
| Training years | Spread: 2012-2018, 2020-2022 (negatives sampled at 0.1, weighted). Ignition: 2013-2018, 2020-2022 (0.05) |
| Early stopping | 2019 |
| Features | Spread: `LightGBM + peat + rain` (hotspot history, GFW peat share, CHIRPS 0.25 deg rain with a 2-day lag). No Drought Code |
| Decision thresholds (from 2019, not re-fitted) | Spread model 0.17093; Yesterday's fires (3x3) 6; Yesterday's fires (cell) 1; Last 7 days (5x5) 57; Logistic regression 0.16435; Ignition model 0.07972 |
| Triage | risk = expected_new_cells x (1 + 1.0 x peat_share); P1 if risk >= 5, P2 if risk >= 1 |
| Dispatch | 14 crew bases (provincial capitals), 1 crew each, 30 km/h, detour 1.4, 2 h on site, 10 h day, 10 candidates per crew; methods solver, greedy, nearest, biggest_today, plus oracle |
| Labels | S-NPP VIIRS, `type == 0`, confidence not low, Sumatra and Kalimantan box (95.0 to 119.5 E, 6.5 S to 6.0 N) |

## Hypotheses

**H1 (forecast).** On all 2023 candidate cells (no negative sampling), the spread model's average precision is higher than the best persistence baseline from 2019, Yesterday's fires (3x3). Uncertainty: 95% interval from 1,000 bootstrap resamples of days (seed 0).

**H2 (dispatch).** In a Jul 1 to Oct 31, 2023 replay with the settings above, the solver reaches more next-day fire cells than biggest_today (biggest fire first).

## Secondary results (reported, no hypothesis)

- AP on new cells (no detection in the last 7 days), F1, IoU and predicted area / burned area at the 2019 thresholds, P@20 and P@50, for every model and baseline.
- Triage: share of next-day new fire cells in P1 and P2, against persistence triage with the same tier sizes.
- Dispatch: visits, cells reached, peat-weighted cells reached, share of oracle, for every method.
- Ignition model AP against its baselines (climatology, nearby fires in the last 7 days).
- Sensor check: H1 metrics recomputed with NOAA-20 labels, to show the model holds when the live path switches from S-NPP to NOAA-20/21.

## Rules

1. Before the run, the scripts may only be changed to accept 2023 as input (file names, dates). No change to features, models, thresholds or settings.
2. The test runs once. If a bug forces a rerun, both results and the reason go in `outputs/test_2023_log.md`.
3. Results are reported in the README whatever they show, including a loss on H1 or H2.
4. Anything not listed here (Drought Code models, new features, other seasons) is labelled exploratory.
