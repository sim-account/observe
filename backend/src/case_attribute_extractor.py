"""
Case-attribute-based process observation extractor.

Discovers conditional patterns connecting case-level attributes (e.g. region,
ticket_type, tier) to process behaviour: cycle time, rework rate, and activity
presence / absence.  Only observations that are both statistically meaningful AND
operationally critical are surfaced.
"""
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional
from scipy.stats import mannwhitneyu, fisher_exact, chi2_contingency


def _test_duration_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Mann-Whitney U p-value for two duration samples. Returns 1.0 if too small."""
    if len(a) < 5 or len(b) < 5:
        return 1.0
    try:
        _, p = mannwhitneyu(a, b, alternative="two-sided")
        return float(p)
    except Exception:
        return 1.0


def _test_proportion_diff(k1: int, n1: int, k2: int, n2: int) -> float:
    """Fisher's exact (n ≤ 1000 or small expected) or chi-squared (n > 1000)."""
    if n1 <= 0 or n2 <= 0:
        return 1.0
    k1, k2 = max(0, min(k1, n1)), max(0, min(k2, n2))
    p1, p2 = k1 / n1, k2 / n2
    min_expected = min(n1 * p1, n1 * (1 - p1), n2 * p2, n2 * (1 - p2))
    try:
        if n1 + n2 <= 1000 or min_expected < 5:
            _, p = fisher_exact([[k1, n1 - k1], [k2, n2 - k2]])
        else:
            _, p, _, _ = chi2_contingency(
                [[k1, n1 - k1], [k2, n2 - k2]], correction=False
            )
        return float(p)
    except Exception:
        return 1.0


# ── public API ────────────────────────────────────────────────────────────────

def derive_case_log_from_event_log(
    df: pd.DataFrame,
    case_id_col: str,
    exclude_cols: set,
    max_cardinality: int = 20,
) -> Optional[pd.DataFrame]:
    """
    Build a per-case attribute DataFrame from the extra (non-mapped) columns
    in the raw event log DataFrame.

    Returns a DataFrame with a 'case_id' column plus one column per discovered
    attribute, or None if no usable attribute columns exist.

    Each attribute value per case is the mode (most frequent value) so that
    row-level event noise doesn't pollute case-level attributes.
    """
    attr_cols = [
        c for c in df.columns
        if c not in exclude_cols
        and df[c].notna().sum() > 0
        and 1 < df[c].nunique() <= max_cardinality
    ]
    if not attr_cols:
        return None

    # Normalize the case ID to string, matching what parse_event_log produces
    df_work = df[[case_id_col] + attr_cols].copy()
    df_work["__cid__"] = df_work[case_id_col].astype(str).str.strip()

    derived = (
        df_work.groupby("__cid__")[attr_cols]
        .agg(lambda x: x.mode().iloc[0] if not x.mode().empty else x.iloc[0])
        .reset_index()
        .rename(columns={"__cid__": "case_id"})
    )

    return derived if len(derived) > 0 else None


# ── public API ────────────────────────────────────────────────────────────────

