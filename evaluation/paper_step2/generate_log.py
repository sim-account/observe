"""Controlled loan-application event logs for the four-opportunity study.

Generate paired baseline and injected logs with working calendars and resource
queuing. The study opportunities are O1 (slow repetitions), O2 (slow resource),
O3 (concentrated resource assignment), and O4 (segment-associated rework).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ── Process constants ─────────────────────────────────────────────────────────

ROLES = {
    "Clerk":          ["Clerk-1", "Clerk-2", "Clerk-3"],
    "CreditAnalyst":  ["CreditAnalyst-1", "CreditAnalyst-2"],
    "AMLInv":         ["AMLInv-1", "AMLInv-2"],
    "RiskAnalyst":    ["RiskAnalyst-1", "RiskAnalyst-2"],
    "LoanOfficer":    ["LoanOfficer-1", "LoanOfficer-2", "LoanOfficer-3"],
    "SeniorOfficer":  ["SeniorOfficer-1", "SeniorOfficer-2"],
}

ACTIVITY_ROLE = {
    "Check Completeness":   "Clerk",
    "Request Additional Info": "Clerk",
    "Check Credit History": "CreditAnalyst",
    "AML Check":            "AMLInv",
    "Escalate":             "SeniorOfficer",
    "Assess Risk":          "RiskAnalyst",
    "Approve":              "LoanOfficer",
    "Reject":               "LoanOfficer",
    "Design Loan Offer":    "LoanOfficer",
    "Approve Loan Offer":   "SeniorOfficer",
}

# Baseline mean and std durations in minutes
BASE_DURATIONS: dict[str, tuple[float, float]] = {
    "Check Completeness":      (30.0, 5.0),
    "Request Additional Info": (25.0, 5.0),
    "Check Credit History":    (45.0, 8.0),
    "AML Check":               (20.0, 4.0),
    "Escalate":                (30.0, 5.0),
    "Assess Risk":             (60.0, 10.0),
    "Approve":                 (15.0, 3.0),
    "Reject":                  (10.0, 2.0),
    "Design Loan Offer":       (40.0, 6.0),
    "Approve Loan Offer":      (20.0, 4.0),
}

WORK_START_HOUR = 9
WORK_END_HOUR   = 17
WORK_MINUTES    = (WORK_END_HOUR - WORK_START_HOUR) * 60   # 480 min/day
INTER_ARRIVAL_MEAN = 60.0   # minutes between case arrivals

# Baseline routing probabilities
P_REQUEST_ADDITIONAL_INFO = 0.25   
P_ESCALATE               = 0.10   
P_APPROVE                = 0.75

# Baseline rework rates (independent per-case loop probability)
P_REWORK_COMPLETENESS    = 0.0    # driven entirely by Request Additional Info occurrence
P_REWORK_CREDIT_HISTORY  = 0.08   


# ── Injection parameter dataclass ─────────────────────────────────────────────

@dataclass
class InjectionSpec:
    """All parameters that can deviate from baseline for one trial."""
    
    resource_duration_multipliers: dict[tuple[str, str], float] = field(default_factory=dict)

    
    rework_case_duration_multipliers: dict[str, float] = field(default_factory=dict)

    
    predecessor_duration_multipliers: dict[tuple[str, str], float] = field(default_factory=dict)

    
    predecessor_rework_probabilities: dict[tuple[str, str], float] = field(default_factory=dict)

    
    resource_rework_probabilities: dict[tuple[str, str], float] = field(default_factory=dict)

    
    predecessor_resource_shares: dict[tuple[str, str, str], float] = field(default_factory=dict)

    
    # Cases where gateway_activity was preceded by the given predecessor use this approval probability.
    predecessor_branch_skews: dict[tuple[str, str], float] = field(default_factory=dict)

    
    # Cases where gateway_activity was performed by the given resource use this approval probability.
    resource_branch_skews: dict[tuple[str, str], float] = field(default_factory=dict)

    
    approval_skip_loan_offer_prob: float = 0.0

    
    segment_duration_multipliers: dict[str, float] = field(default_factory=dict)

    
    segment_rework_probabilities: dict[tuple[str, str], float] = field(default_factory=dict)

    
    segment_activity_probabilities: dict[tuple[str, str], float] = field(default_factory=dict)

    
    # (uses segment_activity_probabilities with reduced probability)

    # Override global routing probabilities
    p_request_additional_info: float = P_REQUEST_ADDITIONAL_INFO
    p_escalate: float = P_ESCALATE
    p_approve: float = P_APPROVE


# ── Working-calendar helpers ──────────────────────────────────────────────────

def _is_workday(ts: pd.Timestamp) -> bool:
    return ts.dayofweek < 5   # Mon–Fri


def _advance_to_work_start(ts: pd.Timestamp) -> pd.Timestamp:
    """Move ts to next 09:00 on a workday if ts is outside working hours."""
    # Advance past weekends
    while not _is_workday(ts):
        ts = (ts + pd.Timedelta(days=1)).replace(hour=WORK_START_HOUR, minute=0,
                                                   second=0, microsecond=0)
    hour = ts.hour + ts.minute / 60
    if hour < WORK_START_HOUR:
        ts = ts.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
    elif hour >= WORK_END_HOUR:
        # Advance to next workday 09:00
        ts = ts + pd.Timedelta(days=1)
        ts = ts.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
        while not _is_workday(ts):
            ts = ts + pd.Timedelta(days=1)
    return ts


def _add_work_minutes(ts: pd.Timestamp, minutes: float) -> pd.Timestamp:
    """Advance ts by `minutes` of working time, respecting 09:00–17:00 Mon–Fri."""
    remaining = minutes
    ts = _advance_to_work_start(ts)
    while remaining > 0:
        end_of_day = ts.replace(hour=WORK_END_HOUR, minute=0, second=0, microsecond=0)
        available = (end_of_day - ts).total_seconds() / 60
        if remaining <= available:
            ts = ts + pd.Timedelta(minutes=remaining)
            remaining = 0
        else:
            remaining -= available
            # Jump to next workday 09:00
            ts = end_of_day + pd.Timedelta(days=1)
            ts = ts.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
            while not _is_workday(ts):
                ts = ts + pd.Timedelta(days=1)
    return ts


# ── Resource queue tracker ────────────────────────────────────────────────────

class ResourceQueues:
    """Tracks when each resource instance next becomes available."""
    def __init__(self):
        self._free: dict[str, pd.Timestamp] = {}

    def assign(self, resource: str, ready: pd.Timestamp, duration_min: float
               ) -> tuple[pd.Timestamp, pd.Timestamp]:
        """Return (start_time, end_time) respecting both case readiness and resource queue."""
        free = self._free.get(resource, ready)
        start = _advance_to_work_start(max(ready, free))
        end = _add_work_minutes(start, duration_min)
        self._free[resource] = end
        return start, end


# ── Case generation ───────────────────────────────────────────────────────────

def _draw_duration(rng: np.random.Generator, activity: str, mean_override: Optional[float] = None
                   ) -> float:
    base_mean, base_std = BASE_DURATIONS[activity]
    mean = mean_override if mean_override is not None else base_mean
    # Scale std proportionally when mean is overridden
    std = base_std * (mean / base_mean) if base_mean > 0 else base_std
    return float(max(1.0, rng.normal(mean, std)))


def _pick_resource(rng: np.random.Generator, activity: str,
                   predecessor: Optional[str], case_attr: dict,
                   injection: InjectionSpec) -> str:
    role = ACTIVITY_ROLE[activity]
    instances = ROLES[role]

    
    if predecessor is not None:
        for inst in instances:
            key = (activity, predecessor, inst)
            if key in injection.predecessor_resource_shares:
                # Build a probability vector: overridden share for inst, rest share remainder
                override_share = injection.predecessor_resource_shares[key]
                others = [r for r in instances if r != inst]
                remainder = max(0.0, 1.0 - override_share) / max(len(others), 1)
                probs = [override_share if r == inst else remainder for r in instances]
                total = sum(probs)
                probs = [p / total for p in probs]
                return str(rng.choice(instances, p=probs))

    # Default: uniform
    return str(rng.choice(instances))


def _effective_duration(rng: np.random.Generator, activity: str, resource: str,
                        predecessor: Optional[str], is_rework_case: bool,
                        case_attr: dict, injection: InjectionSpec) -> float:
    base_mean, _ = BASE_DURATIONS[activity]

    
    seg_mult = 1.0
    for attr_val, mult in injection.segment_duration_multipliers.items():
        if attr_val in case_attr.values():
            seg_mult = mult
            break

    
    res_mult = injection.resource_duration_multipliers.get((activity, resource), 1.0)

    
    pred_mult = 1.0
    if predecessor is not None:
        pred_mult = injection.predecessor_duration_multipliers.get((activity, predecessor), 1.0)

    
    rework_mult = 1.0
    if is_rework_case and activity in injection.rework_case_duration_multipliers:
        rework_mult = injection.rework_case_duration_multipliers[activity]

    effective_mean = base_mean * seg_mult * res_mult * pred_mult * rework_mult
    return _draw_duration(rng, activity, mean_override=effective_mean)


def _rework_probability(activity: str, resource: str, predecessor: Optional[str],
                        case_attr: dict, injection: InjectionSpec) -> float:
    """Return the loop probability for this activity execution.

    Priority order (highest first):
      1. Specific resource override
      2. Specific predecessor override  — checked before wildcard so predecessor
         injection takes priority over a baseline wildcard set for the same activity
      3. Wildcard resource key — used to inject a baseline rework rate for an activity
         that normally has none
      4. Segment rework override
      5. Hardcoded baseline constants
    """
    
    if (activity, resource) in injection.resource_rework_probabilities:
        return injection.resource_rework_probabilities[(activity, resource)]

    
    if predecessor is not None and (activity, predecessor) in injection.predecessor_rework_probabilities:
        return injection.predecessor_rework_probabilities[(activity, predecessor)]

    
    if (activity, "*") in injection.resource_rework_probabilities:
        return injection.resource_rework_probabilities[(activity, "*")]

    
    for attr_val, prob in injection.segment_rework_probabilities.items():
        if isinstance(attr_val, tuple) and attr_val[0] == activity:
            if attr_val[1] in case_attr.values():
                return prob

    # Baseline
    if activity == "Check Completeness":
        return P_REWORK_COMPLETENESS
    if activity == "Check Credit History":
        return P_REWORK_CREDIT_HISTORY
    return 0.0




def _optional_prob(activity: str, case_attr: dict, injection: InjectionSpec,
                   default_prob: float) -> float:
    """Return occurrence probability for an optional activity, respecting segment overrides."""
    for (act, attr_val), prob in injection.segment_activity_probabilities.items():
        if act == activity and attr_val in case_attr.values():
            return prob
    return default_prob


def generate_case(rng: np.random.Generator, case_id: int, arrival: pd.Timestamp,
                  queues: ResourceQueues, injection: InjectionSpec,
                  ) -> tuple[list[dict], dict]:
    """Generate one case using the same resource pool for every segment."""
    # Assign case attributes at creation time
    channel = "online" if rng.random() < 0.70 else "branch"
    loan_bucket = rng.choice(["small", "medium", "large"], p=[0.50, 0.30, 0.20])
    case_attr = {"channel": channel, "loan_amount": loan_bucket}

    rows: list[dict] = []
    ready = arrival   # case is ready to start its first activity
    predecessor: Optional[str] = None

    def add_event(activity: str, is_rework_case: bool = False,
                  _resource: Optional[str] = None) -> None:
        nonlocal ready, predecessor
        resource = _resource if _resource is not None else _pick_resource(rng, activity, predecessor, case_attr, injection)
        dur = _effective_duration(rng, activity, resource, predecessor,
                                  is_rework_case, case_attr, injection)
        start, end = queues.assign(resource, ready, dur)
        rows.append({"case_id": str(case_id), "activity": activity,
                     "resource": resource, "start_time": start, "end_time": end})
        ready = end
        predecessor = activity

    # ── Step 1: Check Completeness (possibly with rework loop) ────────────────
    # Decide whether Request Additional Info occurs (routing + segment overrides)
    p_rai = _optional_prob("Request Additional Info", case_attr, injection,
                           injection.p_request_additional_info)
    # Completeness rework is the RAI loop; a segment override controls this
    # same decision, rather than setting an unused generic rework parameter.
    for (act, val), probability in injection.segment_rework_probabilities.items():
        if act == "Check Completeness" and val in case_attr.values():
            p_rai = probability
    has_rai = rng.random() < p_rai

    
    is_rework_case = has_rai

    if has_rai:
        add_event("Check Completeness", is_rework_case=is_rework_case)
        add_event("Request Additional Info", is_rework_case=is_rework_case)
        # Loop: re-execute Check Completeness after Request Additional Info
        add_event("Check Completeness", is_rework_case=is_rework_case)
    else:
        add_event("Check Completeness", is_rework_case=is_rework_case)

    # ── Step 2: Check Credit History (serialized first) ───────────────────────
    prev_for_credit = predecessor   # = "Check Completeness" or "Request Additional Info"
    # Pre-pick the CCH resource so we can compute rework probability using the ACTUAL
    
    cch_resource = _pick_resource(rng, "Check Credit History", prev_for_credit, case_attr, injection)
    p_rw_credit = _rework_probability("Check Credit History",
                                      cch_resource, prev_for_credit,
                                      case_attr, injection)
    is_cch_rework_case = rng.random() < p_rw_credit
    # Emit first CCH event; tag it as rework-case if CCH will loop
    
    add_event("Check Credit History",
              is_rework_case=is_cch_rework_case,
              _resource=cch_resource)
    if is_cch_rework_case:
        # Rework loop: predecessor is now "Check Credit History"; resource re-picked freely
        add_event("Check Credit History", is_rework_case=True)

    # ── Step 3: AML Check ─────────────────────────────────────────────────────
    add_event("AML Check", is_rework_case=is_rework_case)

    # ── Step 4: Optional Escalate ─────────────────────────────────────────────
    p_esc = _optional_prob("Escalate", case_attr, injection, injection.p_escalate)
    if rng.random() < p_esc:
        add_event("Escalate", is_rework_case=is_rework_case)

    # ── Step 5: Assess Risk (rework loop) ─────────────────────────────────────
    pred_for_risk = predecessor
    risk_resource = _pick_resource(rng, "Assess Risk", pred_for_risk, case_attr, injection)
    p_rw_risk = _rework_probability("Assess Risk", risk_resource, pred_for_risk,
                                    case_attr, injection)
    is_risk_rework_case = rng.random() < p_rw_risk
    add_event("Assess Risk", is_rework_case=is_risk_rework_case, _resource=risk_resource)
    if is_risk_rework_case:
        add_event("Assess Risk", is_rework_case=True)

    # ── Step 6: Approve / Reject ──────────────────────────────────────────────
    
    ar_resource = rows[-1]["resource"] if rows and rows[-1]["activity"] == "Assess Risk" else None
    ar_predecessor = rows[-2]["activity"] if len(rows) >= 2 and rows[-1]["activity"] == "Assess Risk" else predecessor
    
    p_approve_eff = injection.p_approve
    if ar_predecessor is not None:
        for (gw_act, pred_key), p_ov in injection.predecessor_branch_skews.items():
            if gw_act == "Assess Risk" and pred_key == ar_predecessor:
                p_approve_eff = p_ov
                break
    if ar_resource is not None:
        for (gw_act, res_key), p_ov in injection.resource_branch_skews.items():
            if gw_act == "Assess Risk" and res_key == ar_resource:
                p_approve_eff = p_ov
                break
    approved = rng.random() < p_approve_eff
    if approved:
        add_event("Approve")
        add_event("Design Loan Offer")
        
        if rng.random() >= injection.approval_skip_loan_offer_prob:
            add_event("Approve Loan Offer")
    else:
        add_event("Reject")

    return rows, case_attr


# ── Public generator entry point ──────────────────────────────────────────────

def generate(n_cases: int, seed: int, injection: InjectionSpec,
             start_date: str = "2024-01-01") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate the event log and case attributes DataFrame.

    Returns
    -------
    events : pd.DataFrame
        Columns: case_id, activity, start_time, end_time, resource
    case_attrs : pd.DataFrame
        Columns: case_id, channel, loan_amount
    """
    rng = np.random.default_rng(seed)
    queues = ResourceQueues()
    arrival = pd.Timestamp(start_date, tz="UTC").replace(
        hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)

    all_rows: list[dict] = []
    attr_rows: list[dict] = []

    for case_id in range(n_cases):
        gap = rng.exponential(INTER_ARRIVAL_MEAN)
        arrival = _add_work_minutes(arrival, gap)
        rows, case_attr = generate_case(rng, case_id, arrival, queues, injection)
        all_rows.extend(rows)
        attr_rows.append({"case_id": str(case_id), **case_attr})

    events = pd.DataFrame(all_rows)
    case_attrs = pd.DataFrame(attr_rows)
    return events, case_attrs


