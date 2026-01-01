"""
process_behaviour.py
────────────────────
Extracts the statistical process behaviour data used to calibrate the
simulation engine: durations, routing, resources, rework, parallel
execution, arrival rate, and working calendars.

Nothing in this module is used by the recommendations engine.
"""

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple

INSTANTANEOUS_ACTIVITY_THRESHOLD = 0.95
RESIDUAL_WAIT_CONTEXT_MIN_COUNT = 5
RESIDUAL_WAIT_SAMPLE_POINTS = 101


def extract_process_behaviour(traces: pd.DataFrame) -> Dict[str, Any]:
    """Mine all process behaviour statistics from a parsed event log.

    Returns a dict consumed directly by run_simulation() in simulation_engine.py.
    Conditional rule observations are not included here — those live in process_observations.py.
    """
    activity_durations_df = _extract_activity_durations(traces)
    seq_observations_df, start_activities, start_activity_probs, end_activities, terminal_probs = _extract_routing_probabilities(traces)
    arrival_parameters = _extract_arrival_parameters(traces)
    calendars = _learn_calendars(traces)
    resource_assignments_df = _extract_resource_assignments(traces, calendars)
    rework_parameters_df = _extract_rework_parameters(traces)
    handoff_observations_df = _extract_handoff_observations(traces)
    try:
        parallel_parameters_df, parallel_pairs_df, parallel_service_pct = _extract_parallel_parameters(traces)
    except Exception as e:
        import traceback
        print(f"[WARN] _extract_parallel_parameters failed: {e}", flush=True)
        traceback.print_exc()
        parallel_parameters_df = pd.DataFrame(columns=[
            "activity","total_count","parallel_count","parallel_rate_pct",
            "top_partner","top_partner_pct","top_partner_mean_overlap_min"])
        parallel_pairs_df = pd.DataFrame(columns=[
            "activity_a","activity_b","co_parallel_count",
            "a_parallel_with_b_pct","b_parallel_with_a_pct","mean_overlap_min"])
        parallel_service_pct = 0.0
    resource_capacity_dict = _infer_resource_capacity(traces)
    peak_concurrent_dict   = _peak_resource_concurrent(traces)
    residual_wait_parameters_df = _extract_residual_wait_parameters(
        traces, calendars, resource_capacity_dict
    )
    log_metrics = _compute_log_metrics(traces)
    log_metrics["parallel_service_pct"] = parallel_service_pct
    prefix_tree = _build_prefix_tree(traces, seq_observations_df)
    decision_points_df = _extract_decision_points(seq_observations_df)

    resource_assignments_df, resource_capacity_dict, peak_concurrent_dict, calendars = _inject_default_resource(
        traces, activity_durations_df, resource_assignments_df,
        resource_capacity_dict, peak_concurrent_dict, calendars,
    )

    return {
        "activity_durations_df":          activity_durations_df,
        "routing_probabilities_df":      seq_observations_df,
        "arrival_parameters":           arrival_parameters,
        "resource_assignments_df":      resource_assignments_df,
        "residual_wait_parameters_df": residual_wait_parameters_df,
        "rework_parameters_df":        rework_parameters_df,
        "handoff_observations_df":       handoff_observations_df,
        "parallel_parameters_df":      parallel_parameters_df,
        "parallel_pairs_df":      parallel_pairs_df,
        "decision_points_df":        decision_points_df,
        "start_activities":       start_activities,
        "start_activity_probs":   start_activity_probs,
        "end_activities":         end_activities,
        "terminal_probs":         terminal_probs,
        "resource_capacity_dict": resource_capacity_dict,
        "peak_concurrent_dict":   peak_concurrent_dict,
        "log_metrics":            log_metrics,
        "calendars":              calendars,
        "prefix_tree":            prefix_tree,
    }


def _next_work_timestamp(
    timestamp: pd.Timestamp,
    start_hour: int,
    end_hour: int,
    working_days: Optional[List[int]],
) -> pd.Timestamp:
    """Return the first timestamp at or after ``timestamp`` inside a calendar."""
    active_days = set(working_days) if working_days else set(range(7))
    if start_hour == 0 and end_hour >= 24 and len(active_days) == 7:
        return timestamp

    current = timestamp
    for _ in range(14):
        day = current.normalize()
        shift_start = day + pd.Timedelta(hours=start_hour)
        shift_end = day + pd.Timedelta(hours=end_hour)
        if day.dayofweek in active_days:
            if current < shift_start:
                return shift_start
            if current < shift_end:
                return current
        current = day + pd.Timedelta(days=1, hours=start_hour)
    return current


def _compress_wait_samples(values: List[float]) -> List[float]:
    """Keep an empirical distribution bounded in size for serialization."""
    if not values:
        return []
    ordered = np.sort(np.asarray(values, dtype=float))
    if len(ordered) <= RESIDUAL_WAIT_SAMPLE_POINTS:
        return [round(float(value), 3) for value in ordered]
    quantiles = np.linspace(0.0, 1.0, RESIDUAL_WAIT_SAMPLE_POINTS)
    return [round(float(value), 3) for value in np.quantile(ordered, quantiles)]


