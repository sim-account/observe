import heapq
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import pandas as pd

SEP = "|||"   # separator used to join k-gram keys
_NGRAM_ORDER = 5  # must match process_behaviour._build_prefix_tree N
_RESIDUAL_WAIT_MIN_COUNT = 5


@dataclass
class SimulationConfig:
    num_cases: int = 500
    warmup_cases: int = 0          # cases run to warm up resource slots; excluded from stats
    random_seed: int = 42
    enable_resource_contention: bool = True
    enable_residual_wait: bool = True
    work_start_hour: int = 0       # 0 = midnight (24/7 by default)
    work_end_hour: int = 24        # 24 = midnight
    # per-resource calendars: resource_id → (start_hour, end_hour)
    resource_calendars: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    # Active weekdays: 0=Mon … 6=Sun.  None / empty = all 7 days.
    working_days: Optional[List[int]] = None
    # per-resource working days (falls back to working_days if absent)
    resource_working_days: Dict[str, List[int]] = field(default_factory=dict)
    # "probabilistic": assign resource by historical probability, wait for that resource.
    # "availability":  prefer whichever resource is free soonest, softly weighted by
    #                  historical probability to preserve realistic workload distribution.
    resource_routing: str = "probabilistic"
    # Case attribute behaviour: segment_key → {duration_multipliers, rework_multiplier, share}
    # Empty dict = no segment-aware behaviour (runs exactly as before).
    case_attribute_behaviour: Dict[str, Any] = field(default_factory=dict)
    # Real timestamp represented by simulation minute 0.  When omitted, the
    # engine keeps its historical convention that minute 0 is Monday 00:00.
    # Evaluation sets this to the test log's first timestamp.
    simulation_start: Optional[datetime] = None


def _calendar_phase_minutes(simulation_start: Optional[datetime]) -> float:
    """Minute-of-week represented by simulation minute zero."""
    if simulation_start is None:
        return 0.0  # Monday 00:00
    return (
        simulation_start.weekday() * 1440.0
        + simulation_start.hour * 60.0
        + simulation_start.minute
        + simulation_start.second / 60.0
        + simulation_start.microsecond / 60_000_000.0
    )