# ── Behavior measurement helpers ──────────────────────────────────────────────

def measure_behavior(events: pd.DataFrame, case_attrs: pd.DataFrame,
                     injection_name: str, context: str) -> dict:
    """Measure the exact target without using the miner or its thresholds."""
    from study_measurements import measure_target
    if not injection_name:
        return {}
    return measure_target(events, case_attrs, injection_name, CONTEXT_TARGETS[injection_name][context])


# ── Injection factory ─────────────────────────────────────────────────────────

CONTEXT_TARGETS = {'O1': {'B': 'Check Credit History'},
 'O2': {'A': ('Check Completeness', 'Clerk-1')},
 'O3': {'B': ('Assess Risk', 'AML Check', 'RiskAnalyst-1')},
 'O4': {'B': ('Check Credit History', 'branch')}}


def make_injection(injection_name: str, context: str, strength: float) -> InjectionSpec:
    """Construct the specified four-opportunity study manipulation."""
    target = CONTEXT_TARGETS[injection_name][context]
    spec = InjectionSpec()
    if injection_name == "O1":
        spec.rework_case_duration_multipliers[target] = strength
    elif injection_name == "O2":
        activity, resource = target
        spec.resource_duration_multipliers[(activity, resource)] = strength
    elif injection_name == "O3":
        activity, predecessor, resource = target
        spec.predecessor_resource_shares[(activity, predecessor, resource)] = strength
    elif injection_name == "O4":
        activity, value = target
        spec.segment_rework_probabilities[(activity, value)] = strength
    return spec