def _extract_residual_wait_parameters(
    traces: pd.DataFrame,
    calendars: Optional[Dict[str, Any]],
    resource_capacity_dict: Optional[Dict[str, int]],
) -> pd.DataFrame:
    """Learn unexplained enablement-to-release delays per direct transition.

    For an unambiguously sequential event, replay the observed target-resource
    calendar and capacity. Any working time between the earliest feasible start
    and the observed start is residual waiting: timers, batching, external
    responses, or other delays not produced by resource contention. Overlapping
    events are excluded because their enabling predecessor cannot be identified
    reliably from timestamps alone.
    """
    columns = [
        "from_activity", "activity", "observations",
        "positive_wait_count", "positive_wait_probability",
        "mean_positive_minutes", "std_positive_minutes",
        "median_positive_minutes", "p90_positive_minutes",
        "positive_wait_samples_min", "context_supported",
    ]
    required = {"case_id", "activity", "resource", "start_time", "end_time"}
    if not required.issubset(traces.columns):
        return pd.DataFrame(columns=columns)

    calendars = calendars or {}
    capacities = resource_capacity_dict or {}
    global_start = int(calendars.get("global_start_hour", 0))
    global_end = int(calendars.get("global_end_hour", 24))
    global_days = calendars.get("global_working_days") or list(range(7))
    resource_calendars = calendars.get("resource_calendars") or {}
    resource_days = calendars.get("resource_working_days") or {}

    valid = traces.dropna(subset=["start_time", "end_time"]).copy()
    valid = valid[valid["end_time"] >= valid["start_time"]]
    if valid.empty:
        return pd.DataFrame(columns=columns)

    # Precompute intervals during which each resource is at inferred capacity.
    # Availability queries then need only two binary searches, even for logs
    # with many overlapping artificial-resource intervals.
    full_capacity_windows: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    if "resource" in valid.columns:
        positive = valid[
            valid["resource"].notna() & (valid["end_time"] > valid["start_time"])
        ]
        for resource, group in positive.groupby("resource"):
            resource_key = str(resource)
            capacity = max(1, int(capacities.get(resource_key, 1)))
            changes: Dict[int, int] = {}
            for timestamp in group["start_time"]:
                changes[timestamp.value] = changes.get(timestamp.value, 0) + 1
            for timestamp in group["end_time"]:
                changes[timestamp.value] = changes.get(timestamp.value, 0) - 1
            current = 0
            full_start: Optional[int] = None
            windows: List[Tuple[int, int]] = []
            for point, delta in sorted(changes.items()):
                was_full = current >= capacity
                current += delta
                is_full = current >= capacity
                if not was_full and is_full:
                    full_start = point
                elif was_full and not is_full and full_start is not None:
                    if point > full_start:
                        windows.append((full_start, point))
                    full_start = None
            if windows:
                full_capacity_windows[resource_key] = (
                    np.fromiter((start for start, _ in windows), dtype=np.int64),
                    np.fromiter((end for _, end in windows), dtype=np.int64),
                )

    def resource_ready(
        candidate: pd.Timestamp, observed_start: pd.Timestamp, resource: Optional[str],
        start_hour: int, end_hour: int, days: List[int],
    ) -> pd.Timestamp:
        if not resource or resource not in full_capacity_windows:
            return candidate
        starts, ends = full_capacity_windows[resource]
        current = candidate
        while current < observed_start:
            point = current.value
            index = int(np.searchsorted(starts, point, side="right")) - 1
            if index < 0 or point >= ends[index]:
                return current
            current = _next_work_timestamp(
                pd.Timestamp(ends[index], tz=observed_start.tz),
                start_hour, end_hour, days,
            )
        return observed_start

    samples: Dict[Tuple[str, str], List[float]] = {}
    for _, group in valid.groupby("case_id"):
        ordered = group.sort_values(["start_time", "end_time"]).reset_index(drop=True)
        if len(ordered) < 2:
            continue
        running_end = ordered.iloc[0]["end_time"]
        for index in range(1, len(ordered)):
            previous = ordered.iloc[index - 1]
            current = ordered.iloc[index]
            observed_start = current["start_time"]

            # An overlap means this event belongs to a parallel branch. The log
            # alone does not reveal which fork enabled it, so do not invent wait.
            if observed_start < running_end:
                running_end = max(running_end, current["end_time"])
                continue

            enabled_at = running_end
            resource_value = current.get("resource") if "resource" in current else None
            resource = None if pd.isna(resource_value) else str(resource_value)
            # Without an observed resource, the engine uses an inter-event
            # duration fallback. Learning the same gap as residual waiting would
            # count it twice, and resource contention cannot be separated anyway.
            if resource is None:
                running_end = max(running_end, current["end_time"])
                continue
            start_hour, end_hour = resource_calendars.get(
                resource, (global_start, global_end)
            )
            days = resource_days.get(resource, global_days)
            calendar_ready = _next_work_timestamp(
                enabled_at, int(start_hour), int(end_hour), days
            )
            feasible_at = resource_ready(
                calendar_ready, observed_start, resource,
                int(start_hour), int(end_hour), days,
            )
            residual = _working_duration_minutes(
                feasible_at, observed_start, int(start_hour), int(end_hour), days
            )
            if pd.isna(residual):
                residual = 0.0
            key = (str(previous["activity"]), str(current["activity"]))
            samples.setdefault(key, []).append(max(0.0, float(residual)))
            running_end = max(running_end, current["end_time"])

    records = []
    for (from_activity, activity), values in samples.items():
        positive = [value for value in values if value > 1e-9]
        observations = len(values)
        records.append({
            "from_activity": from_activity,
            "activity": activity,
            "observations": observations,
            "positive_wait_count": len(positive),
            "positive_wait_probability": round(len(positive) / observations, 4),
            "mean_positive_minutes": round(float(np.mean(positive)), 3) if positive else 0.0,
            "std_positive_minutes": round(float(np.std(positive)), 3) if positive else 0.0,
            "median_positive_minutes": round(float(np.median(positive)), 3) if positive else 0.0,
            "p90_positive_minutes": round(float(np.percentile(positive, 90)), 3) if positive else 0.0,
            "positive_wait_samples_min": _compress_wait_samples(positive),
            "context_supported": observations >= RESIDUAL_WAIT_CONTEXT_MIN_COUNT,
        })

    if not records:
        return pd.DataFrame(columns=columns)
    return (
        pd.DataFrame(records, columns=columns)
        .sort_values(["mean_positive_minutes", "observations"], ascending=False)
        .reset_index(drop=True)
    )


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE EXTRACTION FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════
def _extract_activity_durations(traces: pd.DataFrame) -> pd.DataFrame:
    """Compute per-activity simulation durations using inter-event times.

    Uses the inter-event time between consecutive activities sorted within each
    case. For the last activity, falls back to the actual sojourn (end - start)
    when both timestamps are available.

    Column priority:
      - start_time present → sort by start_time, use inter-start times.
        Correctly handles parallel streams: parallel activities get near-zero
        duration; sequential bottlenecks get the full calendar gap.
      - only end_time present → sort by end_time, use inter-end times.
        Standard treatment for single-timestamp logs where each event records
        its completion time. The last activity is skipped (no next event and
        no start_time for sojourn fallback).
    """
    has_start = "start_time" in traces.columns
    has_end   = "end_time"   in traces.columns

    if not has_start and not has_end:
        return pd.DataFrame(columns=["activity", "mean_minutes", "std_minutes", "count"])

    ref_col = "start_time" if has_start else "end_time"

    dur_records = []
    for _, grp in traces.groupby("case_id"):
        grp_s  = grp.sort_values(ref_col)
        acts   = grp_s["activity"].tolist()
        refs   = grp_s[ref_col].tolist()
        ends   = grp_s["end_time"].tolist() if has_end else [None] * len(acts)
        starts = grp_s["start_time"].tolist() if has_start else [None] * len(acts)
        n = len(acts)
        for i in range(n):
            if i < n - 1:
                dur_min = (refs[i + 1] - refs[i]).total_seconds() / 60.0
            else:
                # last activity: use actual sojourn when both timestamps available
                if has_start and has_end and pd.notna(ends[i]) and pd.notna(starts[i]):
                    dur_min = (ends[i] - starts[i]).total_seconds() / 60.0
                else:
                    continue
            if dur_min >= 0:
                dur_records.append({"activity": acts[i], "dur": dur_min})

    if not dur_records:
        return pd.DataFrame(columns=["activity", "mean_minutes", "std_minutes", "count"])

    df = pd.DataFrame(dur_records)
    records = []
    for activity, group in df.groupby("activity"):
        durations = group["dur"]
        if len(durations) == 0:
            continue
        records.append({
            "activity": activity,
            "mean_minutes": round(float(durations.mean()), 1),
            "std_minutes": round(float(durations.std()) if len(durations) > 1 else 0.0, 1),
            "count": int(len(durations)),
        })

    return pd.DataFrame(records).sort_values("mean_minutes", ascending=False).reset_index(drop=True)


