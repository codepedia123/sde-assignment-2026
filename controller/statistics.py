from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .store import Store


DIMENSIONS = {"welcome_message", "voice", "caller_id"}
FEATURES = {"lang", "city_tier", "age_band", "value_band"}


@dataclass(frozen=True)
class Rate:
    option: str
    trials: int
    successes: int

    @property
    def value(self) -> float:
        return self.successes / self.trials if self.trials else 0.0

    def wilson(self, z: float = 1.96) -> tuple[float, float]:
        if not self.trials:
            return 0.0, 1.0
        p, n = self.value, self.trials
        den = 1 + z * z / n
        center = (p + z * z / (2 * n)) / den
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
        return max(0.0, center - half), min(1.0, center + half)


def two_proportion_z(a: Rate, b: Rate) -> float:
    if not a.trials or not b.trials:
        return 0.0
    pooled = (a.successes + b.successes) / (a.trials + b.trials)
    se = math.sqrt(max(pooled * (1 - pooled) * (1 / a.trials + 1 / b.trials), 1e-12))
    return (a.value - b.value) / se


def option_rates(
    store: Store,
    workflow_id: str,
    dimension: str,
    since_minute: int,
    cohort_id: str | None = None,
    until_minute: int | None = None,
    prompt_version: str | None = None,
) -> list[Rate]:
    if dimension not in DIMENSIONS:
        raise ValueError(dimension)
    if dimension == "caller_id":
        trials, successes = "COUNT(*)", "SUM(connected)"
    else:
        trials, successes = "SUM(connected)", "SUM(engaged)"
    where = "workflow_id=? AND sim_minute>=?"
    params: list[Any] = [workflow_id, since_minute]
    if until_minute is not None:
        where += " AND sim_minute<?"
        params.append(until_minute)
    if prompt_version is not None:
        where += " AND prompt_version=?"
        params.append(prompt_version)
    if cohort_id is not None:
        where += " AND cohort_id=?"
        params.append(cohort_id)
    rows = store.rows(
        f"SELECT {dimension} option,{trials} trials,{successes} successes FROM calls "
        f"WHERE {where} GROUP BY {dimension}", tuple(params)
    )
    return [Rate(str(r["option"]), int(r["trials"] or 0), int(r["successes"] or 0)) for r in rows]


def choose_winner(rates: list[Rate], min_trials: int = 100, min_uplift: float = 0.015) -> dict[str, Any] | None:
    eligible = [r for r in rates if r.trials >= min_trials]
    if len(eligible) < 2:
        return None
    ranked = sorted(eligible, key=lambda r: (r.value, r.trials, r.option), reverse=True)
    best, runner = ranked[0], ranked[1]
    comparisons = max(len(eligible) - 1, 1)
    # A conservative deterministic approximation to a Bonferroni-adjusted 95% threshold.
    z_required = 1.96 if comparisons == 1 else 2.24 if comparisons == 2 else 2.39
    z = two_proportion_z(best, runner)
    uplift = best.value - runner.value
    if uplift < min_uplift or z < z_required:
        return None
    return {
        "winner": best.option,
        "rate": best.value,
        "trials": best.trials,
        "runner_up": runner.option,
        "runner_rate": runner.value,
        "runner_trials": runner.trials,
        "absolute_uplift": uplift,
        "z": z,
        "z_required": z_required,
        "comparisons": comparisons,
    }


def workflow_metrics(store: Store, workflow_id: str, since_minute: int | None = None) -> dict[str, Any]:
    clause, params = "workflow_id=?", [workflow_id]
    if since_minute is not None:
        clause += " AND sim_minute>=?"
        params.append(since_minute)
    row = store.rows(
        "SELECT COUNT(*) dials,SUM(connected) connected,SUM(engaged) engaged,"
        "SUM(hallucination_flag) flags FROM calls WHERE " + clause, tuple(params)
    )[0]
    d, c, e, f = (int(row[k] or 0) for k in ("dials", "connected", "engaged", "flags"))
    return {
        "dials": d,
        "connected": c,
        "engaged": e,
        "hallucination_flags": f,
        "pickup_rate": c / d if d else 0.0,
        "engagement_rate": e / c if c else 0.0,
        "flag_rate": f / c if c else 0.0,
    }


def feature_winner_candidates(
    store: Store,
    workflow_id: str,
    dimension: str,
    since_minute: int,
    global_winner: str,
    min_trials: int = 60,
) -> list[dict[str, Any]]:
    if dimension not in {"welcome_message", "voice"}:
        raise ValueError(dimension)
    candidates: list[dict[str, Any]] = []
    for feature in sorted(FEATURES):
        rows = store.rows(
            f"SELECT l.{feature} feature_value,c.{dimension} option,SUM(c.connected) trials,"
            "SUM(c.engaged) successes FROM calls c JOIN leads l ON l.lead_id=c.lead_id "
            "WHERE c.workflow_id=? AND c.sim_minute>=? "
            f"GROUP BY l.{feature},c.{dimension}",
            (workflow_id, since_minute),
        )
        by_value: dict[str, list[Rate]] = {}
        for row in rows:
            by_value.setdefault(str(row["feature_value"]), []).append(
                Rate(str(row["option"]), int(row["trials"] or 0), int(row["successes"] or 0))
            )
        for value, rates in by_value.items():
            winner = choose_winner(rates, min_trials=min_trials, min_uplift=0.025)
            if winner and winner["winner"] != global_winner:
                winner.update({"feature": feature, "value": value, "dimension": dimension})
                candidates.append(winner)
    return sorted(candidates, key=lambda c: (c["absolute_uplift"], c["z"]), reverse=True)


def caller_health(store: Store, workflow_id: str, since_minute: int) -> list[Rate]:
    return option_rates(store, workflow_id, "caller_id", since_minute)


def hallucination_window(store: Store, workflow_id: str, since_minute: int) -> dict[str, Any]:
    return workflow_metrics(store, workflow_id, since_minute)

