"""
process_observations.py
────────────────
Extracts conditional behavioural patterns (Process Observations) from the event
log.  These are higher-level rules that explain *when* and *why* behaviour
deviates from the average.  They feed the recommendations engine only —
the simulation engine never reads them.

Process Observation families:
  - Conditional time rules    (rework penalty, speed gap, predecessor effect)
  - Conditional rework rules  (predecessor-triggered, resource-triggered)
  - Resource routing rules    (predecessor → resource concentration)
  - Eventual sequence rules   (gates, exclusions, strong implications)
  - Conditional parallel rules (always-parallel, resource/predecessor-conditional)
  - Case attribute observations       (re-exported from case_attribute_extractor)
"""

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple
from scipy.stats import mannwhitneyu, fisher_exact, chi2_contingency

# Case attribute observations live in their own module; re-export for convenience.
from src.case_attribute_extractor import (
    extract_case_attribute_observations,
    derive_case_log_from_event_log,
)


# ── Statistical test helpers ──────────────────────────────────────────────────

def _test_duration_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Mann-Whitney U test for two duration samples. Returns p-value.
    Robust to skewed distributions (typical for process durations).
    Returns 1.0 if either group is too small to test."""
    if len(a) < 5 or len(b) < 5:
        return 1.0
    try:
        _, p = mannwhitneyu(a, b, alternative="two-sided")
        return float(p)
    except Exception:
        return 1.0


def _test_proportion_diff(k1: int, n1: int, k2: int, n2: int) -> float:
    """Fisher's exact (n ≤ 1000 or small expected counts) or chi-squared (n > 1000).
    Returns p-value. Returns 1.0 if inputs are invalid."""
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


def extract_process_observations(traces: pd.DataFrame, *, strict: bool = False) -> Dict[str, pd.DataFrame]:
    """Mine all conditional rule observations from a parsed event log.

    Returns a dict of DataFrames consumed by the recommendations engine.
    Each extractor is isolated so a failure in one does not block the others.
    """
    empty_df = pd.DataFrame()

    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if strict:
                raise
            import traceback
            print(f"[WARN] {fn.__name__} failed: {e}", flush=True)
            traceback.print_exc()
            return empty_df

    return {
        "eventual_sequence_rules":      _safe(_eventual_sequence_rules, traces),
        "conditional_time_rules":       _safe(_conditional_time_rules, traces),
        "conditional_rework_rules":     _safe(_conditional_rework_rules, traces),
        "resource_routing_rules":       _safe(_resource_routing_rules, traces),
        "conditional_routing_rules":    _safe(_conditional_routing_rules, traces),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CONDITIONAL PARALLEL RULES + CONDITIONAL RULE OBSERVATIONS
# ══════════════════════════════════════════════════════════════════════════════
def _extract_conditional_parallel_rules(
    traces: pd.DataFrame,
    parallel_pairs_df: Optional[pd.DataFrame] = None,
    case_df: Optional[pd.DataFrame] = None,
    case_id_col: str = "case_id",
) -> pd.DataFrame:
    """Extract conditional patterns that explain *when* activities are parallel.

    Rule types:
      always_parallel       — both sides of a pair have ≥80% parallel rate
      resource_conditional  — parallel rate of A changes significantly by resource
      predecessor_conditional — parallel rate of A changes by preceding activity
      case_attr_conditional — parallel rate of A changes by a case attribute value
    """
    _MIN_N        = 10
    _RATIO_HIGH   = 1.5
    _RATIO_LOW    = 0.67
    _ALWAYS_THRESH = 80.0

    _empty_cols = [
        "rule_type", "activity", "partner", "condition_type", "condition_value",
        "parallel_rate_pct", "baseline_rate_pct", "ratio", "count", "severity", "badge", "rule_text",
    ]
    empty = pd.DataFrame(columns=_empty_cols)

    if "start_time" not in traces.columns or "end_time" not in traces.columns:
        return empty

    sub = traces.dropna(subset=["start_time", "end_time"]).copy()
    if sub.empty:
        return empty

    has_resource = "resource" in sub.columns and sub["resource"].notna().any()

    # ── per-occurrence records ────────────────────────────────────────────────
    occ_rows: List[dict] = []
    for case_id, grp in sub.groupby("case_id"):
        grp_s  = grp.sort_values("start_time").reset_index(drop=True)
        acts   = grp_s["activity"].tolist()
        starts = grp_s["start_time"].tolist()
        ends   = grp_s["end_time"].tolist()
        res_l  = grp_s["resource"].tolist() if has_resource else [None] * len(acts)
        n      = len(acts)

        is_par = [False] * n
        if n >= 2:
            for i in range(n):
                end_i = ends[i]
                if pd.isna(end_i):
                    continue
                for j in range(i + 1, n):
                    sj = starts[j]
                    if pd.isna(sj):
                        continue
                    if sj >= end_i:
                        break
                    is_par[i] = True
                    is_par[j] = True

        for i in range(n):
            rv = res_l[i]
            res_val = str(rv) if rv is not None and not (isinstance(rv, float) and pd.isna(rv)) else None
            occ_rows.append({
                "case_id":       case_id,
                "activity":      acts[i],
                "resource":      res_val,
                "preceding_act": acts[i - 1] if i > 0 else None,
                "is_parallel":   is_par[i],
            })

    if not occ_rows:
        return empty

    occ_df = pd.DataFrame(occ_rows)
    occ_df["is_parallel"] = occ_df["is_parallel"].astype(bool)

    # Merge case attributes
    attr_cols: List[str] = []
    if case_df is not None:
        try:
            cdf = case_df.rename(columns={case_id_col: "case_id"})
            attr_cols = [c for c in cdf.columns if c != "case_id"]
            if attr_cols:
                occ_df = occ_df.merge(cdf[["case_id"] + attr_cols], on="case_id", how="left")
        except Exception:
            attr_cols = []

    # Per-activity baseline parallel rate
    act_base = (
        occ_df.groupby("activity")["is_parallel"]
        .agg(n_total="count", n_par="sum")
        .assign(baseline=lambda d: d["n_par"] / d["n_total"])
    )

    rules: List[dict] = []

    def _sev(ratio: float) -> str:
        return "red" if ratio >= 3 or ratio <= 0.33 else "orange"

    def _dir(ratio: float) -> str:
        return "more" if ratio >= 1 else "less"

    # ── 1. ALWAYS PARALLEL ───────────────────────────────────────────────────
    if parallel_pairs_df is not None and not parallel_pairs_df.empty:
        for _, pr in parallel_pairs_df.iterrows():
            a_pct = float(pr.get("a_parallel_with_b_pct", 0))
            b_pct = float(pr.get("b_parallel_with_a_pct", 0))
            cnt   = int(pr.get("co_parallel_count", 0))
            a, b  = str(pr["activity_a"]), str(pr["activity_b"])
            if a_pct >= _ALWAYS_THRESH and b_pct >= _ALWAYS_THRESH and cnt >= _MIN_N:
                sev = "red" if min(a_pct, b_pct) >= 95 else "orange"
                rules.append({
                    "rule_type": "always_parallel", "activity": a, "partner": b,
                    "condition_type": None, "condition_value": None,
                    "parallel_rate_pct": round(min(a_pct, b_pct), 1),
                    "baseline_rate_pct": None, "ratio": None, "count": cnt,
                    "severity": sev, "badge": "ALWAYS TOGETHER",
                    "rule_text": (
                        f"'{a}' and '{b}' almost always execute in parallel "
                        f"({a_pct:.0f}% of '{a}' occurrences · {b_pct:.0f}% of '{b}' occurrences)"
                    ),
                })

    # ── helpers for conditional rule extraction ──────────────────────────────
    def _check_cond(act: str, group_col: str, group_val, sub_df: pd.DataFrame) -> Optional[dict]:
        n = len(sub_df)
        if n < _MIN_N:
            return None
        if act not in act_base.index:
            return None
        baseline = float(act_base.loc[act, "baseline"])
        if baseline <= 0 or baseline >= 1:
            return None
        cond_rate = float(sub_df["is_parallel"].mean())
        ratio = cond_rate / baseline
        if _RATIO_LOW < ratio < _RATIO_HIGH:
            return None
        return {"baseline": baseline, "cond_rate": cond_rate, "ratio": ratio, "n": n}

    # ── 2. RESOURCE-CONDITIONAL ──────────────────────────────────────────────
    if has_resource:
        for act, act_occ in occ_df.groupby("activity"):
            res_occ = act_occ.dropna(subset=["resource"])
            for res, grp in res_occ.groupby("resource"):
                r = _check_cond(str(act), "resource", res, grp)
                if r is None:
                    continue
                rules.append({
                    "rule_type": "resource_conditional", "activity": str(act), "partner": None,
                    "condition_type": "resource", "condition_value": str(res),
                    "parallel_rate_pct": round(r["cond_rate"] * 100, 1),
                    "baseline_rate_pct": round(r["baseline"] * 100, 1),
                    "ratio": round(r["ratio"], 2), "count": r["n"],
                    "severity": _sev(r["ratio"]), "badge": "RESOURCE",
                    "rule_text": (
                        f"'{act}' is parallel in {r['cond_rate']*100:.0f}% of occurrences "
                        f"when handled by '{res}' "
                        f"(vs {r['baseline']*100:.0f}% overall · {r['ratio']:.1f}× {_dir(r['ratio'])} likely)"
                    ),
                })

    # ── 3. PREDECESSOR-CONDITIONAL ───────────────────────────────────────────
    for act, act_occ in occ_df.groupby("activity"):
        pred_occ = act_occ.dropna(subset=["preceding_act"])
        for pred, grp in pred_occ.groupby("preceding_act"):
            r = _check_cond(str(act), "preceded_by", pred, grp)
            if r is None:
                continue
            rules.append({
                "rule_type": "predecessor_conditional", "activity": str(act), "partner": None,
                "condition_type": "preceded_by", "condition_value": str(pred),
                "parallel_rate_pct": round(r["cond_rate"] * 100, 1),
                "baseline_rate_pct": round(r["baseline"] * 100, 1),
                "ratio": round(r["ratio"], 2), "count": r["n"],
                "severity": _sev(r["ratio"]), "badge": "PREDECESSOR",
                "rule_text": (
                    f"'{act}' is parallel in {r['cond_rate']*100:.0f}% of cases "
                    f"when preceded by '{pred}' "
                    f"(vs {r['baseline']*100:.0f}% overall · {r['ratio']:.1f}× {_dir(r['ratio'])} likely)"
                ),
            })

    # ── 4. CASE ATTRIBUTE CONDITIONAL ────────────────────────────────────────
    for attr in attr_cols:
        if attr not in occ_df.columns:
            continue
        for act, act_occ in occ_df.groupby("activity"):
            for val, grp in act_occ.dropna(subset=[attr]).groupby(attr):
                r = _check_cond(str(act), f"case_attr:{attr}", val, grp)
                if r is None:
                    continue
                rules.append({
                    "rule_type": "case_attr_conditional", "activity": str(act), "partner": None,
                    "condition_type": f"case_attr:{attr}", "condition_value": str(val),
                    "parallel_rate_pct": round(r["cond_rate"] * 100, 1),
                    "baseline_rate_pct": round(r["baseline"] * 100, 1),
                    "ratio": round(r["ratio"], 2), "count": r["n"],
                    "severity": _sev(r["ratio"]), "badge": f"ATTR:{attr}",
                    "rule_text": (
                        f"'{act}' is parallel in {r['cond_rate']*100:.0f}% of cases "
                        f"where {attr}='{val}' "
                        f"(vs {r['baseline']*100:.0f}% overall · {r['ratio']:.1f}× {_dir(r['ratio'])} likely)"
                    ),
                })

    if not rules:
        return empty

    df = pd.DataFrame(rules)
    # Sort: always_parallel first (most structural), then by descending ratio
    type_order = {"always_parallel": 0, "resource_conditional": 1, "predecessor_conditional": 2, "case_attr_conditional": 3}
    df["_type_ord"] = df["rule_type"].map(lambda x: type_order.get(x, 9))
    df["_ratio_abs"] = df["ratio"].abs().fillna(99)
    df = df.sort_values(["_type_ord", "_ratio_abs"], ascending=[True, False]).drop(columns=["_type_ord", "_ratio_abs"]).reset_index(drop=True)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# RULE OBSERVATIONS  — conditional patterns expressed as business rules
# ══════════════════════════════════════════════════════════════════════════════

def _extract_rule_observations(traces: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Mine conditional patterns from traces and return them as labelled rules."""
    return {
        "eventual_sequence_rules": _eventual_sequence_rules(traces),
        "conditional_time_rules": _conditional_time_rules(traces),
        "conditional_rework_rules": _conditional_rework_rules(traces),
        "resource_routing_rules": _resource_routing_rules(traces),
    }


