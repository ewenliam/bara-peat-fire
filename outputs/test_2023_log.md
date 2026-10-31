## Run started 2026-10-31T01:26:53+00:00 (first run)

Commit ead814738b36ee7f2309dfb7bb894811c55b0b43, uncommitted changes in src: True

**H1** spread model AP 0.162 vs Yesterday's fires (3x3) 0.099; difference 95% CI 0.054 to 0.071; supported: True

**H2** solver reached 4,081 next-day fire cells vs biggest_today 3,713; supported: True

Forecast, S-NPP labels, 2019 thresholds:

|                           |    AP |   AP_new |    F1 |   IoU |   area_ratio |   P@20 |   P@50 |
|:--------------------------|------:|---------:|------:|------:|-------------:|-------:|-------:|
| Yesterday's fires (cell)  | 0.081 |    0.004 | 0.184 | 0.101 |        1.707 |  0.196 |  0.146 |
| Yesterday's fires (3x3)   | 0.099 |    0.021 | 0.192 | 0.106 |        0.604 |  0.186 |  0.138 |
| Last 7 days (5x5)         | 0.059 |    0.014 | 0.112 | 0.059 |        0.53  |  0.121 |  0.099 |
| Dryness only (-rain 30d)  | 0.011 |    0.006 | 0.024 | 0.012 |       37.835 |  0.002 |  0.003 |
| Logistic regression (all) | 0.155 |    0.028 | 0.228 | 0.129 |        0.612 |  0.247 |  0.186 |
| LightGBM + peat + rain    | 0.162 |    0.032 | 0.236 | 0.134 |        0.625 |  0.273 |  0.204 |

Forecast, NOAA-20 labels:

|                           |    AP |   AP_new |    F1 |   IoU |   area_ratio |   P@20 |   P@50 |
|:--------------------------|------:|---------:|------:|------:|-------------:|-------:|-------:|
| Yesterday's fires (cell)  | 0.072 |    0.004 | 0.175 | 0.096 |        1.696 |  0.195 |  0.143 |
| Yesterday's fires (3x3)   | 0.091 |    0.019 | 0.182 | 0.1   |        0.6   |  0.18  |  0.134 |
| Last 7 days (5x5)         | 0.055 |    0.013 | 0.107 | 0.057 |        0.527 |  0.119 |  0.096 |
| Dryness only (-rain 30d)  | 0.01  |    0.006 | 0.024 | 0.012 |       37.585 |  0.002 |  0.003 |
| Logistic regression (all) | 0.142 |    0.027 | 0.219 | 0.123 |        0.608 |  0.24  |  0.183 |
| LightGBM + peat + rain    | 0.148 |    0.029 | 0.227 | 0.128 |        0.621 |  0.268 |  0.202 |

New-fire model:

|                              |    AP |    F1 |   IoU |   area_ratio |   P@20 |   P@50 |
|:-----------------------------|------:|------:|------:|-------------:|-------:|-------:|
| Climatology (past fire rate) | 0.011 | 0.023 | 0.012 |       31.825 |  0.007 |  0.007 |
| Nearby fires, last 7 days    | 0.026 | 0.078 | 0.041 |        3.132 |  0.042 |  0.037 |
| Dryness only (-rain 30d)     | 0.009 | 0.023 | 0.011 |       23.206 |  0     |  0     |
| Peat x dry days              | 0.007 | 0.022 | 0.011 |        3.738 |  0.006 |  0.006 |
| Logistic regression (all)    | 0.05  | 0.1   | 0.052 |        1.147 |  0.063 |  0.057 |
| LightGBM + peat + rain       | 0.057 | 0.116 | 0.062 |        1.06  |  0.069 |  0.062 |

Triage (share of next-day new fire cells, all days pooled):

| Tier | Share of clusters | Forecast triage | Persistence triage |
|---|---|---|---|
| P1 | 0.5% | 17.0% | 16.3% |
| P2 | 3.4% | 21.2% | 17.2% |

Dispatch (next-day new fire cells in visited clusters, all crews, all days):

| Plan | Visits | New fire cells reached | Peat-weighted | Share of oracle (peat-weighted) |
|---|---|---|---|---|
| solver | 1,122 | 4,081 | 7,097 | 62.0% |
| greedy | 1,060 | 3,853 | 6,658 | 58.2% |
| nearest | 2,847 | 1,110 | 1,790 | 15.6% |
| biggest_today | 1,974 | 3,713 | 6,193 | 54.1% |
| oracle | 1,837 | 7,189 | 11,443 | 100.0% |
