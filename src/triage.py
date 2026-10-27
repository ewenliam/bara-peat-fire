"""Step 3, Triage: sort fire clusters into P1 (brigade now), P2 (village fire watch), P3 (monitor).

Risk is the forecast's expected number of newly burning 1.1 km cells tomorrow, weighted up
for peat because peat fires smoulder underground, are hard to put out and emit far more
smoke. The weights and cut-offs are policy choices, not learned, and are kept here in the
open so a commander can change them.
"""
import numpy as np
import pandas as pd

PEAT_WEIGHT = 1.0  # a cluster fully on peat counts double
P1_MIN = 5.0       # peat-weighted expected new cells tomorrow for "brigade now"
P2_MIN = 1.0       # ... for "village fire watch (Masyarakat Peduli Api)"
TIER_RULE = (f"risk = expected_new_cells x (1 + {PEAT_WEIGHT:g} x peat_share); "
             f"P1 if risk >= {P1_MIN:g}, P2 if risk >= {P2_MIN:g}, else P3")


def add_tiers(table: pd.DataFrame) -> pd.DataFrame:
    risk = table["expected_new_cells"] * (1 + PEAT_WEIGHT * table["peat_share"])
    tier = np.select([risk >= P1_MIN, risk >= P2_MIN], ["P1", "P2"], "P3")
    return table.assign(risk=risk, tier=tier)


def persistence_tiers(table: pd.DataFrame) -> pd.Series:
    """Baseline without the forecast: same tier sizes, ranked by detections today."""
    sizes = table["tier"].value_counts()
    order = table["n_today"].rank(method="first", ascending=False)
    n1, n2 = sizes.get("P1", 0), sizes.get("P2", 0)
    return pd.Series(np.select([order <= n1, order <= n1 + n2], ["P1", "P2"], "P3"), index=table.index)