def _eventual_sequence_rules(traces: pd.DataFrame) -> pd.DataFrame:
    """
    Rule type: SEQUENCE
    Form: "If A occurs → B eventually follows with P%"
           "If A occurs → B directly follows next (P%)"
           "If A occurs → B almost never follows (<5%)"

    Only surfaces statistically strong rules (n≥30, P≥0.80 or P≤0.05).
    """
    empty = pd.DataFrame(columns=[
        "activity_a", "activity_b", "p_eventually", "p_directly",
        "n_cases_a", "n_cases_b_follows", "rule_type", "rule_text"
    ])
    if "start_time" not in traces.columns:
        return empty

    # Limit to top activities to avoid O(n²) explosion
    top_acts = traces["activity"].value_counts().head(25).index.tolist()

    # First occurrence per (case, activity) — O(n log n)
    first_occ = (
        traces[traces["activity"].isin(top_acts)]
        .groupby(["case_id", "activity"])["start_time"]
        .min()
        .unstack()
    )

    # Direct-next probabilities from sequence observations (precompute)
    sort_col = "start_time"
    df_s = traces.sort_values(["case_id", sort_col]).copy()
    df_s["next_activity"] = df_s.groupby("case_id")["activity"].shift(-1)
    direct_counts = (
        df_s[df_s["activity"].isin(top_acts) & df_s["next_activity"].isin(top_acts)]
        .groupby(["activity", "next_activity"])
        .size()
        .reset_index(name="cnt")
    )
    act_totals = direct_counts.groupby("activity")["cnt"].sum().reset_index(name="total")
    direct_counts = direct_counts.merge(act_totals, on="activity")
    direct_counts["p_direct"] = direct_counts["cnt"] / direct_counts["total"]
    direct_map = {
        (row["activity"], row["next_activity"]): row["p_direct"]
        for _, row in direct_counts.iterrows()
    }

    records = []
    for act_a in top_acts:
        if act_a not in first_occ.columns:
            continue
        cases_a = first_occ[act_a].dropna()
        n_a = len(cases_a)
        if n_a < 30:
            continue

        for act_b in top_acts:
            if act_a == act_b or act_b not in first_occ.columns:
                continue

            # Cases where B appears after A's first occurrence
            b_times = first_occ.loc[cases_a.index, act_b].dropna()
            n_b_after = int((b_times > cases_a.loc[b_times.index]).sum())
            p_eventually = n_b_after / n_a
            p_directly = direct_map.get((act_a, act_b), 0.0)

            # Only surface strong rules
            if p_eventually < 0.80 and p_eventually > 0.05:
                continue
            if n_b_after < 30 and p_eventually > 0.05:
                continue

            if p_eventually >= 0.95:
                rule_type = "gate"
                rule_text = (
                    f"'{act_a}' → '{act_b}' always eventually follows ({p_eventually:.0%})"
                    + (f" — directly next {p_directly:.0%} of the time" if p_directly > 0.3 else "")
                )
            elif p_eventually >= 0.80:
                rule_type = "strong_implication"
                rule_text = f"'{act_a}' → '{act_b}' eventually follows in {p_eventually:.0%} of cases"
            else:
                rule_type = "exclusion"
                rule_text = f"'{act_a}' → '{act_b}' almost never follows ({p_eventually:.0%} of cases)"

            records.append({
                "activity_a": act_a,
                "activity_b": act_b,
                "p_eventually": round(p_eventually, 3),
                "p_directly": round(p_directly, 3),
                "n_cases_a": n_a,
                "n_cases_b_follows": n_b_after,
                "rule_type": rule_type,
                "rule_text": rule_text,
            })

    if not records:
        return empty
    return (
        pd.DataFrame(records)
        .sort_values(["rule_type", "n_cases_b_follows"], ascending=[True, False])
        .reset_index(drop=True)
    )