def extract_case_attribute_observations(
    traces: pd.DataFrame,
    case_df: pd.DataFrame,
    case_id_col: str,
    min_cases: int = 20,
    p_threshold: float = 0.05,
    min_effect: float = 0.20,   # minimum 20% practical difference (ratio ≥ 1.2 or ≤ 0.8)
    max_cardinality: int = 20,
) -> List[Dict[str, Any]]:
    """
    Join the event log (traces) with case attributes (case_df) and mine three
    families of case-attribute observations:

      1. cycle_time_effect  — attribute value drives significantly longer/shorter cases
      2. rework_effect      — attribute value correlates with elevated rework rate
      3. activity_presence  — attribute value predicts whether an activity occurs
      4. activity_absence   — attribute value predicts that a normally-common
                              activity is skipped
    """
    case_stats = _compute_case_stats(traces)
    if case_stats.empty:
        return []

    merged = case_df.merge(case_stats, left_on=case_id_col, right_on="case_id", how="inner")
    n_total = len(merged)
    if n_total < min_cases:
        return []

    # ── attribute columns: low cardinality, non-constant ─────────────────────
    skip_cols = {case_id_col, "case_id"}
    attr_cols = [
        c for c in case_df.columns
        if c not in skip_cols and 1 < case_df[c].nunique() <= max_cardinality
    ]
    if not attr_cols:
        return []

    # ── overall baselines ─────────────────────────────────────────────────────
    ct_vals_all = merged["cycle_time_hours"].dropna()
    overall_ct = float(ct_vals_all.mean()) if len(ct_vals_all) >= min_cases else None

    # ── activity presence matrix (vectorised) ─────────────────────────────────
    act_presence = (
        traces.groupby(["case_id", "activity"])
        .size()
        .unstack(fill_value=0)
        .astype(bool)
    )
    activity_counts = traces.groupby(["case_id", "activity"]).size().unstack(fill_value=0)
    activity_counts = activity_counts.loc[activity_counts.index.isin(merged["case_id"])]
    activities = act_presence.columns.tolist()

    # attach per-case presence flags to merged df
    merged_act = merged.merge(act_presence, left_on="case_id", right_index=True, how="left")

    observations: List[Dict[str, Any]] = []

    for attr_col in attr_cols:
        attr_values = merged[attr_col].dropna().unique()

        for val in attr_values:
            mask = merged_act[attr_col] == val
            subset = merged_act[mask]
            n = int(mask.sum())
            if n < min_cases:
                continue

            val_str = str(val)

            # ── 1. Cycle time effect (Mann-Whitney U) ────────────────────────
            if overall_ct is not None and overall_ct > 0:
                ct_sub = subset["cycle_time_hours"].dropna().values
                ct_rest = merged_act[~mask]["cycle_time_hours"].dropna().values
                if len(ct_sub) >= 5 and len(ct_rest) >= 5:
                    cond_ct = float(ct_sub.mean())
                    reference_ct = float(ct_rest.mean())
                    ratio = cond_ct / max(reference_ct, 0.001)
                    p_val = _test_duration_diff(ct_sub, ct_rest)
                    min_ratio = 1.0 + min_effect
                    if p_val < p_threshold and (ratio >= min_ratio or ratio <= 1.0 / min_ratio):
                        direction = "longer" if ratio > 1 else "shorter"
                        eff = ratio if ratio >= 1 else 1 / max(ratio, 0.001)
                        impact = abs(ratio - 1.0) * (n / n_total)
                        severity = "red" if abs(ratio - 1) >= 1.0 else "orange" if abs(ratio - 1) >= 0.5 else "blue"
                        observations.append({
                            "observation_type": "case_attribute",
                            "sub_type": "cycle_time_effect",
                            "attribute": attr_col,
                            "attribute_value": val_str,
                            "n_cases": n,
                            "n_total": n_total,
                            "ratio": round(ratio, 2),
                            "baseline_value": round(reference_ct, 1),
                            "conditional_value": round(cond_ct, 1),
                            "unit": "hours",
                            "impact": round(impact, 4),
                            "severity": severity,
                            "rule_text": (
                                f"When {attr_col} = \"{val_str}\": avg cycle time is "
                                f"{eff:.1f}× {direction} than other cases "
                                f"({cond_ct:.1f}h vs {reference_ct:.1f}h in other cases, n={n:,}, p={p_val:.3f})"
                            ),
                        })

            # Rework is activity-specific: retain the activity responsible for
            # the segment difference, instead of guessing from global rankings.
            for activity in activities:
                visits = activity_counts[activity]
                ids = subset["case_id"]
                in_segment = visits.index.isin(ids)
                a = visits[in_segment & visits.gt(0)]
                b = visits[~in_segment & visits.gt(0)]
                if len(a) < min_cases or len(b) < min_cases:
                    continue
                k1, k2 = int(a.gt(1).sum()), int(b.gt(1).sum())
                cp, bp = k1 / len(a), k2 / len(b)
                ratio = cp / max(bp, 0.001)
                p_val = _test_proportion_diff(k1, len(a), k2, len(b))
                if p_val >= p_threshold or (1 / (1 + min_effect) < ratio < 1 + min_effect):
                    continue
                impact = abs(cp - bp) * len(a) / n_total
                observations.append({
                    "observation_type": "case_attribute", "sub_type": "rework_effect",
                    "attribute": attr_col, "attribute_value": val_str, "activity": activity,
                    "n_cases": len(a), "n_total": n_total, "ratio": round(ratio, 2),
                    "baseline_value": round(bp * 100, 1), "conditional_value": round(cp * 100, 1),
                    "unit": "pct", "impact": round(impact, 4), "severity": "orange",
                    "rule_text": (f"When {attr_col} = '{val_str}': '{activity}' repeats in "
                                  f"{cp:.0%} of visiting cases vs {bp:.0%} for other cases "
                                  f"(n={len(a)}, p={p_val:.3f})"),
                })

            # ── 3 & 4. Activity presence / absence (Fisher/chi-squared) ──────
            act_cols_present = [a for a in activities if a in subset.columns]
            if not act_cols_present:
                continue

            cond_pres = subset[act_cols_present].mean()
            min_ratio = 1.0 + min_effect

            for activity in act_cols_present:
                bp = float(merged_act[~mask][activity].mean())
                cp = float(cond_pres.get(activity, 0))
                if bp < 0.03:
                    continue
                if bp <= 0:
                    continue
                ratio = cp / bp

                # Statistical test for presence/absence
                k1 = int(subset[activity].sum()) if activity in subset.columns else 0
                n1 = n
                k2 = int(merged_act[~mask][activity].sum()) if activity in merged_act.columns else 0
                n2 = int((~mask).sum())
                p_val = _test_proportion_diff(k1, n1, k2, n2)

                if p_val >= p_threshold:
                    continue

                if ratio >= min_ratio and cp >= 0.15:
                    impact = (cp - bp) * (n / n_total)
                    observations.append({
                        "observation_type": "case_attribute",
                        "sub_type": "activity_presence",
                        "attribute": attr_col,
                        "attribute_value": val_str,
                        "activity": activity,
                        "n_cases": n,
                        "n_total": n_total,
                        "ratio": round(ratio, 2),
                        "baseline_value": round(bp * 100, 1),
                        "conditional_value": round(cp * 100, 1),
                        "unit": "pct",
                        "impact": round(impact, 4),
                        "severity": "blue",
                        "rule_text": (
                            f"When {attr_col} = \"{val_str}\": \"{activity}\" occurs in "
                            f"{cp*100:.0f}% of cases "
                            f"({ratio:.1f}× vs {bp*100:.0f}% in other cases, n={n:,}, p={p_val:.3f})"
                        ),
                    })

                elif ratio <= 1.0 / min_ratio and bp >= 0.20 and cp <= 0.20:
                    impact = (bp - cp) * (n / n_total)
                    observations.append({
                        "observation_type": "case_attribute",
                        "sub_type": "activity_absence",
                        "attribute": attr_col,
                        "attribute_value": val_str,
                        "activity": activity,
                        "n_cases": n,
                        "n_total": n_total,
                        "ratio": round(ratio, 2),
                        "baseline_value": round(bp * 100, 1),
                        "conditional_value": round(cp * 100, 1),
                        "unit": "pct",
                        "impact": round(impact, 4),
                        "severity": "orange",
                        "rule_text": (
                            f"When {attr_col} = \"{val_str}\": \"{activity}\" rarely occurs "
                            f"({cp*100:.0f}% vs {bp*100:.0f}% in other cases, n={n:,}, p={p_val:.3f})"
                        ),
                    })

    return _deduplicate_and_rank(observations)