def make_control(injection_name: str, context: str) -> InjectionSpec:
    """Construct the shared baseline for a study opportunity."""
    CONTEXT_TARGETS[injection_name][context]
    return InjectionSpec()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--n-cases", type=int, default=1000)
    parser.add_argument("--seed",    type=int, default=42)
    parser.add_argument("--injection", type=str, default=None,
                        help="Injection name, e.g. O2. Omit for baseline.")
    parser.add_argument("--context",  type=str, default=None,
                        help="Study context (O2 uses A; O1, O3, O4 use B).")
    parser.add_argument("--strength", type=float, default=None,
                        help="Injection magnitude (multiplier or probability, per observation spec).")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    if args.injection is not None and args.strength is None:
        parser.error("--strength is required when --injection is specified.")

    if args.injection is None:
        injection = InjectionSpec()
    else:
        args.context = args.context or next(iter(CONTEXT_TARGETS[args.injection]))
        injection = make_injection(args.injection, args.context, args.strength)

    events, case_attrs = generate(args.n_cases, args.seed, injection)

    events.to_csv(args.output / "events.csv", index=False)
    case_attrs.to_csv(args.output / "case_attributes.csv", index=False)

    settings = {
        "n_cases": args.n_cases, "seed": args.seed,
        "injection": args.injection, "context": args.context, "strength": args.strength,
    }
    (args.output / "generator_settings.json").write_text(
        json.dumps(settings, indent=2) + "\n")

    measurements = measure_behavior(events, case_attrs, args.injection, args.context)
    (args.output / "behavior_measurements.json").write_text(
        json.dumps(measurements, indent=2, default=str) + "\n")

    print(f"Generated {len(events)} events for {events['case_id'].nunique()} cases.")
    print(f"Behavior measurements: {json.dumps(measurements, indent=2, default=str)}")


if __name__ == "__main__":
    main()