def _extract_routing_probabilities(
    traces: pd.DataFrame,
) -> Tuple[pd.DataFrame, List[str], Dict[str, float], List[str], Dict[str, float]]:
    sort_col = "start_time" if "start_time" in traces.columns else None

    transition_counts: Dict[Tuple[str, str], int] = {}
    start_counts: Dict[str, int] = {}
    end_counts: Dict[str, int] = {}
    visit_counts: Dict[str, int] = {}   # total appearances of each activity

    for case_id, group in traces.groupby("case_id"):
        acts = (
            group.sort_values(sort_col)["activity"].tolist()
            if sort_col else group["activity"].tolist()
        )
        if not acts:
            continue
        start_counts[acts[0]] = start_counts.get(acts[0], 0) + 1
        end_counts[acts[-1]] = end_counts.get(acts[-1], 0) + 1
        for act in acts:
            visit_counts[act] = visit_counts.get(act, 0) + 1
        for i in range(len(acts) - 1):
            key = (acts[i], acts[i + 1])
            transition_counts[key] = transition_counts.get(key, 0) + 1

    from_totals: Dict[str, int] = {}
    for (frm, _), cnt in transition_counts.items():
        from_totals[frm] = from_totals.get(frm, 0) + cnt

    records = []
    for (frm, to), cnt in sorted(transition_counts.items(), key=lambda x: -x[1]):
        total = from_totals.get(frm, 1)
        records.append({
            "from_activity": frm,
            "to_activity": to,
            "probability": round(cnt / total, 4),
            "count": cnt,
        })

    seq_df = pd.DataFrame(records) if records else pd.DataFrame(
        columns=["from_activity", "to_activity", "probability", "count"]
    )

    n_cases = max(traces["case_id"].nunique(), 1)
    start_activities = [
        act for act, cnt in sorted(start_counts.items(), key=lambda x: -x[1])
        if cnt > n_cases * 0.03
    ][:5]

    # Empirical start probabilities (used by simulation to sample the right variant mix)
    start_total = sum(start_counts.get(a, 0) for a in start_activities)
    start_activity_probs = {
        a: start_counts[a] / max(start_total, 1) for a in start_activities
    }
    end_activities = [
        act for act, cnt in sorted(end_counts.items(), key=lambda x: -x[1])
        if cnt > n_cases * 0.02
    ][:8]

    activities_with_outgoing = set(frm for frm, _ in transition_counts.keys())
    for act in set(end_counts.keys()):
        if act not in activities_with_outgoing and act not in end_activities:
            end_activities.append(act)

    # Probabilistic terminal probabilities: P(case ends at this activity | visits it).
    # Activities that are sometimes terminal but sometimes continue (e.g. a state that can
    # be the last recorded state for short/abandoned cases but has successors for longer ones)
    # get a terminal probability < 1.  Pure dead-ends get probability 1.
    # The simulation uses this instead of a binary end-activity hard-stop so it can reach
    # long-duration states that follow these "sometimes-terminal" activities.
    terminal_probs: Dict[str, float] = {}
    for act, n_term in end_counts.items():
        n_visits = visit_counts.get(act, n_term)
        n_out    = from_totals.get(act, 0)
        # n_term may exceed n_visits - n_out due to log noise; clamp to [0, 1]
        p_term = max(0.0, min(1.0, (n_visits - n_out) / max(n_visits, 1)))
        terminal_probs[act] = round(p_term, 4)

    return seq_df, start_activities, start_activity_probs, end_activities, terminal_probs


_NGRAM_ORDER = 5   # suffix length used for the ngram context table


def _build_prefix_tree(
    traces: pd.DataFrame,
    routing_probabilities_df: pd.DataFrame,
    min_count: int = 2,
) -> Dict[str, list]:
    """
    Build an N-gram context table keyed by the last _NGRAM_ORDER activities.

    Each key is SEP-joined last-N activities ("C|||D|||E") and the value is a
    sorted list of [next_activity, probability] pairs aggregated across all log
    cases that share that suffix.  Using a fixed-length suffix (rather than the
    full case history) gives a constant high hit-rate throughout the simulation
    instead of dropping off as simulated paths diverge from exact log paths.

    Shorter suffixes are also stored so early-case steps (< N activities so far)
    get context-appropriate transitions.

    Order-1 contexts come from routing_probabilities_df and are always retained so
    every observed directly-follows transition remains routable. Higher-order
    contexts with fewer than min_count observations are dropped, allowing
    lookup to back off to a shorter, better-supported suffix.
    """
    SEP = "|||"
    sort_col = "start_time" if "start_time" in traces.columns else None

    counts: Dict[str, Dict[str, int]] = {}
    for _, group in traces.groupby("case_id"):
        acts = (
            group.sort_values(sort_col)["activity"].tolist()
            if sort_col else group["activity"].tolist()
        )
        for i in range(len(acts) - 1):
            nxt = acts[i + 1]
            # Store higher-order suffixes; order 1 comes from routing_probabilities_df.
            for n in range(2, min(_NGRAM_ORDER, i + 1) + 1):
                start = i + 1 - n
                key = SEP.join(acts[start:i + 1])
                counts.setdefault(key, {})
                counts[key][nxt] = counts[key].get(nxt, 0) + 1

    tree: Dict[str, list] = {
        str(activity): sorted(
            [[str(row["to_activity"]), float(row["probability"])]
             for _, row in group.iterrows()],
            key=lambda item: -item[1],
        )
        for activity, group in routing_probabilities_df.groupby("from_activity")
    }
    for key, nxt_counts in counts.items():
        total = sum(nxt_counts.values())
        if total < min_count:
            continue
        tree[key] = sorted(
            [[act, round(cnt / total, 4)] for act, cnt in nxt_counts.items()],
            key=lambda x: -x[1],
        )
    return tree


def _extract_arrival_parameters(traces: pd.DataFrame) -> Dict[str, float]:
    n_cases = traces["case_id"].nunique()
    ref_col = (
        "start_time" if "start_time" in traces.columns else
        "end_time"   if "end_time"   in traces.columns else
        None
    )
    if ref_col is None or n_cases < 2:
        return {"cases_per_day": float(n_cases), "mean_inter_arrival_hours": 24.0 / max(n_cases, 1)}

    arrivals = traces.groupby("case_id")[ref_col].min().sort_values()
    total_days = (arrivals.max() - arrivals.min()).total_seconds() / 86400
    cases_per_day = n_cases / max(total_days, 1)

    return {
        "cases_per_day": round(cases_per_day, 2),
        "mean_inter_arrival_hours": round(24.0 / cases_per_day, 3),
    }


