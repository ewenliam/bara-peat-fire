"""Step 4, Dispatch: the order in which each crew visits clusters ("C9, then C6, then C2").

Each crew leaves its base in the morning and has `day_h` hours. Each visit costs travel time
plus `on_site_h` on site. The plan picks the visits that cover the most forecast risk
(peat-weighted expected new cells, from triage.py). This is a small orienteering problem;
with at most MAX_CANDIDATES clusters per crew it is solved exactly by dynamic programming
over subsets. A greedy rule (most risk per hour next) and two plans that do not use the
forecast (nearest fire first, biggest fire today first) are kept for comparison.

The `Setup` holds the crew assumptions so a commander can ask what-if questions (more crews,
fewer bases, a shorter day). With several crews per base, crews are planned one after another
and each takes the best remaining clusters.

Visiting is not extinguishing: peat smoulders underground and one visit rarely finishes it.
All speeds and times are assumptions to confirm with Manggala Agni. [[TODO: need source]]
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from places import ISLAND, km_xy, locate

SPEED_KMH = 30.0     # assumed average speed over road, river and foot legs
DETOUR = 1.4         # assumed route length / straight-line distance
ON_SITE_H = 2.0      # assumed time per visit
DAY_H = 10.0         # crew hours per day, including travel
MAX_CANDIDATES = 10  # per crew, highest risk first; keeps the exact solver small
METHODS = ("solver", "greedy", "nearest", "biggest_today")


@dataclass(frozen=True)
class Setup:
    speed_kmh: float = SPEED_KMH
    detour: float = DETOUR
    on_site_h: float = ON_SITE_H
    day_h: float = DAY_H
    crews_per_base: int = 1


DEFAULT = Setup()


def hours(a: np.ndarray, b: np.ndarray, s: Setup = DEFAULT) -> np.ndarray:
    """Travel hours between every row of a and every row of b (km coordinates)."""
    d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
    return d * s.detour / s.speed_kmh


def solve_exact(value: np.ndarray, from_base: np.ndarray, between: np.ndarray,
                s: Setup = DEFAULT) -> list[int]:
    """Visit order with the highest total value that fits in the day (open route, no return)."""
    n = len(value)
    step = between + s.on_site_h
    time = np.full((1 << n, n), np.inf)
    for j in range(n):
        time[1 << j, j] = from_base[j] + s.on_site_h
    best_mask, best_last, best_val = 0, -1, 0.0
    mask_value = np.array([value[[j for j in range(n) if m >> j & 1]].sum() for m in range(1 << n)])
    for mask in range(1, 1 << n):
        for last in range(n):
            t = time[mask, last]
            if t > s.day_h:
                continue
            if mask_value[mask] > best_val:
                best_mask, best_last, best_val = mask, last, mask_value[mask]
            for nxt in range(n):
                if mask >> nxt & 1:
                    continue
                nt = t + step[last, nxt]
                m2 = mask | 1 << nxt
                if nt < time[m2, nxt]:
                    time[m2, nxt] = nt
    order, mask, last = [], best_mask, best_last
    while last >= 0:  # walk back: the previous stop is the one that gives this arrival time
        order.append(last)
        prev_mask = mask & ~(1 << last)
        if prev_mask == 0:
            break
        prev = [p for p in range(n) if prev_mask >> p & 1
                and np.isclose(time[prev_mask, p] + step[p, last], time[mask, last])]
        mask, last = prev_mask, prev[0]
    return order[::-1]


def solve_sequence(rank: np.ndarray, from_base: np.ndarray, between: np.ndarray,
                   by_rate: np.ndarray | None = None, nearest: bool = False,
                   s: Setup = DEFAULT) -> list[int]:
    """Greedy visits: next = best rank (or best value per hour, or nearest) that still fits."""
    order, t, pos = [], 0.0, None
    left = set(range(len(rank)))
    while left:
        travel = {j: (from_base[j] if pos is None else between[pos, j]) for j in left}
        fits = [j for j in left if t + travel[j] + s.on_site_h <= s.day_h]
        if not fits:
            break
        if nearest:
            j = min(fits, key=lambda k: travel[k])
        elif by_rate is not None:
            j = max(fits, key=lambda k: by_rate[k] / (travel[k] + s.on_site_h))
        else:
            j = min(fits, key=lambda k: rank[k])
        t += travel[j] + s.on_site_h
        order.append(j)
        left.remove(j)
        pos = j
    return order


def plan(table: pd.DataFrame, bases: pd.DataFrame, method: str = "solver",
         s: Setup = DEFAULT, crews_at: dict[str, int] | None = None) -> list[dict]:
    """Routes for one day. table: one row per cluster with id, lat, lon, n_today, risk, tier.

    crews_at overrides the number of crews for named bases (others get s.crews_per_base).
    """
    if method in ("solver", "greedy"):
        pool = table[table["tier"].isin(["P1", "P2"])]
    else:  # plans without the forecast only know today's detections
        pool = table[table["n_today"] > 0]
    if pool.empty or bases.empty:
        return []
    bases = bases.reset_index(drop=True)
    cxy = km_xy(pool["lat"].to_numpy(), pool["lon"].to_numpy())
    bxy = km_xy(bases["lat"].to_numpy(), bases["lon"].to_numpy())
    to_base = hours(cxy, bxy, s)
    # crews only serve clusters on their own island (no driving across the sea)
    cl_island = locate(pool["lat"].to_numpy(), pool["lon"].to_numpy())["province"].map(ISLAND).to_numpy()
    b_island = bases["province"].map(ISLAND).to_numpy()
    to_base = np.where(cl_island[:, None] == b_island[None, :], to_base, np.inf)
    home = to_base.argmin(axis=1)
    reachable = to_base.min(axis=1) + s.on_site_h <= s.day_h
    risk_all = pool["risk"].to_numpy()

    routes = []
    for b, base in bases.iterrows():
        taken = np.zeros(len(pool), dtype=bool)
        for crew in range(1, (crews_at or {}).get(base["name"], s.crews_per_base) + 1):
            sel = np.nonzero((home == b) & reachable & ~taken)[0]
            if not len(sel):
                break
            if method in ("solver", "greedy"):
                key = risk_all
            elif method == "nearest":
                key = -to_base[:, b]
            else:
                key = pool["n_today"].to_numpy()
            sel = sel[np.argsort(-key[sel], kind="stable")][:MAX_CANDIDATES]
            from_base = to_base[sel, b]
            between = hours(cxy[sel], cxy[sel], s)
            risk = risk_all[sel]
            if method == "solver":
                order = solve_exact(risk, from_base, between, s)
            elif method == "greedy":
                order = solve_sequence(np.arange(len(sel)), from_base, between, by_rate=risk, s=s)
            elif method == "nearest":
                order = solve_sequence(np.arange(len(sel)), from_base, between, nearest=True, s=s)
            else:
                order = solve_sequence(np.arange(len(sel)), from_base, between, s=s)
            if not order:
                break
            t, pos, stops = 0.0, None, []
            for j in order:
                t += from_base[j] if pos is None else between[pos, j]
                stops.append({"cluster_id": pool.index[sel[j]], "arrive_h": round(t, 1)})
                t += s.on_site_h
                pos = j
            taken[sel[order]] = True
            routes.append({"base": base["name"], "crew": crew, "province": base["province"],
                           "stops": stops, "risk_covered": round(float(risk[order].sum()), 1)})
    return routes