def _conditional_time_rules(traces: pd.DataFrame) -> pd.DataFrame:
    """
    Rule type: TIME
    Forms:
      - "If A has rework in the case → duration is Rx longer than in clean cases"
      - "If A is performed by resource R → duration is Rx faster/slower than average"
      - "If A is preceded by B → duration is Rx different from baseline"
    """
    empty = pd.DataFrame(columns=[
        "activity", "condition_type", "condition_value",
        "conditional_mean_min", "baseline_mean_min", "count", "ratio", "rule_text"
    ])
    if "duration_minutes" not in traces.columns:
        return empty

    if "start_time" in traces and "end_time" in traces:
        from src.process_behaviour import _learn_calendars, _working_duration_minutes
        calendars = _learn_calendars(traces)
        traces = traces.copy()
        global_window = (calendars["global_start_hour"], calendars["global_end_hour"])
        traces["duration_minutes"] = [
            _working_duration_minutes(row.start_time, row.end_time,
                *calendars["resource_calendars"].get(str(getattr(row, "resource", "")), global_window),
                calendars["resource_working_days"].get(str(getattr(row, "resource", "")), calendars["global_working_days"]))
            for row in traces.itertuples()
        ]

    records = []
    act_case_counts = traces.groupby(["case_id", "activity"]).size().reset_index(name="n")

    top_acts = traces["activity"].value_counts().head(20).index.tolist()

    sort_col = "start_time" if "start_time" in traces.columns else None
    if sort_col:
        df_s = traces.sort_values(["case_id", sort_col]).copy()
        df_s["prev_activity"] = df_s.groupby("case_id")["activity"].shift(1)

    for act in top_acts:
        act_rows = traces[traces["activity"] == act]
        durs = act_rows["duration_minutes"].dropna()
        if len(durs) < 30 or durs.mean() <= 0:
            continue

        # ── Rule 1: rework-case penalty ────────────────────────────────────
        rework_case_ids = set(
            act_case_counts[(act_case_counts["activity"] == act) & (act_case_counts["n"] > 1)]["case_id"]
        )
        clean_case_ids = set(act_rows["case_id"]) - rework_case_ids

        if len(rework_case_ids) >= 5 and len(clean_case_ids) >= 5:
            dur_rw = traces[(traces["activity"] == act) & (traces["case_id"].isin(rework_case_ids))]["duration_minutes"].dropna().values
            dur_cl = traces[(traces["activity"] == act) & (traces["case_id"].isin(clean_case_ids))]["duration_minutes"].dropna().values
            ratio = dur_rw.mean() / max(dur_cl.mean(), 0.01)
            p_val = _test_duration_diff(dur_rw, dur_cl)
            # Require: p < 0.05 AND minimum practical effect (≥20% difference)
            if p_val < 0.05 and (ratio >= 1.20 or ratio <= 0.80):
                eff_ratio_rw = ratio if ratio >= 1 else 1 / max(ratio, 0.001)
                direction = "longer" if ratio > 1 else "shorter"
                records.append({
                    "activity": act,
                    "condition_type": "rework_in_case",
                    "condition_value": "case has rework",
                    "conditional_mean_min": round(dur_rw.mean(), 1),
                    "baseline_mean_min": round(dur_cl.mean(), 1),
                    "count": len(dur_rw),
                    "ratio": round(ratio, 4), "direction": "negative" if ratio >= 1 else "positive",
                    "rule_text": (
                        f"If '{act}' occurs in a rework case → takes {dur_rw.mean():.0f} min avg "
                        f"({eff_ratio_rw:.1f}x {direction} than {dur_cl.mean():.0f} min in clean cases, p={p_val:.3f})"
                    ),
                })

        # ── Rule 2: resource speed gap — one negative observation (slowest vs others) + one positive (fastest vs others) ───
        if "resource" in traces.columns:
            res_stats = (
                act_rows.groupby("resource")["duration_minutes"]
                .agg(mean="mean", count="count")
                .reset_index()
            )
            res_stats = res_stats[res_stats["count"] >= 5].sort_values("mean")
            if len(res_stats) >= 2:
                fastest = res_stats.iloc[0]
                slowest = res_stats.iloc[-1]

                # Negative: slowest resource vs other resources (Mann-Whitney U + effect size)
                slow_durs = act_rows[act_rows["resource"] == slowest["resource"]]["duration_minutes"].dropna().values
                slow_other = act_rows[act_rows["resource"] != slowest["resource"]]["duration_minutes"].dropna().values
                slow_baseline = float(slow_other.mean())
                ratio_slow = slowest["mean"] / max(slow_baseline, 0.01)
                p_slow = _test_duration_diff(slow_durs, slow_other)
                if p_slow < 0.05 and ratio_slow >= 1.20:
                    eff_slow = ratio_slow
                    records.append({
                        "activity": act,
                        "condition_type": "resource_speed_gap",
                        "condition_value": f"{slowest['resource']} vs {fastest['resource']}",
                        "conditional_mean_min": round(slowest["mean"], 1),
                        "baseline_mean_min": round(slow_baseline, 1),
                        "count": int(slowest["count"]),
                        "ratio": round(ratio_slow, 4), "direction": "negative",
                        "rule_text": (
                            f"'{act}': {slowest['resource']} averages {slowest['mean']:.0f} min "
                            f"({eff_slow:.1f}x slower than {slow_baseline:.0f} min baseline, p={p_slow:.3f})"
                        ),
                    })

                # Positive: fastest resource vs other resources
                fast_durs = act_rows[act_rows["resource"] == fastest["resource"]]["duration_minutes"].dropna().values
                fast_other = act_rows[act_rows["resource"] != fastest["resource"]]["duration_minutes"].dropna().values
                fast_baseline = float(fast_other.mean())
                ratio_fast = fastest["mean"] / max(fast_baseline, 0.01)
                p_fast = _test_duration_diff(fast_durs, fast_other)
                if p_fast < 0.05 and ratio_fast <= 0.80:
                    eff_fast = 1 / max(ratio_fast, 0.001)
                    records.append({
                        "activity": act,
                        "condition_type": "resource_speed_gap",
                        "condition_value": f"{fastest['resource']} vs {slowest['resource']}",
                        "conditional_mean_min": round(fastest["mean"], 1),
                        "baseline_mean_min": round(fast_baseline, 1),
                        "count": int(fastest["count"]),
                        "ratio": round(ratio_fast, 4), "direction": "positive",
                        "rule_text": (
                            f"'{act}': {fastest['resource']} averages {fastest['mean']:.0f} min "
                            f"({eff_fast:.1f}x faster than {fast_baseline:.0f} min baseline, p={p_fast:.3f})"
                        ),
                    })
        # ── Rule 3: preceded-by effect — both negative (slower) and positive (faster) ──
        if sort_col:
            act_with_prev = df_s[df_s["activity"] == act].dropna(subset=["prev_activity"])
            all_durs_act = act_with_prev["duration_minutes"].dropna().values

            for pred, grp in act_with_prev.groupby("prev_activity"):
                pred_durs = grp["duration_minutes"].dropna().values
                if len(pred_durs) < 5:
                    continue
                # Baseline: all other executions (excluding this predecessor)
                other_durs = act_with_prev[act_with_prev["prev_activity"] != pred]["duration_minutes"].dropna().values
                if len(other_durs) < 5:
                    other_durs = all_durs_act  # fallback to population mean
                ratio = pred_durs.mean() / max(other_durs.mean(), 0.01)
                p_val = _test_duration_diff(pred_durs, other_durs)
                # Require: p < 0.05 AND at least 20% practical difference
                if p_val >= 0.05 or (ratio < 1.20 and ratio > 0.80):
                    continue
                eff_ratio = ratio if ratio >= 1 else 1 / max(ratio, 0.001)
                direction = "longer" if ratio > 1 else "shorter"
                records.append({
                    "activity": act,
                    "condition_type": "preceded_by",
                    "condition_value": pred,
                    "conditional_mean_min": round(pred_durs.mean(), 1),
                    "baseline_mean_min": round(other_durs.mean(), 1),
                    "count": int(len(pred_durs)),
                    "ratio": round(ratio, 4), "direction": "negative" if ratio >= 1 else "positive",
                    "rule_text": (
                        f"If '{act}' is preceded by '{pred}' → "
                        f"{pred_durs.mean():.0f} min avg ({eff_ratio:.1f}x {direction} than {other_durs.mean():.0f} min baseline, p={p_val:.3f})"
                    ),
                })

    if not records:
        return empty
    df = pd.DataFrame(records)
    df["_eff_ratio"] = df["ratio"].apply(lambda r: r if r >= 1 else 1 / max(r, 0.001))
    return df.sort_values("_eff_ratio", ascending=False).drop(columns=["_eff_ratio"]).reset_index(drop=True)