def _inject_default_resource(
    traces: pd.DataFrame,
    activity_durations_df: pd.DataFrame,
    resource_assignments_df: pd.DataFrame,
    resource_capacity_dict: Dict[str, int],
    peak_concurrent_dict: Dict[str, int],
    calendars: Dict,
) -> tuple:
    """
    For every activity in activity_durations_df that has no row in resource_assignments_df,
    add a row assigning it to the virtual resource "default" with probability 1.0.

    default inherits the calendar (start/end hour, working days) of the most-used
    real resource in the log; if no real resources exist it uses the global calendar.
    Its capacity is 1 (single slot) and peak is set to 1 so the capacity bottleneck
    detector doesn't fire for it unless genuinely saturated.

    Returns updated (resource_assignments_df, resource_capacity_dict, peak_concurrent_dict,
    calendars) — all other observations are unchanged.
    """
    SID = "default"

    # Activities already covered by some resource
    covered = set(resource_assignments_df["activity"].unique()) if not resource_assignments_df.empty else set()
    all_activities = set(activity_durations_df["activity"].unique()) if not activity_durations_df.empty else set()
    missing = all_activities - covered
    if not missing:
        return resource_assignments_df, resource_capacity_dict, peak_concurrent_dict, calendars

    # Find the most-used real resource to inherit its calendar
    if "resource" in traces.columns and traces["resource"].notna().any():
        top_res = str(traces["resource"].value_counts().idxmax())
    else:
        top_res = None

    # Duration stats for missing activities come from activity_durations_df
    dur_lookup = {}
    if not activity_durations_df.empty:
        for _, row in activity_durations_df[activity_durations_df["activity"].isin(missing)].iterrows():
            dur_lookup[row["activity"]] = (
                float(row.get("mean_minutes", 0.0)),
                float(row.get("std_minutes", 0.0)),
            )

    # Build new rows
    new_rows = []
    for act in sorted(missing):
        mean_d, std_d = dur_lookup.get(act, (0.0, 0.0))
        # Estimate count from activity_durations_df if available
        ta_row = activity_durations_df[activity_durations_df["activity"] == act] if not activity_durations_df.empty else pd.DataFrame()
        count = int(ta_row["count"].iloc[0]) if not ta_row.empty and "count" in ta_row.columns else 1
        new_rows.append({
            "activity": act,
            "resource": SID,
            "probability": 1.0,
            "mean_duration_min": round(mean_d, 1),
            "std_duration_min": round(std_d, 1),
            "duration_observed": True,
            "duration_source": "inter_event_fallback",
            "duration_observation_count": count,
            "zero_duration_share": np.nan,
            "is_instantaneous": False,
            "count": count,
        })

    if not new_rows:
        return resource_assignments_df, resource_capacity_dict, peak_concurrent_dict, calendars

    new_df = pd.DataFrame(new_rows)
    updated_ra = (
        pd.concat([resource_assignments_df, new_df], ignore_index=True)
        if not resource_assignments_df.empty
        else new_df
    )

    # Capacity: 1 slot; peak: 1 (conservative — won't over-trigger bottleneck alert)
    updated_cap = dict(resource_capacity_dict)
    updated_peak = dict(peak_concurrent_dict)
    if SID not in updated_cap:
        updated_cap[SID] = 1
        updated_peak[SID] = 1

    # Calendar: inherit from top resource, or fall back to global
    updated_cals = dict(calendars)
    if SID not in updated_cals.get("resource_calendars", {}):
        rc = dict(updated_cals.get("resource_calendars", {}))
        rd = dict(updated_cals.get("resource_working_days", {}))
        if top_res and top_res in rc:
            rc[SID] = rc[top_res]
            rd[SID] = rd.get(top_res, updated_cals.get("global_working_days", list(range(7))))
        else:
            rc[SID] = (
                updated_cals.get("global_start_hour", 0),
                updated_cals.get("global_end_hour", 24),
            )
            rd[SID] = updated_cals.get("global_working_days", list(range(7)))
        updated_cals = {**updated_cals, "resource_calendars": rc, "resource_working_days": rd}

    return updated_ra, updated_cap, updated_peak, updated_cals


def _working_duration_minutes(
    start: pd.Timestamp,
    end: pd.Timestamp,
    start_hour: int,
    end_hour: int,
    working_days: Optional[List[int]],
) -> float:
    """Return elapsed minutes inside a recurring weekly working calendar.

    Calendar windows are half-open: ``[start_hour, end_hour)``. Timestamps in
    parsed logs are UTC, so day boundaries and learned hours use the same clock.
    """
    if pd.isna(start) or pd.isna(end):
        return np.nan
    if end < start:
        return np.nan
    if end == start:
        return 0.0

    active_days = set(working_days) if working_days else set(range(7))
    if start_hour == 0 and end_hour >= 24 and len(active_days) == 7:
        return (end - start).total_seconds() / 60.0

    shift_minutes = max(end_hour - start_hour, 0) * 60.0
    if shift_minutes <= 0 or not active_days:
        return 0.0

    start_day = start.normalize()
    end_day = end.normalize()

    def _overlap_on_day(day: pd.Timestamp) -> float:
        if day.dayofweek not in active_days:
            return 0.0
        shift_start = day + pd.Timedelta(hours=start_hour)
        shift_end = day + pd.Timedelta(hours=end_hour)
        overlap_start = max(start, shift_start)
        overlap_end = min(end, shift_end)
        return max(0.0, (overlap_end - overlap_start).total_seconds() / 60.0)

    if start_day == end_day:
        return _overlap_on_day(start_day)

    total = _overlap_on_day(start_day) + _overlap_on_day(end_day)

    # Count complete days between the two boundary days in O(1) full weeks
    # plus at most six remainder days.
    middle_days = max((end_day - start_day).days - 1, 0)
    full_weeks, remainder = divmod(middle_days, 7)
    total += full_weeks * len(active_days) * shift_minutes
    day = start_day + pd.Timedelta(days=1)
    for _ in range(remainder):
        if day.dayofweek in active_days:
            total += shift_minutes
        day += pd.Timedelta(days=1)

    return total


