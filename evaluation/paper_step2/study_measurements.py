"""Independent checks of generated behavior, separate from discovery and simulation."""
from __future__ import annotations

import numpy as np
import pandas as pd


def source_cycle_hours(events):
    groups = events.groupby('case_id')
    return float(((groups.end_time.max() - groups.start_time.min()).dt.total_seconds() / 3600).mean())


def measure_target(events, case_attrs, injection_name, target):
    ev = events.sort_values(['case_id', 'start_time']).copy()
    ev['previous'] = ev.groupby('case_id').activity.shift()
    ev['next'] = ev.groupby('case_id').activity.shift(-1)
    start = ev.start_time.dt.tz_localize(None).to_numpy()
    end = ev.end_time.dt.tz_localize(None).to_numpy()
    sd, ed = start.astype('datetime64[D]'), end.astype('datetime64[D]')
    ev['work_minutes'] = (np.busday_count(sd, ed) * 480
        + np.clip((end - ed) / np.timedelta64(1, 'm') - 540, 0, 480)
        - np.clip((start - sd) / np.timedelta64(1, 'm') - 540, 0, 480))
    counts = ev.groupby(['case_id', 'activity']).size().unstack(fill_value=0)

    def contrast(values, mask, metric):
        a, b = values[mask], values[~mask]
        return {'metric': metric, 'condition_value': float(a.mean()) if len(a) else None,
                'other_value': float(b.mean()) if len(b) else None,
                'condition_n': len(a), 'other_n': len(b)}

    if injection_name in ("O1", "O2"):
        activity = target if injection_name == "O1" else target[0]
        group = ev[ev.activity.eq(activity)]
        mask = (group.case_id.isin(counts.index[counts[activity].gt(1)])
                if injection_name == "O1" else group.resource.eq(target[1]))
        result = contrast(group.work_minutes, mask, 'working_duration_minutes')
    elif injection_name == "O3":
        activity, predecessor, resource = target
        group = ev[ev.activity.eq(activity)]
        result = contrast(group.resource.eq(resource).astype(float), group.previous.eq(predecessor), 'resource_share')
    elif injection_name == "O4":
        activity, value = target
        attribute = 'channel' if value == 'branch' else 'loan_amount'
        attrs = case_attrs.set_index('case_id')
        mask = attrs[attribute].eq(value).reindex(counts.index)
        result = contrast(counts[activity].gt(1).astype(float), mask, 'activity_rework_probability')
    else:
        raise ValueError(f'Unknown study opportunity: {injection_name}')
    result['source_cycle_hours'] = source_cycle_hours(ev)
    result['expected_direction'] = 1
    return result


def verify_injection(baseline, injected):
    """Require exposure, expected contrast and a change from the paired control.

    This is a manipulation check, not a significance test or detector threshold.
    Low support and wrong-direction finite samples are recorded, never discarded.
    """
    if injected['condition_n'] < 5 or baseline['condition_n'] < 5:
        return False, 'insufficient_target_support'
    direction = injected['expected_direction']
    value, before = injected['condition_value'], baseline['condition_value']
    if value is None or before is None or direction * (value - before) <= 0:
        return False, 'target_did_not_change_in_expected_direction'
    other = injected['other_value']
    if other is not None and (injected['other_n'] < 5 or direction * (value - other) <= 0):
        return False, 'target_contrast_not_present'
    return True, 'verified'