def _resource_routing_rules(traces: pd.DataFrame) -> pd.DataFrame:
    """
    Rule type: RESOURCE ROUTING
    Form: "If 'A' precedes 'B' → B is performed by SID X in Y% of cases (Rx more likely than Z% baseline)"

    For each (activity, predecessor) pair, surfaces the dominant resource when that predecessor
    is observed — only when that dominance is significantly above the baseline distribution.
    """
    empty = pd.DataFrame(columns=[
        "activity", "predecessor", "resource",
        "conditional_pct", "baseline_pct", "count", "ratio", "rule_text",
    ])

    if "resource" not in traces.columns:
        return empty

    sort_col = "start_time" if "start_time" in traces.columns else None
    df = traces.sort_values(["case_id", sort_col] if sort_col else ["case_id"]).copy()
    df["prev_activity"] = df.groupby("case_id")["activity"].shift(1)

    top_acts = traces["activity"].value_counts().head(25).index.tolist()

    records = []
    for act in top_acts:
        act_rows = df[(df["activity"] == act)].dropna(subset=["prev_activity", "resource"])
        if len(act_rows) < 30:
            continue

        for pred, pred_group in act_rows.groupby("prev_activity"):
            if len(pred_group) < 5:
                continue

            pred_res_dist = pred_group["resource"].value_counts(normalize=True)
            top_resource = pred_res_dist.index[0]
            cond_pct = float(pred_res_dist.iloc[0])
            rest = act_rows[act_rows["prev_activity"] != pred]
            if len(rest) < 5:
                continue
            base_pct = max(float(rest["resource"].eq(top_resource).mean()), 0.001)
            ratio = cond_pct / base_pct

            # Statistical test: is this resource concentration significantly different?
            k1 = int((pred_group["resource"] == top_resource).sum())
            n1 = len(pred_group)
            k2 = int((act_rows["resource"] == top_resource).sum()) - k1
            n2 = len(act_rows) - n1
            p_val = _test_proportion_diff(k1, n1, k2, max(n2, 1))

            # Require: p < 0.05 AND at least 20% practical increase AND ≥20% absolute share
            if p_val >= 0.05 or ratio < 1.20 or cond_pct < 0.20:
                continue

            direction = "more likely" if ratio > 1 else "less likely"
            records.append({
                "activity": act,
                "predecessor": pred,
                "resource": top_resource,
                "conditional_pct": round(cond_pct * 100, 1),
                "baseline_pct": round(base_pct * 100, 1),
                "count": int(len(pred_group)),
                "ratio": round(ratio, 4), "direction": "negative" if ratio >= 1 else "positive",
                "rule_text": (
                    f"If '{pred}' precedes '{act}' → performed by {top_resource} "
                    f"in {cond_pct*100:.0f}% of cases "
                    f"({ratio:.1f}x {direction} than {base_pct*100:.0f}% baseline, p={p_val:.3f})"
                ),
            })

    if not records:
        return empty
    df = pd.DataFrame(records)
    df["_eff_ratio"] = df["ratio"].apply(lambda r: r if r >= 1 else 1 / max(r, 0.001))
    return df.sort_values("_eff_ratio", ascending=False).drop(columns=["_eff_ratio"]).head(100).reset_index(drop=True)