def _extract_resource_assignments(traces: pd.DataFrame, calendars: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """Extract assignment probabilities and working-time durations per resource.

    When start and end timestamps are available, duration counts only portions of
    the event interval that overlap the resource's learned working calendar. This
    prevents closed nights and weekends from being treated as exclusive service
    time. Resources without an individual calendar use the global calendar.
    """
    output_columns = [
        "activity", "resource", "probability",
        "mean_duration_min", "std_duration_min",
        "duration_observed", "duration_source",
        "duration_observation_count", "zero_duration_share",
        "is_instantaneous", "count",
    ]
    empty = pd.DataFrame(columns=output_columns)
    if "resource" not in traces.columns:
        return empty

    counts = traces.groupby(["activity", "resource"]).size().reset_index(name="count")
    if counts.empty:
        return empty

    totals = traces.groupby("activity").size().reset_index(name="total")
    df = counts.merge(totals, on="activity")
    df["probability"] = (df["count"] / df["total"]).round(4)

    if "start_time" in traces.columns and "end_time" in traces.columns:
        calendars = calendars or {}
        global_start = int(calendars.get("global_start_hour", 0))
        global_end = int(calendars.get("global_end_hour", 24))
        global_days = calendars.get("global_working_days") or list(range(7))
        resource_calendars = calendars.get("resource_calendars") or {}
        resource_days = calendars.get("resource_working_days") or {}

        duration_rows = traces[["activity", "resource", "start_time", "end_time"]].copy()
        valid_interval = (
            duration_rows["start_time"].notna()
            & duration_rows["end_time"].notna()
            & (duration_rows["end_time"] >= duration_rows["start_time"])
        )
        duration_rows["raw_duration_seconds"] = np.nan
        duration_rows.loc[valid_interval, "raw_duration_seconds"] = (
            duration_rows.loc[valid_interval, "end_time"]
            - duration_rows.loc[valid_interval, "start_time"]
        ).dt.total_seconds()

        activity_duration_meta = (
            duration_rows.loc[valid_interval]
            .groupby("activity")["raw_duration_seconds"]
            .agg(
                activity_duration_observation_count="count",
                zero_duration_count=lambda values: int((values == 0.0).sum()),
            )
            .reset_index()
        )
        activity_duration_meta["zero_duration_share"] = (
            activity_duration_meta["zero_duration_count"]
            / activity_duration_meta["activity_duration_observation_count"]
        )
        activity_duration_meta["is_instantaneous"] = (
            activity_duration_meta["zero_duration_share"]
            >= INSTANTANEOUS_ACTIVITY_THRESHOLD
        )
        activity_duration_meta = activity_duration_meta.drop(
            columns=["zero_duration_count", "activity_duration_observation_count"]
        )

        duration_rows["working_duration_minutes"] = [
            _working_duration_minutes(
                row.start_time,
                row.end_time,
                *(resource_calendars.get(str(row.resource), (global_start, global_end))),
                resource_days.get(str(row.resource), global_days),
            )
            for row in duration_rows.itertuples(index=False)
        ]
        dur_stats = (
            duration_rows.groupby(["activity", "resource"])["working_duration_minutes"]
            .agg(
                mean_duration_min="mean",
                std_duration_min="std",
                duration_observation_count="count",
            )
            .reset_index()
        )
        # Preserve the established one-decimal observation representation without
        # turning genuinely positive sub-0.05-minute values into zero.
        for column in ["mean_duration_min", "std_duration_min"]:
            original = dur_stats[column]
            rounded = original.round(1)
            tiny_positive = (original > 0.0) & (rounded == 0.0)
            dur_stats[column] = rounded.mask(tiny_positive, original)
        df = df.merge(dur_stats, on=["activity", "resource"], how="left")
        df = df.merge(activity_duration_meta, on="activity", how="left")
        df["duration_observation_count"] = (
            df["duration_observation_count"].fillna(0).astype(int)
        )
        df["duration_observed"] = df["duration_observation_count"] > 0
        df["is_instantaneous"] = df["is_instantaneous"].fillna(False).astype(bool)
        df["duration_source"] = np.where(
            df["duration_observed"], "observed_interval", "unavailable"
        )

        # The activity-level classification deliberately treats rare positive
        # intervals as timestamp noise. Apply it to every resource used by the
        # activity, even if one resource has no valid interval of its own.
        instant_mask = df["is_instantaneous"]
        df.loc[instant_mask, "mean_duration_min"] = 0.0
        df.loc[instant_mask, "std_duration_min"] = 0.0
        df.loc[instant_mask, "duration_observed"] = True
        df.loc[instant_mask, "duration_source"] = "observed_instantaneous"

        observed_mask = df["duration_observed"]
        df.loc[observed_mask, "std_duration_min"] = (
            df.loc[observed_mask, "std_duration_min"].fillna(0.0)
        )
    else:
        # Missing timestamps mean unknown duration, not observed zero duration.
        df["mean_duration_min"] = np.nan
        df["std_duration_min"] = np.nan
        df["duration_observed"] = False
        df["duration_source"] = "unavailable"
        df["duration_observation_count"] = 0
        df["zero_duration_share"] = np.nan
        df["is_instantaneous"] = False

    df = df.drop(columns=["total"])
    return (
        df.sort_values(["activity", "count"], ascending=[True, False])
        .reset_index(drop=True)
        [output_columns]
    )


def _extract_rework_parameters(traces: pd.DataFrame) -> pd.DataFrame:
    n_cases = traces["case_id"].nunique()
    act_per_case = traces.groupby(["case_id", "activity"]).size().reset_index(name="occurrences")
    rework = act_per_case[act_per_case["occurrences"] > 1].copy()
    rework["extra_loops"] = rework["occurrences"] - 1

    if rework.empty:
        return pd.DataFrame(columns=[
            "activity", "cases_with_rework", "cases_visiting", "total_cases",
            "rework_rate_pct", "rework_rate_given_visit_pct", "mean_extra_loops", "max_loops"
        ])

    summary = (
        rework.groupby("activity")
        .agg(cases_with_rework=("case_id", "count"),
             mean_extra_loops=("extra_loops", "mean"),
             max_loops=("extra_loops", "max"))
        .reset_index()
    )

    # cases_visiting = distinct cases that had at least one execution of the activity
    cases_visiting = (
        act_per_case.groupby("activity")["case_id"].count().reset_index(name="cases_visiting")
    )
    summary = summary.merge(cases_visiting, on="activity", how="left")

    summary["rework_rate_pct"] = (summary["cases_with_rework"] / n_cases * 100).round(1)
    # Probability of rework given the case actually visits the activity — used by rework injection
    summary["rework_rate_given_visit_pct"] = (
        summary["cases_with_rework"] / summary["cases_visiting"].clip(lower=1) * 100
    ).round(1)
    summary["mean_extra_loops"] = summary["mean_extra_loops"].round(2)
    summary["total_cases"] = n_cases

    return (
        summary.sort_values("rework_rate_pct", ascending=False)
        .reset_index(drop=True)
        [["activity", "cases_with_rework", "cases_visiting", "total_cases",
          "rework_rate_pct", "rework_rate_given_visit_pct", "mean_extra_loops", "max_loops"]]
    )




def _extract_handoff_observations(traces: pd.DataFrame) -> pd.DataFrame:
    empty = pd.DataFrame(columns=[
        "from_source", "from_activity", "to_source", "to_activity", "count", "probability"
    ])
    if "source" not in traces.columns:
        return empty

    sort_col = "start_time" if "start_time" in traces.columns else None
    df = traces.sort_values(["case_id", sort_col] if sort_col else ["case_id"]).copy()
    df["next_source"] = df.groupby("case_id")["source"].shift(-1)
    df["next_activity"] = df.groupby("case_id")["activity"].shift(-1)

    cross = df[(df["source"] != df["next_source"]) & df["next_source"].notna()].copy()
    if cross.empty:
        return empty

    counts = (
        cross.groupby(["source", "activity", "next_source", "next_activity"])
        .size().reset_index(name="count")
    )
    counts.columns = ["from_source", "from_activity", "to_source", "to_activity", "count"]
    totals = counts.groupby(["from_source", "from_activity"])["count"].sum().reset_index(name="total")
    counts = counts.merge(totals, on=["from_source", "from_activity"])
    counts["probability"] = (counts["count"] / counts["total"]).round(4)
    return counts.drop(columns=["total"]).sort_values("count", ascending=False).reset_index(drop=True)


def _extract_parallel_parameters(traces: pd.DataFrame):
    """Detect parallel activity execution using timestamp overlap.

    Two activity occurrences in the same case are parallel when:
        start_time[j] < end_time[i]   (j starts before i finishes)

    Returns (parallel_activity_df, parallel_pairs_df).

    parallel_activity_df columns:
      activity, total_count, parallel_count, parallel_rate_pct,
      top_partner, top_partner_pct, top_partner_mean_overlap_min

    parallel_pairs_df columns:
      activity_a, activity_b, co_parallel_count,
      a_parallel_with_b_pct, b_parallel_with_a_pct, mean_overlap_min
    """
    empty_act = pd.DataFrame(columns=[
        "activity", "total_count", "parallel_count", "parallel_rate_pct",
        "top_partner", "top_partner_pct", "top_partner_mean_overlap_min",
    ])
    empty_pairs = pd.DataFrame(columns=[
        "activity_a", "activity_b", "co_parallel_count",
        "a_parallel_with_b_pct", "b_parallel_with_a_pct", "mean_overlap_min",
    ])
    if "start_time" not in traces.columns or "end_time" not in traces.columns:
        return empty_act, empty_pairs, 0.0

    sub = traces.dropna(subset=["start_time", "end_time"]).copy()
    # Drop zero-duration events — they have no real execution window and produce
    # spurious 100% parallel rates with 0 min overlap when paired with anything.
    sub = sub[sub["end_time"] > sub["start_time"]]
    if sub.empty:
        return empty_act, empty_pairs, 0.0

    total_counts: Dict[str, int] = sub.groupby("activity").size().to_dict()

    # parallel_occ_counts[act]         = # of act's occurrences that overlap with ≥1 other activity
    # pair_co_occ_counts[(act_a, act_b)] = # of act_a's occurrences that overlap with ≥1 act_b occurrence
    # pair_overlap_durs[(act_a, act_b)]  = list of per-instance overlap durations (minutes)
    from collections import defaultdict
    parallel_occ_counts: Dict[str, int] = defaultdict(int)
    pair_co_occ_counts: Dict[tuple, int] = defaultdict(int)
    pair_overlap_durs: Dict[tuple, list] = defaultdict(list)

    total_service_min   = 0.0
    parallel_service_min = 0.0

    for _, grp in sub.groupby("case_id"):
        if len(grp) < 2:
            continue
        grp_s = grp.sort_values("start_time").reset_index(drop=True)
        acts   = grp_s["activity"].tolist()
        starts = grp_s["start_time"].tolist()
        ends   = grp_s["end_time"].tolist()
        n = len(acts)

        # For each event track which distinct partner-activities it overlaps with
        is_parallel = [False] * n
        partners: List[set] = [set() for _ in range(n)]

        for i in range(n):
            end_i = ends[i]
            if pd.isna(end_i):
                continue
            for j in range(i + 1, n):
                start_j = starts[j]
                if pd.isna(start_j):
                    continue
                if start_j >= end_i:
                    break  # sorted by start → no further j can overlap with i
                # Genuine overlap
                is_parallel[i] = True
                is_parallel[j] = True
                partners[i].add(acts[j])
                partners[j].add(acts[i])
                # Overlap window duration
                end_j = ends[j]
                if not pd.isna(end_j):
                    overlap_start = max(starts[i], start_j)
                    overlap_end   = min(end_i, end_j)
                    dur = (overlap_end - overlap_start).total_seconds() / 60.0
                    if dur > 0:
                        pair_overlap_durs[(acts[i], acts[j])].append(dur)
                        pair_overlap_durs[(acts[j], acts[i])].append(dur)

        for i in range(n):
            if is_parallel[i]:
                parallel_occ_counts[acts[i]] += 1
                for p in partners[i]:
                    pair_co_occ_counts[(acts[i], p)] += 1
            # Accumulate service-time sums for log-level ratio
            if not pd.isna(starts[i]) and not pd.isna(ends[i]):
                dur_i = (ends[i] - starts[i]).total_seconds() / 60.0
                if dur_i > 0:
                    total_service_min += dur_i
                    if is_parallel[i]:
                        parallel_service_min += dur_i

    if not parallel_occ_counts:
        return empty_act, empty_pairs, 0.0

    # ── per-activity DataFrame ────────────────────────────────────────────────
    act_records = []
    for act, total in total_counts.items():
        par_count = parallel_occ_counts.get(act, 0)
        if par_count == 0:
            continue
        par_rate = par_count / total * 100

        # All partners for this activity, sorted by co-occurrence count
        partner_entries = [
            {
                "activity": p_act,
                "count": pair_co_occ_counts[(act, p_act)],
                "co_pct": round(pair_co_occ_counts[(act, p_act)] / par_count * 100, 1),
                "mean_overlap_min": round(
                    sum(pair_overlap_durs[(act, p_act)]) / len(pair_overlap_durs[(act, p_act)]), 1
                ) if pair_overlap_durs[(act, p_act)] else 0.0,
            }
            for p_act in set(p for (a, p) in pair_co_occ_counts if a == act)
        ]
        partner_entries.sort(key=lambda x: -x["count"])
        top = partner_entries[0] if partner_entries else None

        act_records.append({
            "activity":                    act,
            "total_count":                 int(total),
            "parallel_count":              int(par_count),
            "parallel_rate_pct":           round(par_rate, 1),
            "top_partner":                 top["activity"] if top else None,
            "top_partner_pct":             top["co_pct"] if top else 0.0,
            "top_partner_mean_overlap_min": top["mean_overlap_min"] if top else 0.0,
        })

    act_df = (
        pd.DataFrame(act_records)
        .sort_values("parallel_rate_pct", ascending=False)
        .reset_index(drop=True)
    )

    # ── per-pair DataFrame ────────────────────────────────────────────────────
    seen: set = set()
    pair_records = []
    for (a, b), cnt in pair_co_occ_counts.items():
        key = tuple(sorted([a, b]))
        if key in seen:
            continue
        seen.add(key)
        a_s, b_s = key
        # Use both directions from pair_co_occ_counts; the sorted key may not match
        # the stored direction, so look up both and use the one that exists.
        cnt_ab = pair_co_occ_counts.get((a_s, b_s), 0)
        cnt_ba = pair_co_occ_counts.get((b_s, a_s), 0)
        # Use whichever overlap duration list is non-empty
        durs = pair_overlap_durs.get((a_s, b_s), []) or pair_overlap_durs.get((b_s, a_s), [])
        co_count = cnt_ab + cnt_ba  # total co-parallel occurrences across both directions
        pair_records.append({
            "activity_a":            a_s,
            "activity_b":            b_s,
            "co_parallel_count":     int(max(cnt_ab, cnt_ba)),
            "a_parallel_with_b_pct": round(cnt_ab / max(total_counts.get(a_s, 1), 1) * 100, 1),
            "b_parallel_with_a_pct": round(cnt_ba / max(total_counts.get(b_s, 1), 1) * 100, 1),
            "mean_overlap_min":      round(sum(durs) / len(durs), 1) if durs else 0.0,
        })

    pair_df = (
        pd.DataFrame(pair_records)
        .sort_values("co_parallel_count", ascending=False)
        .reset_index(drop=True)
        if pair_records else empty_pairs
    )

    par_service_pct = round(parallel_service_min / total_service_min * 100, 1) if total_service_min > 0 else 0.0
    return act_df, pair_df, par_service_pct


def _extract_decision_points(
    seq_observations_df: pd.DataFrame,
    min_branch_pct: float = 5.0,
    min_branch_count: int = 2,
) -> pd.DataFrame:
    """Identify XOR decision points from the sequence transition probabilities.

    An XOR gateway is an activity that branches into ≥2 successors, each
    carrying at least min_branch_pct% of the outgoing probability mass.
    The result describes every branch of every detected gateway.

    Columns:
      gateway_activity  — the activity where the decision happens
      branch_activity   — a downstream successor
      probability       — P(branch_activity | gateway_activity)  [0-1]
      probability_pct   — same as percentage
      count             — raw observed transitions
      is_dominant       — True for the branch with the highest probability
    """
    empty = pd.DataFrame(columns=[
        "gateway_activity", "branch_activity",
        "probability", "probability_pct", "count", "is_dominant",
    ])
    if seq_observations_df is None or seq_observations_df.empty:
        return empty

    records = []
    for gw, grp in seq_observations_df.groupby("from_activity"):
        # Keep only branches above the threshold
        branches = grp[grp["probability"] * 100 >= min_branch_pct].copy()
        if len(branches) < min_branch_count:
            continue  # not a real decision point
        dominant = branches["probability"].idxmax()
        for _, row in branches.iterrows():
            records.append({
                "gateway_activity": gw,
                "branch_activity":  str(row["to_activity"]),
                "probability":      round(float(row["probability"]), 4),
                "probability_pct":  round(float(row["probability"]) * 100, 1),
                "count":            int(row.get("count", 0)),
                "is_dominant":      (row.name == dominant),
            })

    if not records:
        return empty

    return (
        pd.DataFrame(records)
        .sort_values(["gateway_activity", "probability"], ascending=[True, False])
        .reset_index(drop=True)
    )


def _infer_resource_capacity(traces: pd.DataFrame) -> Dict[str, int]:
    """
    Estimate simulation capacity per resource from the empirical concurrency distribution.

    Uses a sweep-line over (start_time, end_time) intervals to build a time-weighted
    histogram of concurrent case counts.  The 90th percentile of that distribution
    (counting only busy time, i.e. concurrency > 0) is returned as capacity.

    Properties vs. the previous Little's Law approach:
    - Result is always <= the observed peak (no physically impossible values).
    - A resource at p90 capacity operates at or above capacity ~10% of its busy
      time, generating realistic queue wait times without systematic over-saturation.
    - Robust to calendar-estimation errors because it reads concurrency directly
      from the timestamps rather than dividing by an inferred working period.
    """
    if (
        "resource" not in traces.columns
        or "start_time" not in traces.columns
        or "end_time" not in traces.columns
    ):
        return {}

    clean = traces.dropna(subset=["start_time", "end_time", "resource"])
    clean = clean[clean["end_time"] > clean["start_time"]]
    if clean.empty:
        return {}

    result: Dict[str, int] = {}
    for resource, grp in clean.groupby("resource"):
        events = sorted(
            [(t, 1) for t in grp["start_time"]] + [(t, -1) for t in grp["end_time"]],
            key=lambda x: x[0],
        )

        # Build (concurrency, duration_seconds) intervals for every busy period.
        intervals: List[Tuple[int, float]] = []
        current = 0
        prev_t = None
        for t, delta in events:
            if prev_t is not None and current > 0:
                dur = (t - prev_t).total_seconds()
                if dur > 0:
                    intervals.append((current, dur))
            current += delta
            prev_t = t

        if not intervals:
            result[str(resource)] = 1
            continue

        # Time-weighted 90th percentile: sort by concurrency, accumulate duration
        # until we've covered 90% of total busy time.
        total_dur = sum(d for _, d in intervals)
        cum = 0.0
        p90 = intervals[0][0]  # initialise; overwritten in loop
        for conc, dur in sorted(intervals, key=lambda x: x[0]):
            cum += dur
            p90 = conc
            if cum / total_dur >= 0.90:
                break

        result[str(resource)] = max(1, int(p90))

    return result


def _peak_resource_concurrent(traces: pd.DataFrame) -> Dict[str, int]:
    """Return the historical peak concurrent case count per resource (for display only)."""
    if (
        "resource" not in traces.columns
        or "start_time" not in traces.columns
        or "end_time" not in traces.columns
    ):
        return {}

    clean = traces.dropna(subset=["start_time", "end_time", "resource"])
    clean = clean[clean["end_time"] > clean["start_time"]]
    if clean.empty:
        return {}

    peaks: Dict[str, int] = {}
    for resource, grp in clean.groupby("resource"):
        events = sorted(
            [(t, 1) for t in grp["start_time"]] + [(t, -1) for t in grp["end_time"]],
            key=lambda x: x[0],
        )
        current = peak = 0
        for _, delta in events:
            current += delta
            if current > peak:
                peak = current
        peaks[str(resource)] = int(peak)

    return peaks


def _compute_log_metrics(traces: pd.DataFrame) -> Dict[str, Any]:
    """Compute actual cycle time and rework metrics directly from the event log."""
    n_cases = traces["case_id"].nunique()
    out: Dict[str, Any] = {"cases_count": int(n_cases)}

    # Path length (activities per case) — drives the step cap in the simulation
    path_lengths = traces.groupby("case_id")["activity"].count()
    out["avg_path_length"]  = round(float(path_lengths.mean()), 1)
    out["p95_path_length"]  = int(np.percentile(path_lengths, 95))
    out["max_path_length"]  = int(path_lengths.max())

    has_start = "start_time" in traces.columns
    has_end   = "end_time"   in traces.columns

    if has_start and has_end:
        log_start = traces["start_time"].min()
        log_end   = traces["end_time"].max()
        if pd.notnull(log_start) and pd.notnull(log_end) and log_end > log_start:
            out["log_span_days"] = round((log_end - log_start).total_seconds() / 86400, 1)
        if pd.notnull(log_start):
            out["log_start_iso"] = log_start.isoformat()
        if pd.notnull(log_end):
            out["log_end_iso"] = log_end.isoformat()

        case_times = traces.groupby("case_id").agg(
            case_start=("start_time", "min"),
            case_end=("end_time", "max"),
        )
        cycle_hours = (
            (case_times["case_end"] - case_times["case_start"])
            .dt.total_seconds()
            .div(3600)
            .dropna()
        )
        cycle_hours = cycle_hours[cycle_hours >= 0]
        if len(cycle_hours) > 0:
            out["avg_cycle_time_hours"]    = float(np.mean(cycle_hours))
            out["median_cycle_time_hours"] = float(np.median(cycle_hours))
            out["p75_cycle_time_hours"]    = float(np.percentile(cycle_hours, 75))
            out["p90_cycle_time_hours"]    = float(np.percentile(cycle_hours, 90))
            out["cycle_hours_list"]        = cycle_hours.tolist()

    elif has_end:
        # Single-timestamp log: approximate cycle time as span between first and last
        # event completion per case (underestimates by the first activity's duration).
        log_start = traces["end_time"].min()
        log_end   = traces["end_time"].max()
        if pd.notnull(log_start) and pd.notnull(log_end) and log_end > log_start:
            out["log_span_days"] = round((log_end - log_start).total_seconds() / 86400, 1)
        if pd.notnull(log_start):
            out["log_start_iso"] = log_start.isoformat()
        if pd.notnull(log_end):
            out["log_end_iso"] = log_end.isoformat()

        case_times = traces.groupby("case_id").agg(
            case_start=("end_time", "min"),
            case_end=("end_time", "max"),
        )
        cycle_hours = (
            (case_times["case_end"] - case_times["case_start"])
            .dt.total_seconds()
            .div(3600)
            .dropna()
        )
        cycle_hours = cycle_hours[cycle_hours >= 0]
        if len(cycle_hours) > 0:
            out["avg_cycle_time_hours"]    = float(np.mean(cycle_hours))
            out["median_cycle_time_hours"] = float(np.median(cycle_hours))
            out["p75_cycle_time_hours"]    = float(np.percentile(cycle_hours, 75))
            out["p90_cycle_time_hours"]    = float(np.percentile(cycle_hours, 90))
            out["cycle_hours_list"]        = cycle_hours.tolist()

    act_per_case = traces.groupby(["case_id", "activity"]).size()
    rework_cases = int((act_per_case > 1).groupby(level="case_id").any().sum())
    out["rework_rate_pct"] = round(rework_cases / max(n_cases, 1) * 100, 1)
    out["rework_cases"]    = rework_cases

    return out


def _learn_calendars(traces: pd.DataFrame) -> Dict[str, Any]:
    """
    Infer active-hour windows and working days from when activities actually occur.
    Returns a global window + per-resource windows + working_days lists.
    """
    empty: Dict[str, Any] = {
        "global_start_hour": 0,
        "global_end_hour": 24,
        "global_working_days": list(range(7)),
        "resource_calendars": {},
        "resource_working_days": {},
    }
    if "start_time" not in traces.columns:
        if "end_time" not in traces.columns:
            return empty
        ref_col = "end_time"
    else:
        ref_col = "start_time"

    def _window(hour_series: pd.Series) -> Tuple[int, int]:
        hc = hour_series.dt.hour.value_counts()
        if hc.empty:
            return 0, 24
        pk = int(hc.max())
        active = sorted(int(h) for h, c in hc.items() if int(c) >= pk * 0.05)
        if not active or len(active) >= 22:   # 22+ active hours = effectively 24/7
            return 0, 24
        return min(active), max(active) + 1

    def _working_days(dt_series: pd.Series) -> List[int]:
        """Return sorted list of active weekdays (0=Mon … 6=Sun), or all 7 if effectively 7-day."""
        dc = dt_series.dt.dayofweek.value_counts()
        if dc.empty:
            return list(range(7))
        pk = int(dc.max())
        active = sorted(int(d) for d, c in dc.items() if int(c) >= pk * 0.10)
        if len(active) >= 6:   # 6+ active days = treat as 7-day
            return list(range(7))
        return active

    g_start, g_end = _window(traces[ref_col])
    g_days = _working_days(traces[ref_col])

    resource_cals: Dict[str, Tuple[int, int]] = {}
    resource_days: Dict[str, List[int]] = {}
    if "resource" in traces.columns:
        for res, grp in traces.groupby("resource"):
            if len(grp) < 30:
                continue
            rs, re = _window(grp[ref_col])
            rd = _working_days(grp[ref_col])
            resource_cals[str(res)] = (rs, re)
            resource_days[str(res)] = rd

    return {
        "global_start_hour": g_start,
        "global_end_hour": g_end,
        "global_working_days": g_days,
        "resource_calendars": resource_cals,
        "resource_working_days": resource_days,
    }


# ── Case Attribute Behaviour ───────────────────────────────────────────────────

def extract_case_attribute_behaviour(
    traces: pd.DataFrame,
    case_df: pd.DataFrame,
    case_id_col: str,
    min_segment_cases: int = 10,
    max_cardinality: int = 20,
) -> Dict[str, Any]:
    """Mine per-attribute value distributions for segment-aware simulation.

    Returns a dict keyed by attribute name, each containing a list of
    {value, share, n_cases} objects sorted by share descending.

    Example:
      {
        "region": {
          "n_total": 10782,
          "values": [
            {"value": "Region 10", "share": 0.560, "n_cases": 6040},
            {"value": "Region 1",  "share": 0.355, "n_cases": 3833},
            {"value": "Region 8",  "share": 0.084, "n_cases":  909},
          ]
        },
        ...
      }
    """
    skip_cols = {case_id_col, "case_id"}
    attr_cols = [
        c for c in case_df.columns
        if c not in skip_cols and 1 < case_df[c].nunique() <= max_cardinality
    ]
    if not attr_cols:
        return {}

    n_total = int(case_df[case_id_col].nunique()) if case_id_col in case_df.columns else len(case_df)

    result: Dict[str, Any] = {}
    for attr_col in attr_cols:
        counts = case_df[attr_col].dropna().value_counts()
        values = []
        for val, cnt in counts.items():
            if cnt < min_segment_cases:
                continue
            values.append({
                "value":   str(val),
                "share":   round(float(cnt) / max(n_total, 1), 4),
                "n_cases": int(cnt),
            })
        if not values:
            continue
        # Normalise shares so they sum to 1.0
        total = sum(v["share"] for v in values)
        if total > 0:
            for v in values:
                v["share"] = round(v["share"] / total, 4)
        result[str(attr_col)] = {
            "n_total": n_total,
            "values":  sorted(values, key=lambda x: -x["share"]),
        }

    return result
