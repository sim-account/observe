from __future__ import annotations

import argparse

import json

from pathlib import Path

import numpy as np

import pandas as pd

from scipy.stats import t


def mean_interval(values: pd.Series, prefix: str) -> dict:
    """Mean and paired-trial Student-t interval, in hours."""
    n = len(values)
    mean = float(values.mean()) if n else None
    half_width = float(t.ppf(0.975, n - 1) * values.std() / np.sqrt(n)) if n > 1 else None
    return {
        f"{prefix}_mean_h": mean,
        f"{prefix}_ci95_lower_h": mean - half_width if half_width is not None else None,
        f"{prefix}_ci95_upper_h": mean + half_width if half_width is not None else None,
    }

def summarize(group: pd.DataFrame) -> dict:
    executed = group[group.target_detected & group.recommendation_executed].dropna(
        subset=["baseline_cycle_hours", "injected_cycle_hours",
                "cycle_time_reduction_after_recommendation_hours"])
    benefit = executed.cycle_time_reduction_after_recommendation_hours
    increase = executed.injected_cycle_hours - executed.baseline_cycle_hours
    n = len(benefit)
    eligible = executed[executed.goal_b_applicable
                        & (executed.source_cycle_loss_hours > 0)
                        & (executed.model_loss_hours > 0)]
    recovery = (100 * eligible.cycle_time_reduction_after_recommendation_hours
                / eligible.model_loss_hours).dropna()
    return {
        "n_trials": len(group),
        "n_injection_verified": int(group.injection_verified.sum()),
        "n_target_detected": int(group.target_detected.sum()),
        "n_baseline_target": int(group.target_detected_in_baseline.sum()),
        "n_recommendation_executed_total": int(group.recommendation_executed.sum()),
        "n_effect": n,
        "n_positive_effect": int((benefit > 0).sum()),
        "source_loss_mean_h": float(group.source_cycle_loss_hours.mean()),
        "model_loss_mean_h": float(group.model_loss_hours.mean()),
        **mean_interval(increase, "injection_increase"),
        **mean_interval(benefit, "benefit"),
        "control_cycle_mean_h": float(executed.baseline_cycle_hours.mean()) if n else None,
        "as_is_cycle_mean_h": float(executed.injected_cycle_hours.mean()) if n else None,
        "to_be_cycle_mean_h": float((executed.injected_cycle_hours - benefit).mean()) if n else None,
        "n_recovery": len(recovery),
        "recovery_mean_pct": float(recovery.mean()) if len(recovery) else None,
        "recovery_median_pct": float(recovery.median()) if len(recovery) else None,
    }