def _conditional_rework_rules(traces: pd.DataFrame) -> pd.DataFrame:
    """
    Rule type: REWORK
    Forms:
      - "If A is preceded by B → rework rate is X% (vs Y% baseline)"
      - "If A is performed by resource R → rework rate is X% (vs Y% baseline)"
    """
    empty = pd.DataFrame(columns=[
        "activity", "condition_type", "condition_value",
        "conditional_rework_rate_pct", "baseline_rework_rate_pct",
        "count", "ratio", "rule_text"
    ])

    act_per_case = traces.groupby(["case_id", "activity"]).size().reset_index(name="n")
    act_per_case["has_rework"] = (act_per_case["n"] > 1).astype(int)

    baseline = (
        act_per_case.groupby("activity")["has_rework"]
        .agg(baseline_rate="mean", total="count")
        .reset_index()
    )
    baseline = baseline[baseline["total"] >= 30]

    sort_col = "start_time" if "start_time" in traces.columns else None
    if sort_col:
        traces_s = traces.sort_values(["case_id", sort_col]).copy()
    else:
        traces_s = traces.copy()
    traces_s["prev_activity"] = traces_s.groupby("case_id")["activity"].shift(1)

    traces_rw = traces_s.merge(
        act_per_case[["case_id", "activity", "has_rework"]],
        on=["case_id", "activity"], how="left"
    )

    records = []
    for _, base_row in baseline.iterrows():
        act = base_row["activity"]
        base_rate = base_row["baseline_rate"]
        if base_rate <= 0:
            continue

        # The exposure is the first execution, before the rework outcome.
        act_t = traces_rw[traces_rw["activity"] == act].drop_duplicates("case_id")

        # ── Rule 1: preceded-by triggers rework ───────────────────────────
        with_prev = act_t.dropna(subset=["prev_activity"])
        for pred, grp in with_prev.groupby("prev_activity"):
            if len(grp) < 5:
                continue
            k1, n1 = int(grp["has_rework"].sum()), len(grp)
            # Baseline: complement (all other predecessors)
            other = with_prev[with_prev["prev_activity"] != pred]
            k2, n2 = int(other["has_rework"].sum()), len(other)
            if n2 < 5:
                continue
            p_val = _test_proportion_diff(k1, n1, k2, n2)
            reference_rate = k2 / n2
            ratio = (k1 / n1) / max(reference_rate, 0.001)
            # Require: p < 0.05 AND ≥20% practical difference in rate
            if p_val >= 0.05 or (ratio < 1.20 and ratio > 0.80):
                continue
            cond_rate = k1 / n1
            eff_ratio_rw2 = ratio if ratio >= 1 else 1 / max(ratio, 0.001)
            direction = "higher" if ratio > 1 else "lower"
            records.append({
                "activity": act,
                "condition_type": "preceded_by",
                "condition_value": pred,
                "conditional_rework_rate_pct": round(cond_rate * 100, 1),
                "baseline_rework_rate_pct": round(reference_rate * 100, 1),
                "count": n1,
                "ratio": round(ratio, 4), "direction": "negative" if ratio >= 1 else "positive",
                "rule_text": (
                    f"If '{act}' follows '{pred}' → rework rate is "
                    f"{cond_rate*100:.0f}% ({eff_ratio_rw2:.1f}x {direction} than {reference_rate*100:.0f}% baseline, p={p_val:.3f})"
                ),
            })

        # ── Rule 2: resource triggers rework ──────────────────────────────
        if "resource" in act_t.columns:
            for res, grp in act_t.groupby("resource"):
                if len(grp) < 5:
                    continue
                k1, n1 = int(grp["has_rework"].sum()), len(grp)
                # Baseline: all other resources
                other = act_t[act_t["resource"] != res]
                k2, n2 = int(other["has_rework"].sum()), len(other)
                if n2 < 5:
                    continue
                p_val = _test_proportion_diff(k1, n1, k2, n2)
                reference_rate = k2 / n2
                ratio = (k1 / n1) / max(reference_rate, 0.001)
                if p_val >= 0.05 or (ratio < 1.20 and ratio > 0.80):
                    continue
                cond_rate = k1 / n1
                eff_ratio_rw3 = ratio if ratio >= 1 else 1 / max(ratio, 0.001)
                direction = "higher" if ratio > 1 else "lower"
                records.append({
                    "activity": act,
                    "condition_type": "resource",
                    "condition_value": res,
                    "conditional_rework_rate_pct": round(cond_rate * 100, 1),
                    "baseline_rework_rate_pct": round(reference_rate * 100, 1),
                    "count": n1,
                    "ratio": round(ratio, 4), "direction": "negative" if ratio >= 1 else "positive",
                    "rule_text": (
                        f"If '{act}' is done by {res} → rework rate is "
                        f"{cond_rate*100:.0f}% ({eff_ratio_rw3:.1f}x {direction} than {reference_rate*100:.0f}% baseline, p={p_val:.3f})"
                    ),
                })

    if not records:
        return empty
    df = pd.DataFrame(records)
    df["_eff_ratio"] = df["ratio"].apply(lambda r: r if r >= 1 else 1 / max(r, 0.001))
    return df.sort_values("_eff_ratio", ascending=False).drop(columns=["_eff_ratio"]).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# SIMULATION CALIBRATION HELPERS