def _next_work_time(t_minutes: float, start_min: float, end_min: float,
                    working_days: Optional[List[int]] = None,
                    simulation_start: Optional[datetime] = None) -> float:
    """Advance t_minutes to the next working moment (daily hours + day-of-week).

    working_days: 0=Mon … 6=Sun.  None / all-7 = no day-of-week restriction.
    With no simulation_start, the epoch (t=0) is Monday 00:00. Otherwise
    weekdays and times of day are interpreted relative to that timestamp.
    """
    wd_set = set(working_days) if working_days else set(range(7))
    if start_min == 0 and end_min >= 1440 and len(wd_set) == 7:
        return t_minutes
    phase_min = _calendar_phase_minutes(simulation_start)
    calendar_minutes = t_minutes + phase_min
    for _ in range(14):          # search at most 2 weeks forward
        day_abs  = int(calendar_minutes // 1440)
        dow      = day_abs % 7   # 0=Mon … 6=Sun
        day_base = day_abs * 1440.0
        t_in_day = calendar_minutes - day_base
        if dow in wd_set:
            if t_in_day < start_min:
                return day_base + start_min - phase_min
            if t_in_day < end_min:
                return calendar_minutes - phase_min
        # Past end-of-day or non-working day → jump to start of next day
        calendar_minutes = day_base + 1440.0 + start_min
    return calendar_minutes - phase_min  # fallback — valid calendars never reach here


def _work_to_calendar(work_t: float, start_min: float, end_min: float,
                      working_days: Optional[List[int]] = None,
                      simulation_start: Optional[datetime] = None) -> float:
    """Convert work-time (accumulates only during working periods) to calendar minutes.

    working_days: sorted list 0=Mon … 6=Sun. None / all-7 = daily schedule only.
    With no simulation_start this preserves the historical Monday-00:00
    convention. With an explicit start, work_t=0 maps to that exact timestamp
    and signed work offsets are converted around it (warm-up offsets are negative).
    """
    if (not working_days or len(working_days) == 7) and start_min == 0 and end_min >= 1440:
        return work_t
    work_len = max(end_min - start_min, 1.0)

    if simulation_start is not None:
        if work_t == 0:
            return 0.0
        sorted_days = sorted(working_days) if working_days else list(range(7))
        work_per_week = len(sorted_days) * work_len
        phase_min = _calendar_phase_minutes(simulation_start)
        week_min = 7.0 * 1440.0

        def work_coordinate(calendar_min: float) -> float:
            """Cumulative working minutes on an infinite weekly calendar."""
            week_idx = int(calendar_min // week_min)
            in_week = calendar_min - week_idx * week_min
            value = week_idx * work_per_week
            for day in sorted_days:
                interval_start = day * 1440.0 + start_min
                interval_end = day * 1440.0 + end_min
                if in_week >= interval_end:
                    value += work_len
                elif in_week > interval_start:
                    value += in_week - interval_start
                    break
                else:
                    break
            return value

        target_work = work_coordinate(phase_min) + work_t
        week_idx = int(target_work // work_per_week)
        remaining = target_work - week_idx * work_per_week
        for day in sorted_days:
            if remaining <= work_len:
                target_calendar = (
                    week_idx * week_min + day * 1440.0 + start_min + remaining
                )
                return target_calendar - phase_min
            remaining -= work_len
        # Floating-point boundary fallback: beginning of the next work week.
        target_calendar = (week_idx + 1) * week_min + sorted_days[0] * 1440.0 + start_min
        return target_calendar - phase_min

    if not working_days or len(working_days) == 7:
        full_days, remainder = divmod(work_t, work_len)
        return full_days * 1440.0 + start_min + remainder
    sorted_days  = sorted(working_days)
    work_per_week = len(sorted_days) * work_len
    full_weeks, rem = divmod(max(work_t, 0.0), work_per_week)
    cal = full_weeks * 7.0 * 1440.0
    for d in sorted_days:
        if rem <= work_len:
            return cal + d * 1440.0 + start_min + rem
        rem -= work_len
    return cal   # unreachable with valid inputs


def _add_working_minutes(
    timestamp: float,
    duration: float,
    start_min: float,
    end_min: float,
    working_days: Optional[List[int]] = None,
    simulation_start: Optional[datetime] = None,
) -> float:
    """Advance from a calendar timestamp by working minutes only.

    This is used for residual delays so closed nights and weekends are traversed
    but are not sampled a second time as unexplained waiting.
    """
    if duration <= 0:
        return timestamp
    active_days = set(working_days) if working_days else set(range(7))
    if start_min == 0 and end_min >= 1440 and len(active_days) == 7:
        return timestamp + duration

    current = _next_work_time(
        timestamp, start_min, end_min, working_days, simulation_start
    )
    phase_min = _calendar_phase_minutes(simulation_start)
    calendar_minutes = current + phase_min
    sorted_days = sorted(active_days)
    work_len = max(end_min - start_min, 1.0)
    work_per_week = len(sorted_days) * work_len
    week_minutes = 7.0 * 1440.0

    def work_coordinate(value: float) -> float:
        week_index = int(value // week_minutes)
        in_week = value - week_index * week_minutes
        coordinate = week_index * work_per_week
        for day in sorted_days:
            interval_start = day * 1440.0 + start_min
            interval_end = day * 1440.0 + end_min
            if in_week >= interval_end:
                coordinate += work_len
            elif in_week > interval_start:
                coordinate += in_week - interval_start
                break
            else:
                break
        return coordinate

    target = work_coordinate(calendar_minutes) + float(duration)
    week_index = int(target // work_per_week)
    remaining = target - week_index * work_per_week
    for day in sorted_days:
        if remaining <= work_len + 1e-9:
            target_calendar = (
                week_index * week_minutes + day * 1440.0 + start_min + remaining
            )
            return target_calendar - phase_min
        remaining -= work_len
    return (
        (week_index + 1) * week_minutes
        + sorted_days[0] * 1440.0
        + start_min
        - phase_min
    )


def run_simulation(observations: Dict[str, Any], config: SimulationConfig) -> Dict[str, Any]:
    """Run a discrete-event simulation with resource contention, capacity, and working hours."""
    np.random.seed(config.random_seed)

    activity_durations_df: pd.DataFrame = observations["activity_durations_df"]
    resource_assignments_df: Optional[pd.DataFrame] = observations.get("resource_assignments_df")
    rework_parameters_df: Optional[pd.DataFrame] = observations.get("rework_parameters_df")
    arrival_parameters: dict = observations.get("arrival_parameters") or {}
    resource_capacity_dict: Dict[str, int] = observations.get("resource_capacity_dict") or {}
    start_activities: List[str] = observations["start_activities"]
    end_activities: set = set(observations["end_activities"])
    terminal_probs: Dict[str, float] = observations.get("terminal_probs") or {}
    log_metrics: dict = observations.get("log_metrics") or {}
    prefix_tree: Dict[str, list] = observations.get("prefix_tree") or {}
    residual_wait_parameters_df: Optional[pd.DataFrame] = observations.get("residual_wait_parameters_df")

    # Empirical start-activity distribution — sample per case so variant mix matches the log.
    _start_probs_raw: dict = observations.get("start_activity_probs") or {}
    if _start_probs_raw and start_activities:
        _sa_acts  = [a for a in start_activities if a in _start_probs_raw]
        _sa_probs = np.array([_start_probs_raw[a] for a in _sa_acts], dtype=float)
        _sa_probs /= _sa_probs.sum()
    else:
        _sa_acts  = start_activities[:1]
        _sa_probs = np.array([1.0])

    # Step cap: 2× the 95th-percentile path length observed in the log, floored at 20.
    # Prevents the Markov chain from generating cases far longer than any real variant.
    step_cap = max(20, int(log_metrics.get("p95_path_length", 50)) * 2)

    work_start_min = config.work_start_hour * 60.0
    work_end_min   = config.work_end_hour   * 60.0
    working_days   = config.working_days or None   # None = 7-day (fast path in helpers)

    # Per-resource calendars in minutes (falls back to global if not specified)
    res_cal_min: Dict[str, Tuple[float, float]] = {
        rid: (s * 60.0, e * 60.0)
        for rid, (s, e) in (config.resource_calendars or {}).items()
    }

    # Duration lookup: activity → (mean_min, std_min)
    time_lookup: Dict[str, Tuple[float, float]] = {
        row["activity"]: (float(row["mean_minutes"]), float(row["std_minutes"]))
        for _, row in activity_durations_df.iterrows()
    }

    # Resource-specific durations: (activity, resource) → (mean_min, std_min).
    # Used when a resource is assigned so that each SID's working-time service
    # duration is honoured rather than blending all resources into one activity-wide mean.
    resource_time_lookup: Dict[Tuple[str, str], Tuple[float, float]] = {}
    if resource_assignments_df is not None and not resource_assignments_df.empty:
        for _, row in resource_assignments_df.iterrows():
            has_mean = "mean_duration_min" in row and pd.notna(row["mean_duration_min"])
            mean_dur = float(row["mean_duration_min"]) if has_mean else 0.0
            marker = row.get("duration_observed")
            if pd.notna(marker):
                duration_known = has_mean and bool(marker)
            else:
                # Backward compatibility for observations created before duration
                # provenance was recorded: retain the historical positive-only
                # behavior because their zero values are ambiguous.
                duration_known = has_mean and mean_dur > 0
            if duration_known:
                resource_time_lookup[(str(row["activity"]), str(row["resource"]))] = (
                    mean_dur,
                    float(row.get("std_duration_min", 0.0) or 0.0),
                )

    # Resource assignment lookup: activity → [(resource, probability)]
    resource_lookup: Dict[str, List[Tuple[str, float]]] = {}
    if (config.enable_resource_contention
            and resource_assignments_df is not None
            and not resource_assignments_df.empty):
        for act, grp in resource_assignments_df.groupby("activity"):
            resource_lookup[act] = list(zip(grp["resource"].astype(str), grp["probability"]))

    # Utilization-only lookup: always built when resource data exists, even without contention.
    # Used to sample which resource performed each activity for utilization accounting.
    util_resource_lookup: Dict[str, List[Tuple[str, float]]] = {}
    if resource_assignments_df is not None and not resource_assignments_df.empty:
        for act, grp in resource_assignments_df.groupby("activity"):
            util_resource_lookup[act] = list(zip(grp["resource"].astype(str), grp["probability"]))

    # Per-activity max-visit cap learned from the log.
    # Activities in rework_parameters get cap = max_loops + 1; all others default to 1.
    # This blocks spurious loops from parallel-system bidirectional Markov arcs while
    # allowing genuine rework up to the empirically observed maximum.
    max_visits_lookup: Dict[str, int] = {}
    if rework_parameters_df is not None and not rework_parameters_df.empty:
        for _, row in rework_parameters_df.iterrows():
            max_visits_lookup[str(row["activity"])] = int(row["max_loops"]) + 1

    # Parallel-pair rates: (activity_a, activity_b) → probability that b starts in parallel
    # with a when the Markov chain picks b as the next activity after a.
    # Directional: (A,B) uses a_parallel_with_b_pct, (B,A) uses b_parallel_with_a_pct.
    par_pair_rates: Dict[Tuple[str, str], float] = {}
    parallel_pairs_df: Optional[pd.DataFrame] = observations.get("parallel_pairs_df")
    if parallel_pairs_df is not None and not parallel_pairs_df.empty:
        for _, row in parallel_pairs_df.iterrows():
            a, b = str(row["activity_a"]), str(row["activity_b"])
            rate_ab = float(row["a_parallel_with_b_pct"]) / 100.0
            rate_ba = float(row["b_parallel_with_a_pct"]) / 100.0
            if rate_ab >= 0.20:
                par_pair_rates[(a, b)] = rate_ab
            if rate_ba >= 0.20:
                par_pair_rates[(b, a)] = rate_ba

    # Residual wait is the learned enablement-to-release delay that remains
    # after replaying calendars and historical resource capacity. Supported
    # transition contexts take precedence; sparse contexts back off to a
    # mixture of all observations for the target activity.
    residual_pair_lookup: Dict[Tuple[str, str], dict] = {}
    residual_activity_lookup: Dict[str, List[dict]] = {}
    if (config.enable_residual_wait
            and residual_wait_parameters_df is not None
            and not residual_wait_parameters_df.empty):
        for _, row in residual_wait_parameters_df.iterrows():
            record = row.to_dict()
            key = (str(row["from_activity"]), str(row["activity"]))
            if bool(row.get("context_supported", False)):
                residual_pair_lookup[key] = record
            residual_activity_lookup.setdefault(str(row["activity"]), []).append(record)

    def _sample_wait_record(record: dict) -> float:
        probability = float(record.get("positive_wait_probability", 0.0) or 0.0)
        if probability <= 0 or np.random.random() >= probability:
            return 0.0
        samples = record.get("positive_wait_samples_min") or []
        if isinstance(samples, np.ndarray):
            samples = samples.tolist()
        if not samples:
            return max(0.0, float(record.get("mean_positive_minutes", 0.0) or 0.0))
        return max(0.0, float(np.random.choice(samples)))

    def _sample_residual_wait(from_activity: Optional[str], activity: str) -> float:
        if not from_activity:
            return 0.0
        exact = residual_pair_lookup.get((from_activity, activity))
        if exact is not None:
            return _sample_wait_record(exact)
        fallback = residual_activity_lookup.get(activity) or []
        if not fallback:
            return 0.0
        if (
            sum(float(row.get("observations", 0) or 0) for row in fallback)
            < _RESIDUAL_WAIT_MIN_COUNT
        ):
            return 0.0
        weights = np.asarray(
            [max(1.0, float(row.get("observations", 1) or 1)) for row in fallback],
            dtype=float,
        )
        weights /= weights.sum()
        picked = fallback[int(np.random.choice(len(fallback), p=weights))]
        return _sample_wait_record(picked)

    # ── Case attribute distributions ──────────────────────────────────────────
    # New structure: {attr: {n_total, values: [{value, share, n_cases}]}}
    # For each case, sample one value per attribute independently.
    # If empty, no case attributes are assigned — zero regression risk.
    # _attr_dist: attr → (values_list, shares_array) ready for np.random.choice
    _attr_dist: Dict[str, Tuple[List[str], np.ndarray]] = {}

    if config.case_attribute_behaviour:
        for attr, attr_data in config.case_attribute_behaviour.items():
            vals_list = attr_data.get("values", [])
            if not vals_list:
                continue
            values  = [str(v["value"]) for v in vals_list]
            shares  = np.array([float(v["share"]) for v in vals_list], dtype=float)
            total   = shares.sum()
            if total > 0:
                shares /= total
            _attr_dist[str(attr)] = (values, shares)

    _use_segments = bool(_attr_dist)

    # Arrivals: generate inter-arrivals in work-time then convert to calendar time.
    # work_min_per_day = effective working minutes per *calendar* day (accounts for
    # day-of-week restriction so the arrival density across all 7 days stays correct).
    cases_per_day = float(arrival_parameters.get("cases_per_day", config.num_cases))
    n_work_days   = len(working_days) if working_days else 7
    if work_start_min == 0 and work_end_min >= 1440 and n_work_days == 7:
        work_min_per_day = 1440.0
    else:
        daily_shift_min  = max(work_end_min - work_start_min, 1.0)
        work_min_per_day = daily_shift_min * n_work_days / 7.0
    rate_per_work_min = cases_per_day / max(work_min_per_day, 1.0)
    inter_arrivals_work = np.random.exponential(1.0 / max(rate_per_work_min, 1e-9), config.num_cases)
    arrival_work_times  = np.cumsum(inter_arrivals_work)
    # In dated simulations minute zero is the start of the measured period.
    # Rebase arrivals so warm-up cases occur before it and the first retained
    # case arrives at zero. Undated simulations preserve the legacy timeline.
    if config.simulation_start is not None and config.warmup_cases < config.num_cases:
        arrival_work_times = arrival_work_times - arrival_work_times[config.warmup_cases]
    arrival_times = np.array([
        _work_to_calendar(
            wt, work_start_min, work_end_min, working_days, config.simulation_start
        )
        for wt in arrival_work_times
    ])
    initial_resource_time = float(np.min(arrival_times)) if len(arrival_times) else 0.0

    # Resource slots: resource_id → list of N free-at times (one per capacity instance)
    resource_slots: Dict[str, List[float]] = {}
    resource_busy_min: Dict[str, float] = {}   # accumulated busy minutes per resource

    def _get_slot(res: str) -> Tuple[int, float]:
        """Return (slot_index, available_at) for the earliest-free slot of a resource."""
        if res not in resource_slots:
            cap = resource_capacity_dict.get(res, 1)
            resource_slots[res] = [initial_resource_time] * max(1, cap)
        slots = resource_slots[res]
        idx = int(np.argmin(slots))
        return idx, slots[idx]

    # Case records
    case_records: Dict[int, dict] = {
        i: {
            "arrival": arrival_times[i],
            "service_minutes": 0.0,
            "wait_minutes": 0.0,
            "finish_time": None,
            "activities": [],
            "activity_durations": {},
            "activity_waits": {},
            "full_history": deque(maxlen=_NGRAM_ORDER),
            "visit_counts": {},
            # Cases may arrive before zero during a dated simulation's warm-up.
            # Starting this at zero would postpone their successor activities to
            # the reporting boundary and create an artificial resource backlog.
            "parallel_finish": arrival_times[i],
            "events": [],        # per-event log: [{activity, start_wmin, end_wmin, resource, wait_wmin}]
            "case_attributes": {},  # {attr: value} — assigned below when distributions are active
        }
        for i in range(config.num_cases)
    }

    # Assign one value per attribute per case from the learned distributions
    if _use_segments:
        for attr, (values, shares) in _attr_dist.items():
            sampled = np.random.choice(values, size=config.num_cases, p=shares)
            for i in range(config.num_cases):
                case_records[i]["case_attributes"][attr] = str(sampled[i])

    # DES heap: (time, tie_break, case_id, activity, steps, released, residual_wait)
    # Residual waits use a separate release event. This is essential: processing
    # an activity immediately at its future release time would reserve a resource
    # before other heap events occurring in between had a chance to run.
    event_id = 0
    heap: List[Tuple] = []
    for i in range(config.num_cases):
        t0 = _next_work_time(
            arrival_times[i], work_start_min, work_end_min, working_days,
            config.simulation_start,
        )
        start_act = str(np.random.choice(_sa_acts, p=_sa_probs))
        heapq.heappush(heap, (t0, event_id, i, start_act, 0, False, 0.0))
        event_id += 1

    while heap:
        sched_time, _, case_id, activity, steps, released, residual_wait = heapq.heappop(heap)
        rec = case_records[case_id]
        old_join = rec["parallel_finish"]

        if steps > step_cap:
            if rec["finish_time"] is None:
                rec["finish_time"] = max(sched_time, old_join)
            continue

        if not released:
            from_activity = rec["activities"][-1] if rec["activities"] else None
            residual_wait_work = _sample_residual_wait(from_activity, activity)
            release_time = _add_working_minutes(
                sched_time, residual_wait_work, work_start_min, work_end_min,
                working_days, config.simulation_start,
            )
            residual_wait = max(0.0, release_time - sched_time)
            if residual_wait > 1e-9:
                heapq.heappush(
                    heap,
                    (release_time, event_id, case_id, activity, steps, True, residual_wait),
                )
                event_id += 1
                continue
        release_time = sched_time

        slot_idx: Optional[int] = None
        if config.resource_routing == "availability" and resource_lookup.get(activity):
            _picked = _pick_resource_by_availability(
                activity, resource_lookup, resource_slots, resource_capacity_dict, release_time,
                initial_resource_time,
            )
            if _picked:
                resource, slot_idx, res_avail = _picked
                candidate = max(release_time, res_avail)
                r_start, r_end = res_cal_min.get(resource, (work_start_min, work_end_min))
                r_days = config.resource_working_days.get(resource, working_days)
                actual_start = _next_work_time(
                    candidate, r_start, r_end, r_days, config.simulation_start
                )
                resource_wait = actual_start - release_time
            else:
                resource = None
                actual_start = _next_work_time(
                    release_time, work_start_min, work_end_min, working_days,
                    config.simulation_start,
                )
                resource_wait = actual_start - release_time
        else:
            resource = _sample_resource(activity, resource_lookup)
            if resource:
                slot_idx, res_avail = _get_slot(resource)
                candidate = max(release_time, res_avail)
                r_start, r_end = res_cal_min.get(resource, (work_start_min, work_end_min))
                r_days = config.resource_working_days.get(resource, working_days)
                actual_start = _next_work_time(
                    candidate, r_start, r_end, r_days, config.simulation_start
                )
                resource_wait = actual_start - release_time
            else:
                actual_start = _next_work_time(
                    release_time, work_start_min, work_end_min, working_days,
                    config.simulation_start,
                )
                resource_wait = actual_start - release_time
        wait = residual_wait + max(0.0, resource_wait)

        resource_duration_key = (activity, resource)
        if resource_duration_key in resource_time_lookup:
            mean, std = resource_time_lookup[resource_duration_key]
        elif activity in time_lookup:
            mean, std = time_lookup[activity]
        else:
            mean, std = (0.0, 0.0)
        duration = _sample_duration(mean, std)
        finish = actual_start + duration

        # Advance the case's parallel join-time to cover this activity.
        # Sequential next-activities wait until rec["parallel_finish"]; parallel
        # ones start at actual_start (overlapping), so old_join is used for their push.
        rec["parallel_finish"] = max(old_join, finish)

        if resource and slot_idx is not None:
            resource_slots[resource][slot_idx] = finish
        # Utilization tracking: use contention-assigned resource if available,
        # otherwise sample from util_resource_lookup (works when contention is OFF).
        util_res = resource if resource else _sample_resource(activity, util_resource_lookup)
        if util_res:
            resource_busy_min[util_res] = resource_busy_min.get(util_res, 0.0) + duration

        rec["activities"].append(activity)
        rec["full_history"].append(activity)
        rec["service_minutes"] += duration
        rec["wait_minutes"] += max(0.0, wait)
        rec["activity_durations"][activity] = rec["activity_durations"].get(activity, 0.0) + duration
        rec["activity_waits"][activity]     = rec["activity_waits"].get(activity, 0.0) + max(0.0, wait)
        rec["visit_counts"][activity]       = rec["visit_counts"].get(activity, 0) + 1
        rec["events"].append({
            "activity":   activity,
            "start_wmin": actual_start,
            "end_wmin":   finish,
            "resource":   resource or "",
            "wait_wmin":  max(0.0, wait),
            "residual_wait_wmin": residual_wait,
            "resource_wait_wmin": max(0.0, resource_wait),
        })

        if activity in end_activities:
            # Probabilistic termination: stop with P = terminal_probs[activity].
            # Activities that sometimes end a case but also have successors (e.g. a
            # state that is terminal for short cases but leads onward for long ones)
            # get a terminal probability < 1, allowing the chain to continue and
            # reach the long-duration downstream states.
            t_prob = terminal_probs.get(activity, 1.0)
            if np.random.random() < t_prob:
                rec["finish_time"] = rec["parallel_finish"]
                continue
            # Fall through to transition lookup for the continuation fraction

        # Transition lookup: try the ngram context table longest-suffix-first.
        # Order-1 contexts are always present for observed outgoing transitions,
        # so every known activity automatically provides the final fallback.
        transitions = None
        full_hist = rec["full_history"]

        if prefix_tree and full_hist:
            for n in range(min(_NGRAM_ORDER, len(full_hist)), 0, -1):
                key = SEP.join(list(full_hist)[-n:])
                transitions = prefix_tree.get(key)
                if transitions:
                    break

        if not transitions:
            rec["finish_time"] = rec["parallel_finish"]
            continue

        # Filter out activities that have already hit their per-case max-visits cap.
        # Activities not in max_visits_lookup default to cap = 1 (no rework allowed).
        visit_counts = rec["visit_counts"]
        valid = [(t, p) for t, p in transitions
                 if visit_counts.get(t, 0) < max_visits_lookup.get(t, 1)]

        if not valid:
            rec["finish_time"] = rec["parallel_finish"]
            continue

        next_acts = [t[0] for t in valid]
        probs = np.array([t[1] for t in valid], dtype=float)
        probs /= probs.sum()
        next_act = str(np.random.choice(next_acts, p=probs))

        par_rate = par_pair_rates.get((activity, next_act), 0.0)
        if par_rate > 0.0 and np.random.random() < par_rate:
            # next_act runs in parallel with current activity: start it at the
            # same time as the current activity (not after it finishes).
            # old_join is the join-time *before* this activity — ensures next_act
            # doesn't start before earlier parallel batches have been resolved.
            push_time = max(actual_start, old_join)
        else:
            # Sequential: next_act can only start after all in-flight parallel
            # activities (including the current one) have finished.
            push_time = rec["parallel_finish"]

        heapq.heappush(
            heap, (push_time, event_id, case_id, next_act, steps + 1, False, 0.0)
        )
        event_id += 1

    # Finalise cases — skip warmup cases from stats (they still ran to pre-load resource slots)
    cases = []
    for i in range(config.num_cases):
        rec = case_records[i]
        if rec["finish_time"] is None:
            rec["finish_time"] = rec["arrival"] + rec["service_minutes"] + rec["wait_minutes"]
        if i < config.warmup_cases:
            continue
        cycle_time = rec["finish_time"] - rec["arrival"]
        has_rework = any(v > 1 for v in Counter(rec["activities"]).values())
        cases.append({
            "case_id": i,
            "cycle_minutes":   cycle_time,
            "service_minutes": rec["service_minutes"],
            "wait_minutes":    rec["wait_minutes"],
            "activities":      rec["activities"],
            "activity_durations": rec["activity_durations"],
            "activity_waits":     rec["activity_waits"],
            "has_rework":         has_rework,
            "total_minutes":      cycle_time,
            "events":             rec["events"],
            "case_attributes":    rec.get("case_attributes", {}),
        })

    # Aggregate stats
    cycle_h   = [c["cycle_minutes"]  / 60 for c in cases]
    wait_h    = [c["wait_minutes"]   / 60 for c in cases]
    service_h = [c["service_minutes"]/ 60 for c in cases]

    def _pcts(s):
        return (float(np.mean(s)), float(np.median(s)),
                float(np.percentile(s, 75)), float(np.percentile(s, 90)))

    avg_ct, med_ct, p75_ct, p90_ct = _pcts(cycle_h)
    avg_wt, med_wt, p75_wt, p90_wt = _pcts(wait_h)
    avg_svc = float(np.mean(service_h))

    rework_cases   = sum(1 for c in cases if c["has_rework"])
    rework_rate_pct = round(rework_cases / len(cases) * 100, 1)
    avg_rework_acts = round(float(np.mean([
        sum(1 for v in Counter(c["activities"]).values() if v > 1) for c in cases
    ])), 2)

    # Activity-level aggregates
    total_svc:  Dict[str, float] = {}
    total_wait: Dict[str, float] = {}
    act_counts: Dict[str, int]   = {}
    for c in cases:
        for act, dur in c["activity_durations"].items():
            total_svc[act]  = total_svc.get(act, 0.0) + dur
            act_counts[act] = act_counts.get(act, 0) + 1
        for act, wt in c["activity_waits"].items():
            total_wait[act] = total_wait.get(act, 0.0) + wt

    grand_svc  = max(sum(total_svc.values()), 1.0)
    grand_wait = max(sum(total_wait.values()), 1.0)

    activity_time_breakdown = dict(sorted(
        {act: round(t / grand_svc * 100, 2) for act, t in total_svc.items() if t > 0}.items(),
        key=lambda x: -x[1],
    ))
    activity_wait_breakdown = dict(sorted(
        {act: round(w / grand_wait * 100, 2) for act, w in total_wait.items() if w > 0}.items(),
        key=lambda x: -x[1],
    ))

    bottlenecks = sorted(
        [
            {
                "activity": act,
                "mean_duration_min": round(total_svc.get(act, 0.0) / max(act_counts.get(act, 1), 1), 1),
                "mean_wait_min":     round(total_wait.get(act, 0.0) / max(act_counts.get(act, 1), 1), 1),
                "total_wait_share_pct": round(total_wait.get(act, 0.0) / grand_wait * 100, 1),
                "total_time_share_pct": round(total_svc.get(act, 0.0) / grand_svc * 100, 1),
            }
            for act in set(total_svc) | set(total_wait)
            if act_counts.get(act, 0) > 0
        ],
        key=lambda x: -x["total_wait_share_pct"],
    )[:10]

    waiting_pct_of_cycle = round(avg_wt / max(avg_ct, 1e-9) * 100, 1)

    # Resource utilization: busy_min / (sim_duration_min * capacity)
    # sim_duration_min = time from first arrival to last finish (calendar minutes)
    all_finishes = [r["finish_time"] for r in case_records.values() if r["finish_time"] is not None]
    sim_start_cal = float(arrival_times[0]) if len(arrival_times) > 0 else 0.0
    sim_end_cal   = max(all_finishes) if all_finishes else sim_start_cal
    sim_span_min  = max(sim_end_cal - sim_start_cal, 1.0)

    resource_utilization: Dict[str, float] = {}
    for res, busy in resource_busy_min.items():
        cap = resource_capacity_dict.get(res, 1)
        util = round(busy / (sim_span_min * max(cap, 1)) * 100, 1)
        resource_utilization[res] = min(util, 100.0)

    return {
        "cases": cases,
        "avg_cycle_time_hours":    avg_ct,
        "median_cycle_time_hours": med_ct,
        "p75_cycle_time_hours":    p75_ct,
        "p90_cycle_time_hours":    p90_ct,
        "avg_waiting_time_hours":    avg_wt,
        "median_waiting_time_hours": med_wt,
        "p75_waiting_time_hours":    p75_wt,
        "p90_waiting_time_hours":    p90_wt,
        "waiting_pct_of_cycle":      waiting_pct_of_cycle,
        "avg_service_time_hours":    avg_svc,
        "rework_rate_pct":   rework_rate_pct,
        "rework_cases":      rework_cases,
        "avg_rework_acts":   avg_rework_acts,
        "activity_time_breakdown": activity_time_breakdown,
        "activity_wait_breakdown": activity_wait_breakdown,
        "bottlenecks":             bottlenecks,
        "cases_completed":         len(cases),
        "resource_utilization":    resource_utilization,
    }


def _sample_resource(activity: str, resource_lookup: Dict) -> Optional[str]:
    options = resource_lookup.get(activity)
    if not options:
        return None
    # Pure-Python weighted draw — faster than np.random.choice on small lists
    total = sum(p for _, p in options)
    r = np.random.random() * total
    cumsum = 0.0
    for res, p in options:
        cumsum += p
        if r <= cumsum:
            return res
    return options[-1][0]


def _pick_resource_by_availability(
    activity: str,
    resource_lookup: Dict[str, List[Tuple[str, float]]],
    resource_slots: Dict[str, List[float]],
    resource_capacity_dict: Dict[str, int],
    sched_time: float,
    initial_available: float = 0.0,
) -> Optional[Tuple[str, int, float]]:
    """Pick a resource by availability-weighted probability.

    Prefers the resource that will be free soonest, softly weighted by historical
    probability so that the long-run workload distribution matches the log.
    When all candidates are equally free the selection is pure historical sampling;
    a resource busy for ~scale minutes gets roughly half its historical weight.
    """
    options = resource_lookup.get(activity)
    if not options:
        return None

    scale = 60.0
    total_w = 0.0
    # Build (res, slot_idx, avail_at, weight) inline — one pass, no numpy on small lists
    cands: List[Tuple[str, int, float, float]] = []
    for res, hist_prob in options:
        if res not in resource_slots:
            cap = resource_capacity_dict.get(res, 1)
            resource_slots[res] = [initial_available] * max(1, cap)
        slots = resource_slots[res]
        # Fast path: most resources have capacity 1
        if len(slots) == 1:
            idx, avail = 0, slots[0]
        else:
            idx = min(range(len(slots)), key=slots.__getitem__)
            avail = slots[idx]
        wait = avail - sched_time if avail > sched_time else 0.0
        w = hist_prob / (1.0 + wait / scale)
        total_w += w
        cands.append((res, idx, avail, w))

    # Pure-Python weighted draw — avoids np.random.choice overhead on small collections
    r = np.random.random() * total_w
    cumsum = 0.0
    for res, idx, avail, w in cands:
        cumsum += w
        if r <= cumsum:
            return res, idx, avail
    res, idx, avail, _ = cands[-1]
    return res, idx, avail


def build_simulation_log(
    cases: List[Dict[str, Any]],
    config: SimulationConfig,
    log_end_iso: Optional[str],
    seg_meta: Optional[Dict[str, Dict[str, str]]] = None,  # kept for API compat, unused
) -> pd.DataFrame:
    """Convert simulated cases to an event log DataFrame with anchored timestamps.

    Event start/end values are already calendar-minute offsets: arrivals are
    converted from work time before they enter the DES, and all subsequent
    scheduling happens on that calendar timeline.

    Columns: case_id | activity | start_time | end_time | resource | wait_minutes |
             residual_wait_minutes | resource_wait_minutes |
             <one column per case attribute>
    """
    if config.simulation_start is not None:
        anchor = config.simulation_start
    elif log_end_iso:
        try:
            anchor = datetime.fromisoformat(log_end_iso) + timedelta(days=1)
        except ValueError:
            anchor = datetime(2025, 1, 1)
    else:
        anchor = datetime(2025, 1, 1)

    # Collect all attribute names across all cases
    all_attrs: List[str] = []
    if cases:
        seen: set = set()
        for case in cases:
            for k in (case.get("case_attributes") or {}):
                if k not in seen:
                    all_attrs.append(k)
                    seen.add(k)

    rows = []
    for case in cases:
        ca = case.get("case_attributes") or {}
        for ev in case.get("events", []):
            # The legacy *_wmin names are misleading: these values have already
            # been converted to calendar minutes by run_simulation(). Converting
            # them again would insert non-working nights/weekends a second time.
            start_cal = ev["start_wmin"]
            end_cal   = ev["end_wmin"]
            row: Dict[str, Any] = {
                "case_id":      str(case["case_id"]),
                "activity":     ev["activity"],
                "start_time":   anchor + timedelta(minutes=start_cal),
                "end_time":     anchor + timedelta(minutes=end_cal),
                "resource":     ev["resource"],
                "wait_minutes": round(ev["wait_wmin"], 2),
                "residual_wait_minutes": round(ev.get("residual_wait_wmin", 0.0), 2),
                "resource_wait_minutes": round(ev.get("resource_wait_wmin", ev["wait_wmin"]), 2),
            }
            for attr in all_attrs:
                row[attr] = ca.get(attr, "")
            rows.append(row)

    base_cols = [
        "case_id", "activity", "start_time", "end_time", "resource",
        "wait_minutes", "residual_wait_minutes", "resource_wait_minutes",
    ]
    if not rows:
        return pd.DataFrame(columns=base_cols + all_attrs)
    return pd.DataFrame(rows)


def _sample_duration(mean: float, std: float) -> float:
    if mean <= 0:
        return 0.0
    if std <= 0 or (std / mean) < 0.01:
        return max(0.0, float(np.random.normal(mean, mean * 0.05)))
    cv = std / mean
    sigma = float(np.sqrt(np.log(1 + cv ** 2)))
    mu    = float(np.log(mean) - sigma ** 2 / 2)
    return float(np.random.lognormal(mu, sigma))