# ── helpers ───────────────────────────────────────────────────────────────────

def _compute_case_stats(traces: pd.DataFrame) -> pd.DataFrame:
    """Build a per-case DataFrame with cycle_time_hours and has_rework."""
    has_time = "start_time" in traces.columns
    records = []
    for case_id, group in traces.groupby("case_id"):
        activities = group["activity"].tolist()
        has_rework = any(activities.count(a) > 1 for a in set(activities))

        cycle_time_hours = None
        if has_time:
            start = group["start_time"].min()
            end_col = "end_time" if "end_time" in group.columns else "start_time"
            end = group[end_col].max()
            if pd.notnull(start) and pd.notnull(end) and end > start:
                cycle_time_hours = (end - start).total_seconds() / 3600

        records.append({"case_id": case_id, "cycle_time_hours": cycle_time_hours, "has_rework": has_rework})

    return pd.DataFrame(records) if records else pd.DataFrame(columns=["case_id", "cycle_time_hours", "has_rework"])


def _deduplicate_and_rank(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sort by impact descending.  Cap activity presence/absence to top-5 per
    (attribute, attribute_value) pair so a single attribute value doesn't
    flood the list with activity observations.
    """
    sorted_observations = sorted(observations, key=lambda x: -x["impact"])

    act_counts: Dict[str, int] = {}
    seen_exact: set = set()
    result = []

    for observation in sorted_observations:
        key = f"{observation['attribute']}:{observation['attribute_value']}:{observation['sub_type']}"
        if observation["sub_type"] in ("activity_presence", "activity_absence", "rework_effect"):
            key_act = key + f":{observation.get('activity', '')}"
            if key_act in seen_exact:
                continue
            seen_exact.add(key_act)
            av_key = f"{observation['attribute']}:{observation['attribute_value']}:{observation['sub_type']}"
            act_counts[av_key] = act_counts.get(av_key, 0) + 1
            if act_counts[av_key] > 5:
                continue
        else:
            if key in seen_exact:
                continue
            seen_exact.add(key)

        result.append(observation)

    return result


def detect_case_id_column(case_df: pd.DataFrame, event_case_id_hint: str = "") -> str:
    """
    Heuristic: pick the column in case_df most likely to be the case identifier.
    Prefers an exact name match with the event log case_id column, then looks
    for common patterns, then falls back to the first high-cardinality column.
    """
    cols = case_df.columns.tolist()
    n = len(case_df)

    # Exact match with event log hint
    if event_case_id_hint and event_case_id_hint in cols:
        return event_case_id_hint

    # Name patterns
    patterns = ["case_id", "caseid", "case id", "ticket", "ticketnumber", "id", "case", "key"]
    for pat in patterns:
        for col in cols:
            if col.lower().replace(" ", "").replace("_", "") == pat.replace("_", ""):
                return col

    # Fallback: column with highest cardinality that looks like an ID
    best = max(cols, key=lambda c: case_df[c].nunique())
    return best