# ══════════════════════════════════════════════════════════════════════════════


def _conditional_routing_rules(
    traces: pd.DataFrame,
    min_branch_pct: float = 0.0,
    min_branch_count: int = 2,
    p_threshold: float = 0.05,
    min_effect: float = 0.20,   # minimum 20% practical shift in branch probability
) -> pd.DataFrame:
    """Discover how predecessor activity or resource shifts branch probabilities
    at decision points, using Fisher's exact / chi-squared test.

    Only surfaces rules where p < p_threshold AND ≥20% practical shift.
    """
    empty = pd.DataFrame(columns=[
        "gateway_activity", "branch_activity", "condition_type", "condition_value",
        "baseline_pct", "conditional_pct", "ratio", "count", "rule_text", "severity",
    ])

    sort_col = "start_time" if "start_time" in traces.columns else None
    if sort_col is None:
        return empty

    has_resource = "resource" in traces.columns and traces["resource"].notna().any()

    df = traces.sort_values(["case_id", sort_col]).copy()
    df["preceding_act"] = df.groupby("case_id")["activity"].shift(1)
    df["next_act"] = df.groupby("case_id")["activity"].shift(-1)
    if has_resource:
        df["resource_val"] = df["resource"].astype(str).where(df["resource"].notna(), None)

    # Identify decision points
    seq = df.dropna(subset=["next_act"]).copy()
    from_totals = seq.groupby("activity").size().reset_index(name="total")
    branch_counts = seq.groupby(["activity", "next_act"]).size().reset_index(name="cnt")
    branch_counts = branch_counts.merge(from_totals, on="activity")
    branch_counts["pct"] = branch_counts["cnt"] / branch_counts["total"] * 100
    significant = branch_counts[(branch_counts["cnt"] >= min_branch_count)
                                & (branch_counts["pct"] >= min_branch_pct)]
    gateways = significant.groupby("activity").filter(lambda g: len(g) >= 2)
    gateway_acts = set(gateways["activity"].unique())

    if not gateway_acts:
        return empty

    records = []
    min_ratio = 1.0 + min_effect

    for gw in gateway_acts:
        gw_rows = seq[seq["activity"] == gw].copy()
        total_gw = len(gw_rows)
        if total_gw < 10:
            continue

        baseline = (gw_rows.groupby("next_act").size() / total_gw * 100).round(1).to_dict()
        sig_branches = {b: p for b, p in baseline.items() if p > 0 and p >= min_branch_pct}
        if len(sig_branches) < 2:
            continue

        def _test_condition(col: str, val, label: str):
            subset = gw_rows[gw_rows[col] == val]
            n = len(subset)
            if n < 5:
                return
            rest = gw_rows[gw_rows[col] != val]
            n_rest = len(rest)
            if n_rest < 5:
                return
            cond_dist = (subset.groupby("next_act").size() / n * 100).round(1).to_dict()
            for branch, base_pct in sig_branches.items():
                cond_pct = cond_dist.get(branch, 0.0)
                # Effect size and test both compare against the other cases.
                base_pct = float((rest["next_act"] == branch).mean() * 100)
                ratio = cond_pct / max(base_pct, 0.1)
                eff_ratio = ratio if ratio >= 1 else 1 / max(ratio, 0.001)
                if eff_ratio < min_ratio:
                    continue
                # Chi-squared / Fisher test on the 2×2 contingency for this branch
                k1 = int((subset["next_act"] == branch).sum())
                k2 = int((rest["next_act"] == branch).sum())
                p_val = _test_proportion_diff(k1, n, k2, n_rest)
                if p_val >= p_threshold:
                    continue
                direction = "more" if ratio > 1 else "less"
                sev = "red" if eff_ratio >= 3 else "orange"
                records.append({
                    "gateway_activity": gw,
                    "branch_activity":  branch,
                    "condition_type":   col.replace("_val", "").replace("preceding_act", "predecessor"),
                    "condition_value":  label,
                    "baseline_pct":     round(base_pct, 1),
                    "conditional_pct":  round(cond_pct, 1),
                    "ratio":            round(ratio, 3),
                    "count":            n,
                    "rule_text": (
                        f"After '{label}', '{gw}' routes to '{branch}' in "
                        f"{cond_pct:.0f}% of cases (baseline: {base_pct:.0f}%) — "
                        f"{eff_ratio:.1f}× {direction} likely (p={p_val:.3f})"
                    ),
                    "severity": sev,
                })
        # Test every condition while the current gateway and closure are in scope.
        for pred in gw_rows["preceding_act"].dropna().unique():
            _test_condition("preceding_act", pred, str(pred))
        if has_resource:
            for res in gw_rows["resource_val"].dropna().unique():
                _test_condition("resource_val", res, str(res))

    if not records:
        return empty

    return (
        pd.DataFrame(records)
        .sort_values(["gateway_activity", "ratio"], ascending=[True, False])
        .reset_index(drop=True)
    )
