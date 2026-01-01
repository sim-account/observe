import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import copy
import hashlib
import io
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import Any, Dict, List, Optional

import session_store
from models import SimulationRequest, ParseScenarioRequest
from serializers import deserialize_model
from src.simulation_engine import SimulationConfig, run_simulation, build_simulation_log
from src.scenario_parser import parse_observation_with_llm

router = APIRouter()


@router.post("/session/{session_id}/simulate")
async def simulate(session_id: str, body: SimulationRequest):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    observations_data = session.get("baseline_model") or session.get("discovered_model")
    if observations_data is None:
        raise HTTPException(status_code=400, detail="Observations not yet extracted")

    observations = deserialize_model(observations_data)
    mods = [m.model_dump() for m in body.modifications]

    if mods:
        import logging
        log = logging.getLogger("simulate")
        ra = observations.get("resource_assignments_df")
        ta = observations.get("activity_durations_df")
        for m in mods:
            t = m["type"]
            act = m.get("activity")
            if t == "resource_share":
                res = m.get("resource")
                match = (ra is not None and not ra.empty and
                         ((ra["activity"] == act) & (ra["resource"] == res)).any())
                cur = float(ra.loc[(ra["activity"] == act) & (ra["resource"] == res), "probability"].iloc[0]) if match else "NOT FOUND"
                log.warning("MOD resource_share  act=%r  res=%r  change=%s  value=%s  cur_prob=%s  match=%s",
                            act, res, m["change_type"], m["value"], cur, match)
            elif t == "time":
                match = (ta is not None and not ta.empty and (ta["activity"] == act).any())
                cur = float(ta.loc[ta["activity"] == act, "mean_minutes"].iloc[0]) if match else "NOT FOUND"
                log.warning("MOD time  act=%r  change=%s  value=%s  cur_mean_min=%s  match=%s",
                            act, m["change_type"], m["value"], cur, match)
            else:
                log.warning("MOD %s  act=%r  res=%r  change=%s  value=%s",
                            t, act, m.get("resource"), m["change_type"], m["value"])


    try:
        observations = _apply_modifications(observations, mods)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Derive num_cases from sim_days + warmup_days using the (possibly modified) arrival rate.
    # Scaling by the modified rate means arrival-rate to-be scenarios always cover the same
    # calendar horizon as the as-is baseline — fixes the "more arrivals → shorter simulation
    # → artificially lower cycle times" burn-in artefact.
    cases_per_day = float((observations.get("arrival_parameters") or {}).get("cases_per_day", 1.0))
    cases_per_day = max(cases_per_day, 1e-6)
    warmup_cases = max(0, round(cases_per_day * body.warmup_days))
    sim_cases    = max(50, round(cases_per_day * body.sim_days))

    # Use working hours and working days learned from the event log; fall back to 24/7 if absent.
    calendars = observations.get("calendars") or {}
    work_start = int(calendars.get("global_start_hour", 0))
    work_end   = int(calendars.get("global_end_hour", 24))
    working_days = calendars.get("global_working_days") or None
    resource_calendars = {
        str(rid): (int(s), int(e))
        for rid, (s, e) in (calendars.get("resource_calendars") or {}).items()
    }
    resource_working_days = {
        str(rid): list(map(int, days))
        for rid, days in (calendars.get("resource_working_days") or {}).items()
    }

    config = SimulationConfig(
        num_cases=warmup_cases + sim_cases,
        warmup_cases=warmup_cases,
        random_seed=body.random_seed,
        enable_resource_contention=body.enable_resource_contention,
        resource_routing=body.resource_routing,
        work_start_hour=work_start,
        work_end_hour=work_end,
        working_days=working_days,
        resource_calendars=resource_calendars,
        resource_working_days=resource_working_days,
        case_attribute_behaviour=session.get("case_attribute_behaviour") or {},
    )

    audit = _build_modification_audit(deserialize_model(observations_data), mods) if mods else []

    try:
        result = run_simulation(observations, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {e}")

    # Build and store the regenerated event log from this run (run 1 / deterministic seed).
    # Cases include per-event timestamps; strip them from the response after log generation.
    try:
        log_end_iso = (observations.get("log_metrics") or {}).get("log_end_iso")
        sim_log_df = build_simulation_log(result.get("cases", []), config, log_end_iso)
        session_store.update(session_id, last_simulation_log=sim_log_df.to_json(orient="split", date_format="iso"))
    except Exception:
        pass

    # Strip per-case events before returning (too large for the response)
    cases = result.pop("cases", [])
    for c in cases:
        c.pop("events", None)

    # Evaluate constraints against simulated cases
    if body.constraints and cases:
        result["constraint_results"] = _evaluate_constraints(body.constraints, cases, result)

    result["modification_audit"] = audit
    result.pop("cases", None)
    return _to_json_compatible(result)


@router.get("/session/{session_id}/observations-recommendations")
async def observations_recommendations(session_id: str):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    observations_data = session.get("baseline_model") or session.get("discovered_model")
    if observations_data is None:
        raise HTTPException(status_code=400, detail="Observations not yet extracted")

    observations = deserialize_model(observations_data)
    try:
        items = _collect_recommendations(observations)
    except Exception as e:
        import traceback; traceback.print_exc()
        items = []

    try:
        sim_recs = _collect_simulation_impact_recommendations(observations)
    except Exception as e:
        import traceback; traceback.print_exc()
        sim_recs = []

    ca_observations = session.get("case_attribute_observations") or []
    ca_recs: list = []
    if ca_observations:
        try:
            ca_recs = _collect_case_attribute_recommendations(ca_observations, observations)
        except Exception as e:
            import traceback; traceback.print_exc()

    all_items = items + sim_recs + ca_recs

    # Keep top 50 by impact within each observation_type category, then re-sort globally
    _CAT = {
        "conditional_time": "time", "top_duration_driver": "time", "arrival_demand_relief": "time",
        "resource_routing": "resource", "capacity_bottleneck": "resource",
        "sequence": "control_flow", "routing_fast_track": "control_flow",
        "parallel_contention": "control_flow", "conditional_routing": "control_flow",
        "decision_point_routing": "control_flow",
        "conditional_rework": "rework", "expensive_rework": "rework",
        "case_attribute": "case_attribute",
    }
    from collections import defaultdict
    by_cat: dict = defaultdict(list)
    for r in all_items:
        cat = _CAT.get(r["observation_type"], r["observation_type"])
        by_cat[cat].append(r)

    capped: list = []
    for cat_items in by_cat.values():
        capped.extend(sorted(cat_items, key=lambda x: -x["impact"])[:50])

    return {"recommendations": sorted(capped, key=lambda x: -x["impact"])}


    return {"recommendations": sorted(capped, key=lambda x: -x["impact"])}


# ── Parse-observation endpoint ──────────────────────────────────────────────────────

import re as _re

_INCREASE = r'(?:increase|add|raise|grow|expand)'
_REDUCE   = r'(?:reduce|decrease|cut|lower|shrink|drop)'
_BY_PCT   = r'by\s+(\d+(?:\.\d+)?)\s*%'
_BY_N     = r'by\s+(\d+(?:\.\d+)?)\s*(?:slot|slots)?'

def _fuzzy_match(name: str, candidates: List[str]) -> Optional[str]:
    """Return best candidate containing name (case-insensitive), or None."""
    if not candidates:
        return None
    name_l = name.lower().strip()
    # exact
    for c in candidates:
        if c.lower() == name_l:
            return c
    # substring
    for c in candidates:
        if name_l in c.lower():
            return c
    # any word overlap
    words = set(name_l.split())
    best, best_score = None, 0
    for c in candidates:
        score = len(words & set(c.lower().split()))
        if score > best_score:
            best, best_score = c, score
    return best if best_score > 0 else None


# ── Parse-observation endpoint ──────────────────────────────────────────────────────

@router.post("/session/{session_id}/parse-scenario")
async def parse_observation(session_id: str, body: ParseScenarioRequest):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    observations_data = session.get("baseline_model") or session.get("discovered_model")
    cab = session.get("case_attribute_behaviour") or {}

    known_activities: List[str] = []
    known_resources: List[str] = []
    known_attr_values: Dict[str, List[str]] = {}
    end_activities: List[str] = []
    start_activities: List[str] = []
    if observations_data:
        a = deserialize_model(observations_data)
        ta = a.get("activity_durations_df")
        ra = a.get("resource_assignments_df")
        if ta is not None and not ta.empty:
            known_activities = ta["activity"].tolist()
        if ra is not None and not ra.empty:
            known_resources = ra["resource"].unique().tolist()
        end_activities = list(a.get("end_activities") or [])
        start_activities = list(a.get("start_activities") or [])
    for attr, attr_data in cab.items():
        known_attr_values[str(attr)] = [v["value"] for v in attr_data.get("values", [])]

    # Try LLM-based parsing first; fall back to regex if unavailable
    try:
        result = parse_observation_with_llm(
            body.text, known_activities, known_resources, known_attr_values,
            _segment_note, cab,
            end_activities=end_activities, start_activities=start_activities,
        )
        if result is not None:
            return result
    except Exception as e:
        import logging
        logging.getLogger("parse_observation").warning(f"LLM parse failed, falling back to regex: {e}")

    return _parse_observation(body.text, known_activities, known_resources, known_attr_values, cab)


import re as _re


def _fuzzy_match(name: str, candidates: List[str]) -> Optional[str]:
    """Return best candidate by substring/stem overlap (case-insensitive)."""
    if not candidates:
        return None
    name_l = name.lower().strip()

    def stem(w: str) -> str:
        for sfx in ('ing', 'tions', 'tion', 'ied', 'ies', 'ness', 'ments', 'ment', 'ers', 'er', 'es', 's'):
            if w.endswith(sfx) and len(w) - len(sfx) >= 3:
                return w[:-len(sfx)]
        return w

    # 1. Exact
    for c in candidates:
        if c.lower() == name_l:
            return c
    # 2. Substring
    for c in candidates:
        if name_l in c.lower():
            return c
    # 3. Stem overlap
    name_stems = {stem(w) for w in name_l.split()}
    best, best_score = None, 0
    for c in candidates:
        c_words = set(c.lower().split())
        c_stems = {stem(w) for w in c_words}
        score = len(name_stems & c_stems)
        # partial stem substring
        partial = sum(1 for ns in name_stems for cw in c_words if len(ns) >= 3 and (ns in cw or cw in ns))
        total = score * 3 + partial
        if total > best_score:
            best, best_score = c, total
    return best if best_score > 0 else None


def _ranked_matches(name: str, candidates: List[str], top_n: int = 3) -> List[str]:
    """Return top_n candidates ranked by word overlap with stemming tolerance."""
    if not candidates:
        return []
    name_l = name.lower().strip()
    name_words = set(name_l.split())

    def stem(w: str) -> str:
        for sfx in ('ing', 'tions', 'tion', 'ied', 'ies', 'ness', 'ments', 'ment', 'ers', 'er', 'es', 's'):
            if w.endswith(sfx) and len(w) - len(sfx) >= 3:
                return w[:-len(sfx)]
        return w

    name_stems = {stem(w) for w in name_words}

    scored = []
    for c in candidates:
        c_l = c.lower()
        if c_l == name_l:
            return [c]
        sub_score = 2 if name_l in c_l else (1 if c_l in name_l else 0)
        c_words = set(c_l.split())
        c_stems = {stem(w) for w in c_words}
        word_score = len(name_stems & c_stems)
        # partial: any name stem appears as substring in any candidate word
        partial = sum(1 for ns in name_stems for cw in c_words if len(ns) >= 3 and (ns in cw or cw in ns))
        scored.append((sub_score * 10 + word_score * 3 + partial, c))

    scored.sort(reverse=True)
    return [c for score, c in scored[:top_n] if score > 0]


def _extract_number(text: str) -> Optional[float]:
    """Extract the first number from text."""
    m = _re.search(r'(\d+(?:\.\d+)?)', text)
    return float(m.group(1)) if m else None


def _extract_pct(text: str) -> Optional[float]:
    """Extract the first percentage from text."""
    m = _re.search(r'(\d+(?:\.\d+)?)\s*%', text)
    return float(m.group(1)) if m else None


def _is_increase(text: str) -> bool:
    return bool(_re.search(r'\b(?:increase|add|raise|grow|expand|more)\b', text, _re.IGNORECASE))


def _is_reduce(text: str) -> bool:
    return bool(_re.search(r'\b(?:reduce|decrease|cut|lower|shrink|drop|less|fewer|limit|restrict|cap|not\s+more)\b', text, _re.IGNORECASE))


def _intent(text: str) -> str:
    """Classify the high-level intent of the observation text."""
    t = text.lower()
    # Visit cap patterns — check before duration/rework since "no more than 2 reviews" can conflict
    if _re.search(r'\b(?:no\s+more\s+than|at\s+most|maximum|max|cap|limit|only|up\s+to)\b.*\b\d+\b', t):
        # "no more than 2 X", "at most 2 X", "max 2 X", "up to 2 X per case"
        # Distinguish from percentage: if there's a % sign it's not a visit cap
        if '%' not in t:
            return 'max_visits'
    if _re.search(r'\b(?:must\s+not|should\s+not|cannot|never)\s+(?:happen|occur|appear|repeat|loop|execute)\b', t):
        return 'max_visits'  # "must not repeat" → cap at 1
    if _re.search(r'\b(?:from|start|between).*\bto\b.*\b(?:must|should|cannot|≤|<=|≥|>=|exceed|longer|shorter)\b', t):
        return 'time_span'
    if _re.search(r'\b(?:must\s+not|should\s+not)\s+(?:occur|appear|exist|happen|be\s+present)\b', t):
        return 'activity_absence'
    if _re.search(r'\b(?:must|should|always)\s+(?:occur|appear|exist|happen|be\s+present)\b', t):
        return 'activity_presence'
    if _re.search(r'\b(?:rework|redo|repeat|loop)\b.*\b(?:rate|percent|%)\b', t):
        return 'rework_mod' if ('%' in t and (_is_increase(t) or _is_reduce(t))) else 'rework_constraint'
    if _re.search(r'\b(?:rework|redo)\s+rate\b.*\b(?:≤|<=|must\s+not\s+exceed|not\s+more\s+than)\b', t):
        return 'rework_constraint'
    if _re.search(r'\b(?:capacity|slot|slots|resource)\b', t) and ('%' in t or _re.search(r'\badd\s+\d+\b', t)):
        return 'capacity'
    if _re.search(r'\b(?:utilis|utiliz)\w*\b.*\b(?:≤|<=|must\s+not\s+exceed|not\s+more\s+than|below)\b', t):
        return 'utilisation_constraint'
    if _re.search(r'\b(?:arrival|arrivals|incoming)\b.*(?:%|\d)', t):
        return 'arrival'
    if _re.search(r'\b(?:duration|time|faster|slower|speed|throughput)\b.*%', t) or \
       (_re.search(r'\b(?:reduce|increase)\b.*\b(?:by)\b.*%', t) and not _re.search(r'\brework\b', t)):
        return 'duration'
    if _re.search(r'%', t) and (_is_increase(t) or _is_reduce(t)):
        return 'duration'  # fallback: percentage change with directional verb
    return 'unknown'


def _parse_observation(text: str, activities: List[str], resources: List[str],
                attr_values: Dict[str, List[str]], cab: dict) -> dict:
    """
    Unified observation parser. Intent-first, flexible wording, fuzzy activity matching.
    Returns:
      { understood_as, modifications, constraint, notes, engine_enforced, confidence }
    """
    t = text.strip()
    notes: List[str] = []
    intent = _intent(t)

    def _act_result(raw: str) -> tuple:
        """(matched_activity, confidence, suggestion_note)"""
        if not activities:
            return raw, "low", None
        exact = _fuzzy_match(raw, activities)
        if exact:
            # Check if multiple activities match similarly — if so, note the ambiguity
            top = _ranked_matches(raw, activities, top_n=5)
            if len(top) > 1 and top[0] == exact:
                others = [a for a in top[1:] if a != exact]
                if others:
                    return exact, "medium", f"Matched '{exact}'. Other similar activities: {', '.join(repr(a) for a in others[:2])}. Rename the observation to be more specific if needed."
            return exact, "high", None
        # No match — find top suggestions
        top = _ranked_matches(raw, activities, top_n=3)
        if top:
            return raw, "low", f"Activity '{raw}' not found. Did you mean: {', '.join(repr(a) for a in top)}?"
        return raw, "low", f"Activity '{raw}' not found in this log."

    def _res_result(raw: str) -> tuple:
        if not resources:
            return raw, "low", None
        exact = _fuzzy_match(raw, resources)
        if exact:
            return exact, "high", None
        top = _ranked_matches(raw, resources, top_n=3)
        if top:
            return raw, "low", f"Resource '{raw}' not found. Did you mean: {', '.join(repr(r) for r in top)}?"
        return raw, "low", f"Resource '{raw}' not found in this log."

    # ── MAX VISITS ────────────────────────────────────────────────────────────
    if intent == 'max_visits':
        # Extract the number
        cap_raw = _extract_number(t)
        cap = int(cap_raw) if cap_raw is not None else 1

        # Extract activity name: what's the noun/phrase after the number?
        # Try several patterns
        raw_act = None
        m = _re.search(r'(?:no\s+more\s+than|at\s+most|maximum|max|up\s+to)\s+\d+\s+([^\s,\.]+(?:\s+[^\s,\.]+){0,4}?)\s*(?:per\s+case|times?|occurrences?|visits?|loops?|repeats?|$)', t, _re.IGNORECASE)
        if m:
            raw_act = m.group(1).strip()
        else:
            # "only 2 reviews", "limit reviews to 2", "cap reviews at 2"
            m2 = _re.search(r'(?:only|limit|cap)\s+([^\s,\.]+(?:\s+[^\s,\.]+){0,3}?)\s+(?:to|at)\s+\d+', t, _re.IGNORECASE)
            if m2:
                raw_act = m2.group(1).strip()
            else:
                # Grab nouns around the number
                m3 = _re.search(r'\d+\s+([^\s,\.]+(?:\s+[^\s,\.]+){0,2})', t, _re.IGNORECASE)
                if m3:
                    raw_act = m3.group(1).strip()

        if not raw_act:
            return _cannot_parse(t)

        # Clean trailing noise from activity name
        raw_act = _re.sub(r'\s+(?:per\s+case|times?|occurrences?|activit\w+|$)', '', raw_act, flags=_re.IGNORECASE).strip()

        act, conf, act_note = _act_result(raw_act)
        if act_note:
            notes.append(act_note)

        mod: Dict[str, Any] = {
            "type": "max_visits", "activity": act,
            "change_type": "set_value", "value": float(cap),
            "description": f"'{act}' at most {cap} time(s) per case",
            "baseline_value": 0.0, "modified_value": float(cap),
        }
        return {"understood_as": f"'{act}' occurs at most {cap} time(s) per case",
                "modifications": [mod], "constraint": None, "notes": notes,
                "engine_enforced": True, "confidence": conf}

    # ── DURATION ──────────────────────────────────────────────────────────────
    if intent == 'duration':
        pct = _extract_pct(t)
        if pct is None:
            return _cannot_parse(t)
        is_red = _is_reduce(t)
        change_type = "reduce_pct" if is_red else "increase_pct"
        direction_word = "reduce" if is_red else "increase"

        # Extract activity: everything between direction verb and "by X%"
        raw_act = None
        m = _re.search(rf'(?:reduce|increase|cut|lower|raise|add)\s+["\']?(.+?)["\']?\s+by\s+\d', t, _re.IGNORECASE)
        if m:
            raw_act = m.group(1).strip()
        else:
            # "X% faster/slower for [activity]" or "[activity] by X%"
            m2 = _re.search(r'(?:for|of|at|on)\s+["\']?(.+?)["\']?\s+(?:by\s+\d|$)', t, _re.IGNORECASE)
            if m2:
                raw_act = m2.group(1).strip()

        if not raw_act:
            return _cannot_parse(t)

        act, conf, act_note = _act_result(raw_act)
        if act_note:
            notes.append(act_note)

        mod = {"type": "time", "activity": act, "change_type": change_type,
               "value": pct, "description": f"'{act}' duration {direction_word} {pct:.0f}%",
               "baseline_value": 0.0, "modified_value": 0.0}
        return {"understood_as": f"{direction_word} '{act}' duration by {pct:.0f}%",
                "modifications": [mod], "constraint": None, "notes": notes,
                "engine_enforced": True, "confidence": conf}

    # ── CAPACITY ──────────────────────────────────────────────────────────────
    if intent == 'capacity':
        pct = _extract_pct(t)
        # "add N slots to [resource]"
        m_slots = _re.search(r'add\s+(\d+)\s+slots?\s+to\s+["\']?(.+?)["\']?\s*$', t, _re.IGNORECASE)
        m_cap = _re.search(r'(?:capacity\s+of\s+|slots?\s+(?:for|to)\s+)["\']?(.+?)["\']?\s+by\s+\d', t, _re.IGNORECASE)
        # "add X% to resource capacity [for segment]"
        m_cap2 = _re.search(r'(?:add|increase|raise)\s+\d+(?:\.\d+)?\s*%\s+to\s+(?:resource\s+)?capacity', t, _re.IGNORECASE)

        if m_slots:
            n_slots = int(m_slots.group(1))
            raw_res = m_slots.group(2).strip()
            res, conf, res_note = _res_result(raw_res)
            if res_note: notes.append(res_note)
            seg_note = _segment_note(t, cab, attr_values)
            if seg_note: notes.append(seg_note)
            mod = {"type": "resource_capacity", "resource": res, "change_type": "set_value",
                   "value": float(n_slots), "description": f"'{res}' +{n_slots} slot(s)",
                   "baseline_value": 0.0, "modified_value": 0.0}
            return {"understood_as": f"Add {n_slots} slot(s) to '{res}'",
                    "modifications": [mod], "constraint": None, "notes": notes,
                    "engine_enforced": True, "confidence": conf}
        elif m_cap:
            raw_res = m_cap.group(1).strip()
            res, conf, res_note = _res_result(raw_res)
            if res_note: notes.append(res_note)
            seg_note = _segment_note(t, cab, attr_values)
            if seg_note: notes.append(seg_note)
            direction_word = "increase" if _is_increase(t) else "reduce"
            change_type = "increase_pct" if direction_word == "increase" else "reduce_pct"
            mod = {"type": "resource_capacity", "resource": res, "change_type": change_type,
                   "value": pct or 10.0, "description": f"'{res}' capacity {direction_word} {pct:.0f}%",
                   "baseline_value": 0.0, "modified_value": 0.0}
            return {"understood_as": f"{direction_word} capacity of '{res}' by {pct:.0f}%",
                    "modifications": [mod], "constraint": None, "notes": notes,
                    "engine_enforced": True, "confidence": conf}
        else:  # m_cap2
            seg_note = _segment_note(t, cab, attr_values)
            if seg_note: notes.append(seg_note)
            mod = {"type": "resource_capacity", "resource": "all resources", "change_type": "increase_pct",
                   "value": pct or 10.0, "description": f"capacity increase {pct:.0f}%",
                   "baseline_value": 0.0, "modified_value": 0.0}
            return {"understood_as": f"increase capacity of all resources by {pct:.0f}%",
                    "modifications": [mod], "constraint": None, "notes": notes,
                    "engine_enforced": True, "confidence": "medium"}

    # ── ARRIVAL ───────────────────────────────────────────────────────────────
    if intent == 'arrival':
        pct = _extract_pct(t)
        if pct is None:
            return _cannot_parse(t)
        direction_word = "reduce" if _is_reduce(t) else "increase"
        change_type = "reduce_pct" if direction_word == "reduce" else "increase_pct"
        mod = {"type": "arrival", "change_type": change_type, "value": pct,
               "description": f"Arrival rate {direction_word} {pct:.0f}%",
               "baseline_value": 0.0, "modified_value": 0.0}
        return {"understood_as": f"{direction_word} arrival rate by {pct:.0f}%",
                "modifications": [mod], "constraint": None, "notes": notes,
                "engine_enforced": True, "confidence": "high"}

    # ── REWORK MODIFICATION ───────────────────────────────────────────────────
    if intent == 'rework_mod':
        pct = _extract_pct(t)
        if pct is None:
            return _cannot_parse(t)
        direction_word = "reduce" if _is_reduce(t) else "increase"
        change_type = "reduce_pct" if direction_word == "reduce" else "increase_pct"
        # Extract activity
        raw_act = None
        for pat in [r'rework\s+(?:rate\s+)?(?:at|for|of)\s+["\']?(.+?)["\']?\s+by',
                    r'(?:at|for|of)\s+["\']?(.+?)["\']?\s+rework']:
            m = _re.search(pat, t, _re.IGNORECASE)
            if m:
                raw_act = m.group(1).strip()
                break
        if not raw_act:
            return _cannot_parse(t)
        act, conf, act_note = _act_result(raw_act)
        if act_note: notes.append(act_note)
        mod = {"type": "rework_rate", "activity": act, "change_type": change_type,
               "value": pct, "description": f"'{act}' rework {direction_word} {pct:.0f}%",
               "baseline_value": 0.0, "modified_value": 0.0}
        return {"understood_as": f"{direction_word} rework rate at '{act}' by {pct:.0f}%",
                "modifications": [mod], "constraint": None, "notes": notes,
                "engine_enforced": True, "confidence": conf}

    # ── TIME SPAN CONSTRAINT ──────────────────────────────────────────────────
    if intent == 'time_span':
        m = _re.search(
            r'from\s+["\']?(.+?)["\']?\s+to\s+["\']?(.+?)["\']?\s+'
            r'(?:must\s+not\s+(?:take|exceed)|should\s+not\s+(?:take|exceed)|≤|<=|<|not\s+(?:more|longer)\s+than)\s+'
            r'(\d+(?:\.\d+)?)\s*(day|days|hour|hours|hr|hrs|minute|minutes|min)',
            t, _re.IGNORECASE)
        if not m:
            m = _re.search(
                r'from\s+["\']?(.+?)["\']?\s+to\s+["\']?(.+?)["\']?\s+'
                r'(?:must\s+(?:take|be)\s+(?:at\s+least|more\s+than)|≥|>=|>)\s+'
                r'(\d+(?:\.\d+)?)\s*(day|days|hour|hours|hr|hrs|minute|minutes|min)',
                t, _re.IGNORECASE)
            op = "gte" if m else None
        else:
            op = "lte"
        if m and op:
            raw_a, raw_b = m.group(1).strip(), m.group(2).strip()
            val, unit_raw = float(m.group(3)), m.group(4).lower()
            unit = "days" if "day" in unit_raw else "hours" if "hr" in unit_raw or "hour" in unit_raw else "minutes"
            act_a, ca, na = _act_result(raw_a)
            act_b, cb, nb = _act_result(raw_b)
            if na: notes.append(na)
            if nb: notes.append(nb)
            notes.append("The engine cannot enforce time-span constraints during routing — this will be checked post-run.")
            op_sym = "≤" if op == "lte" else "≥"
            constraint = {"type": "time_span", "text": text, "activity_a": act_a,
                          "activity_b": act_b, "operator": op, "threshold": val, "unit": unit}
            return {"understood_as": f"from '{act_a}' to '{act_b}' {op_sym} {val} {unit}",
                    "modifications": [], "constraint": constraint, "notes": notes,
                    "engine_enforced": False,
                    "confidence": "high" if (ca == "high" and cb == "high") else "medium"}
        return _cannot_parse(t)

    # ── ACTIVITY ABSENCE ─────────────────────────────────────────────────────
    if intent == 'activity_absence':
        raw_act = None
        m = _re.search(r'["\']?(.+?)["\']?\s+(?:must\s+not|should\s+not|cannot|never)\s+(?:occur|appear|happen|be)', t, _re.IGNORECASE)
        if m:
            raw_act = m.group(1).strip()
        if not raw_act:
            return _cannot_parse(t)
        act, conf, act_note = _act_result(raw_act)
        if act_note: notes.append(act_note)
        notes.append("Checked post-run — the engine does not prevent activities during routing.")
        constraint = {"type": "activity_absence", "text": text, "activity": act,
                      "operator": "lte", "threshold": 0.0, "unit": "pct"}
        return {"understood_as": f"'{act}' must not occur",
                "modifications": [], "constraint": constraint, "notes": notes,
                "engine_enforced": False, "confidence": conf}

    # ── ACTIVITY PRESENCE ────────────────────────────────────────────────────
    if intent == 'activity_presence':
        raw_act = None
        m = _re.search(r'["\']?(.+?)["\']?\s+(?:must|should|always)\s+(?:occur|appear|happen)', t, _re.IGNORECASE)
        if m:
            raw_act = m.group(1).strip()
        if not raw_act:
            return _cannot_parse(t)
        act, conf, act_note = _act_result(raw_act)
        if act_note: notes.append(act_note)
        notes.append("Checked post-run — the engine does not force activities during routing.")
        constraint = {"type": "activity_presence", "text": text, "activity": act,
                      "operator": "gte", "threshold": 0.0, "unit": "pct"}
        return {"understood_as": f"'{act}' must occur in every case",
                "modifications": [], "constraint": constraint, "notes": notes,
                "engine_enforced": False, "confidence": conf}

    # ── UTILISATION CONSTRAINT ───────────────────────────────────────────────
    if intent == 'utilisation_constraint':
        val = _extract_pct(t)
        raw_res = None
        m = _re.search(r'["\']?(.+?)["\']?\s+utili[sz]', t, _re.IGNORECASE)
        if m:
            raw_res = m.group(1).strip()
        if not raw_res or val is None:
            return _cannot_parse(t)
        res, conf, res_note = _res_result(raw_res)
        if res_note: notes.append(res_note)
        notes.append("Checked post-run against simulated resource utilisation.")
        constraint = {"type": "resource_utilisation", "text": text, "resource": res,
                      "operator": "lte", "threshold": val, "unit": "pct"}
        return {"understood_as": f"'{res}' utilisation ≤ {val}%",
                "modifications": [], "constraint": constraint, "notes": notes,
                "engine_enforced": False, "confidence": conf}

    # ── REWORK CONSTRAINT ────────────────────────────────────────────────────
    if intent == 'rework_constraint':
        val = _extract_pct(t)
        if val is None:
            return _cannot_parse(t)
        notes.append("Checked post-run against simulated rework rate.")
        constraint = {"type": "rework_rate", "text": text, "operator": "lte", "threshold": val, "unit": "pct"}
        return {"understood_as": f"rework rate ≤ {val}%",
                "modifications": [], "constraint": constraint, "notes": notes,
                "engine_enforced": False, "confidence": "high"}

    return _cannot_parse(t)


def _cannot_parse(text: str) -> dict:
    return {
        "understood_as": None,
        "modifications": [], "constraint": None, "notes": [],
        "engine_enforced": False, "confidence": "low",
        "reason": f"Could not understand '{text}'.",
        "guidance": (
            "Examples of supported observations:\n"
            "• 'ensure no more than 2 reviews per case'  → engine caps activity visits\n"
            "• 'eliminate 30% of preparation time'  → duration modification\n"
            "• 'increase capacity of SID-042 by 20%'  → capacity modification\n"
            "• 'add 2 slots to SID-042'  → capacity modification\n"
            "• 'prevent rework at Review by reducing it 50%'  → rework modification\n"
            "• 'reduce arrival rate by 10%'  → arrival modification\n"
            "• 'enforce Submit to Close under 30 days'  → post-run time check\n"
            "• 'prevent Review from occurring'  → post-run presence check\n"
            "• 'SID-042 utilisation must not exceed 80%'  → post-run utilisation check\n"
            "• 'rework rate must not exceed 10%'  → post-run rework check"
        ),
    }


def _segment_note(text: str, cab: dict, attr_values: Dict[str, List[str]]) -> Optional[str]:
    """If the observation text mentions a known attribute value, return an approximation note.
    Requires whole-word match and minimum 3-character value to avoid false positives."""
    import re as _re2
    text_l = text.lower()
    for attr, vals in attr_values.items():
        for val in vals:
            if len(val) < 3:
                continue
            # Whole-word boundary match
            if _re2.search(r'\b' + _re2.escape(val.lower()) + r'\b', text_l):
                attr_data = cab.get(attr, {})
                share = next((v["share"] for v in attr_data.get("values", []) if v["value"] == val), None)
                if share is not None:
                    return (f"Modification applies globally — '{attr}={val}' cases "
                            f"({share*100:.0f}% of total) benefit proportionally.")
    return None


def _evaluate_constraints(constraints: list, cases: list, result: dict) -> list:
    """Evaluate each constraint against simulated cases and aggregate results."""
    from collections import Counter as _Counter
    out = []
    n = max(len(cases), 1)

    for c in constraints:
        ctype = c["type"] if isinstance(c, dict) else c.type
        text  = c["text"] if isinstance(c, dict) else c.text
        op    = c["operator"] if isinstance(c, dict) else c.operator
        thr   = float(c["threshold"] if isinstance(c, dict) else c.threshold)
        unit  = c.get("unit") if isinstance(c, dict) else c.unit

        def _threshold_in_minutes(thr: float, unit: Optional[str]) -> float:
            if unit == "days":   return thr * 24 * 60
            if unit == "hours":  return thr * 60
            return thr  # minutes or count/pct — already correct unit

        try:
            if ctype == "time_span":
                act_a = c.get("activity_a") if isinstance(c, dict) else c.activity_a
                act_b = c.get("activity_b") if isinstance(c, dict) else c.activity_b
                thr_min = _threshold_in_minutes(thr, unit)
                violations = 0
                for case in cases:
                    acts = case.get("activities", [])
                    durs = case.get("activity_durations", {})
                    waits = case.get("activity_waits", {})
                    if act_a not in acts or act_b not in acts:
                        continue
                    # Approximate span as cumulative duration+wait from first occurrence of A to last of B
                    idx_a = acts.index(act_a)
                    idx_b = len(acts) - 1 - acts[::-1].index(act_b)
                    span = sum((durs.get(acts[i], 0) + waits.get(acts[i], 0)) for i in range(idx_a, idx_b + 1))
                    if (op == "lte" and span > thr_min) or (op == "gte" and span < thr_min):
                        violations += 1
                viol_pct = round(violations / n * 100, 1)
                out.append({"text": text, "type": ctype, "passed": viol_pct == 0.0,
                            "violation_pct": viol_pct, "threshold": thr, "unit": unit})

            elif ctype == "activity_presence":
                act = c.get("activity") if isinstance(c, dict) else c.activity
                violations = sum(1 for case in cases if act not in case.get("activities", []))
                viol_pct = round(violations / n * 100, 1)
                out.append({"text": text, "type": ctype, "passed": viol_pct == 0.0,
                            "violation_pct": viol_pct, "threshold": 0, "unit": "pct"})

            elif ctype == "activity_absence":
                act = c.get("activity") if isinstance(c, dict) else c.activity
                violations = sum(1 for case in cases if act in case.get("activities", []))
                viol_pct = round(violations / n * 100, 1)
                out.append({"text": text, "type": ctype, "passed": viol_pct == 0.0,
                            "violation_pct": viol_pct, "threshold": 0, "unit": "pct"})

            elif ctype == "activity_count":
                act = c.get("activity") if isinstance(c, dict) else c.activity
                violations = 0
                for case in cases:
                    cnt = _Counter(case.get("activities", []))[act]
                    if (op == "lte" and cnt > thr) or (op == "gte" and cnt < thr):
                        violations += 1
                viol_pct = round(violations / n * 100, 1)
                out.append({"text": text, "type": ctype, "passed": viol_pct == 0.0,
                            "violation_pct": viol_pct, "threshold": thr, "unit": "count"})

            elif ctype == "resource_utilisation":
                res = c.get("resource") if isinstance(c, dict) else c.resource
                util_dict = result.get("resource_utilization", {})
                if res:
                    # Specific resource
                    actual = float(util_dict.get(res, 0.0))
                    passed = (actual <= thr) if op == "lte" else (actual >= thr)
                    out.append({"text": text, "type": ctype, "passed": passed,
                                "actual_value": actual, "threshold": thr, "unit": "pct"})
                else:
                    # No specific resource — check what % of all resources violate the threshold
                    if not util_dict:
                        out.append({"text": text, "type": ctype, "passed": True,
                                    "violation_pct": 0.0, "threshold": thr, "unit": "pct"})
                    else:
                        violations = sum(1 for u in util_dict.values()
                                        if (op == "lte" and u > thr) or (op == "gte" and u < thr))
                        viol_pct = round(violations / len(util_dict) * 100, 1)
                        worst = max(util_dict.values()) if op == "lte" else min(util_dict.values())
                        out.append({"text": text, "type": ctype, "passed": viol_pct == 0.0,
                                    "violation_pct": viol_pct, "actual_value": round(worst, 1),
                                    "threshold": thr, "unit": "pct"})

            elif ctype == "rework_rate":
                actual = float(result.get("rework_rate_pct", 0.0))
                passed = (actual <= thr) if op == "lte" else (actual >= thr)
                out.append({"text": text, "type": ctype, "passed": passed,
                            "actual_value": actual, "threshold": thr, "unit": "pct"})

        except Exception:
            out.append({"text": text, "type": ctype, "passed": False,
                        "violation_pct": None, "threshold": thr, "unit": unit,
                        "error": "Could not evaluate this constraint"})

    return out


# ── Scenario modification logic ───────────────────────────────────────────────

def _build_modification_audit(observations: Dict[str, Any], modifications: list) -> list:
    """Return per-modification diagnostic records: what matched, what changed, expected impact."""
    ra = observations.get("resource_assignments_df")
    ta = observations.get("activity_durations_df")
    records = []
    for mod in modifications:
        t = mod["type"]
        act = mod.get("activity", "")
        rec: Dict[str, Any] = {"type": t, "activity": act, "matched": False}

        if t == "time":
            if ta is not None and not ta.empty:
                row = ta[ta["activity"] == act]
                if not row.empty:
                    rec["matched"] = True
                    rec["old_mean_min"] = round(float(row.iloc[0]["mean_minutes"]), 1)
                    factor = _mod_factor(mod)
                    rec["new_mean_min"] = round(rec["old_mean_min"] * factor, 1)
                    rec["pct_change"] = round((factor - 1) * 100, 1)

        elif t == "resource_share":
            res = mod.get("resource", "")
            rec["resource"] = res
            if ra is not None and not ra.empty:
                mask = (ra["activity"] == act) & (ra["resource"] == res)
                act_rows = ra[ra["activity"] == act]
                if mask.any():
                    rec["matched"] = True
                    old_prob = float(ra.loc[mask, "probability"].iloc[0])
                    ct = mod.get("change_type", "set_value")
                    if ct == "reduce_pct":
                        new_raw = old_prob * (1 - mod["value"] / 100)
                    elif ct == "increase_pct":
                        new_raw = old_prob * (1 + mod["value"] / 100)
                    else:
                        new_raw = mod["value"] / 100
                    new_raw = max(0.0, min(1.0, new_raw))
                    # Simulate renorm: replace this resource's prob and scale rest
                    other_total = float(act_rows.loc[~mask, "probability"].sum())
                    new_total = new_raw + other_total
                    new_prob = new_raw / new_total if new_total > 0 else new_raw

                    rec["old_probability"] = round(old_prob, 4)
                    rec["new_probability"] = round(new_prob, 4)
                    rec["resource_mean_min"] = round(float(ra.loc[mask, "mean_duration_min"].iloc[0]), 1) if "mean_duration_min" in ra.columns else None
                    # Compute expected weighted avg service time before and after
                    if "mean_duration_min" in act_rows.columns and act_rows["mean_duration_min"].gt(0).any():
                        old_weighted = float((act_rows["probability"] * act_rows["mean_duration_min"]).sum())
                        # Simulate new probabilities
                        new_probs = act_rows["probability"].copy()
                        new_probs.loc[mask] = new_raw
                        new_probs = new_probs / new_probs.sum()
                        new_weighted = float((new_probs * act_rows["mean_duration_min"]).sum())
                        rec["old_weighted_mean_min"] = round(old_weighted, 1)
                        rec["new_weighted_mean_min"] = round(new_weighted, 1)
                        if old_weighted > 0:
                            rec["expected_service_pct_change"] = round((new_weighted - old_weighted) / old_weighted * 100, 1)
                    rec["num_resources_at_activity"] = int(len(act_rows))

        elif t == "parallel_rate":
            act_a = mod.get("activity_a", mod.get("activity", ""))
            act_b = mod.get("activity_b", "")
            rec["activity"] = f"{act_a} ∥ {act_b}"
            pp = observations.get("parallel_pairs_df")
            if pp is not None and not pp.empty:
                mask = (pp["activity_a"] == act_a) & (pp["activity_b"] == act_b)
                if not mask.any():
                    mask = (pp["activity_a"] == act_b) & (pp["activity_b"] == act_a)
                if mask.any():
                    rec["matched"] = True
                    old_rate = float(pp.loc[mask, "a_parallel_with_b_pct"].iloc[0])
                    change_type = mod.get("change_type", "set_value")
                    if change_type == "set_value":
                        new_rate = round(float(mod["value"]), 1)
                    else:
                        new_rate = round(old_rate * _mod_factor(mod), 1)
                    rec["old_rate"] = old_rate
                    rec["new_rate"] = new_rate

        elif t == "decision_point":
            gw = mod.get("activity", "")
            branches = mod.get("branches", [])
            rec["activity"] = gw
            sa = observations.get("routing_probabilities_df")
            if sa is not None and not sa.empty and gw and branches:
                total_new = sum(b["probability_pct"] for b in branches)
                old_parts, new_parts = [], []
                all_matched = True
                for branch in branches:
                    to_act = branch["to_activity"]
                    mask = (sa["from_activity"] == gw) & (sa["to_activity"] == to_act)
                    if mask.any():
                        old_p = round(float(sa.loc[mask, "probability"].iloc[0]) * 100, 1)
                        new_p = round(branch["probability_pct"] / max(total_new, 1) * 100, 1)
                        old_parts.append(f"{to_act}: {old_p}%")
                        new_parts.append(f"{to_act}: {new_p}%")
                    else:
                        all_matched = False
                if old_parts:
                    rec["matched"] = all_matched
                    rec["old_probability"] = ", ".join(old_parts)
                    rec["new_probability"] = ", ".join(new_parts)

        elif t == "sequence":
            frm = mod.get("from_activity", "")
            to  = mod.get("to_activity", "")
            rec["activity"] = f"{frm} → {to}"
            sa = observations.get("routing_probabilities_df")
            if sa is not None and not sa.empty:
                mask = (sa["from_activity"] == frm) & (sa["to_activity"] == to)
                if mask.any():
                    rec["matched"] = True
                    old_prob = float(sa.loc[mask, "probability"].iloc[0])
                    ct = mod.get("change_type", "set_value")
                    if ct == "reduce_pct":
                        raw = old_prob * (1 - mod["value"] / 100)
                    elif ct == "increase_pct":
                        raw = old_prob * (1 + mod["value"] / 100)
                    else:
                        raw = mod["value"] / 100.0
                    new_prob = max(0.0, min(1.0, raw))
                    rec["old_probability"] = round(old_prob * 100, 1)
                    rec["new_probability"] = round(new_prob * 100, 1)

        elif t == "arrival":
            arrival = observations.get("arrival_parameters") or {}
            old_rate = float(arrival.get("cases_per_day", 0))
            if old_rate > 0:
                rec["matched"] = True
                new_rate = round(old_rate * _mod_factor(mod), 2)
                rec["old_rate"] = old_rate
                rec["new_rate"] = new_rate
                rec["activity"] = "arrival rate"

        elif t == "resource_capacity":
            res = mod.get("resource", "")
            rec["resource"] = res
            cap_dict = observations.get("resource_capacity_dict") or {}
            peak_dict = observations.get("peak_concurrent_dict") or {}
            if res in cap_dict:
                rec["matched"] = True
                old_cap = cap_dict[res]
                new_cap = max(1, int(mod["value"])) if mod.get("change_type") == "set_value" else old_cap
                rec["old_capacity"] = old_cap
                rec["new_capacity"] = new_cap
                rec["peak_observed"] = peak_dict.get(res, old_cap)

        elif t == "rework_rate":
            if observations.get("rework_parameters_df") is not None:
                rw = observations["rework_parameters_df"]
                mask = rw["activity"] == act
                if mask.any():
                    rec["matched"] = True
                    col = "rework_rate_given_visit_pct" if "rework_rate_given_visit_pct" in rw.columns else "rework_rate_pct"
                    rec["old_rate"] = round(float(rw.loc[mask, col].iloc[0]), 1)
                    rec["new_rate"] = round(rec["old_rate"] * _mod_factor(mod), 1)

        elif t == "max_visits":
            rw = observations.get("rework_parameters_df")
            cap = int(mod.get("value", 1))
            if rw is not None and not rw.empty and (rw["activity"] == act).any():
                old_cap = int(rw.loc[rw["activity"] == act, "max_loops"].iloc[0]) + 1
                rec["matched"] = True
                rec["old_capacity"] = old_cap
                rec["new_capacity"] = cap
            else:
                # Activity exists in activity_durations but has no rework entry — cap will be injected
                ta = observations.get("activity_durations_df")
                if ta is not None and not ta.empty and (ta["activity"] == act).any():
                    rec["matched"] = True
                    rec["old_capacity"] = 1
                    rec["new_capacity"] = cap

        elif t == "case_attribute_dist":
            rec["matched"] = True  # applied via session, always takes effect

        records.append(rec)
    return records


def _apply_modifications(observations: Dict[str, Any], modifications: list) -> Dict[str, Any]:
    observations = copy.deepcopy(observations)
    for mod in modifications:
        t = mod["type"]
        if t == "case_attribute_dist":
            # Case attribute distributions are applied via session.case_attribute_behaviour
            # (read by SimulationConfig at request time), not via observations mutation.
            continue
        elif t == "time":
            factor = _mod_factor(mod)
            act = mod["activity"]
            mask = observations["activity_durations_df"]["activity"] == act
            observations["activity_durations_df"].loc[mask, "mean_minutes"] = (
                observations["activity_durations_df"].loc[mask, "mean_minutes"] * factor
            ).round(1)
            observations["activity_durations_df"].loc[mask, "std_minutes"] = (
                observations["activity_durations_df"].loc[mask, "std_minutes"] * factor
            ).round(1)
            # Also scale resource_assignments_df durations — the simulation uses resource-specific
            # durations first (resource_time_lookup) and only falls back to activity_durations_df
            # when no resource entry exists.  Without this, time modifications have no effect
            # on logs that have resource data.
            ra = observations.get("resource_assignments_df")
            if ra is not None and not ra.empty:
                rmask = ra["activity"] == act
                for col in ("mean_duration_min", "std_duration_min"):
                    if col in ra.columns:
                        observations["resource_assignments_df"].loc[rmask, col] = (
                            ra.loc[rmask, col] * factor
                        ).round(1)

        elif t == "decision_point":
            # branches lists only the visible (≥5%) paths with their new probabilities.
            # "others" (the minor branches, each <5%) absorb whatever probability remains
            # so the total always sums to 1.  We scale minor branches proportionally.
            gw = mod.get("activity", "")
            branches = mod.get("branches", [])
            if not branches or not gw:
                continue
            sa = observations["routing_probabilities_df"]
            gw_mask = sa["from_activity"] == gw
            if not gw_mask.any():
                continue

            # Named branch activities and their requested probabilities (normalised to 1)
            total_req = sum(b["probability_pct"] for b in branches)
            if total_req <= 0:
                continue
            named = {b["to_activity"]: b["probability_pct"] / total_req for b in branches}

            # Probability reserved for "others" = 1 − sum(named_probs)
            # Clamp to [0, 1] to handle floating point and user rounding errors
            named_sum = sum(named.values())
            others_budget = max(0.0, min(1.0, 1.0 - named_sum))

            # Current total probability of minor (unnamed) branches
            minor_mask = gw_mask & ~sa["to_activity"].isin(named.keys())
            minor_total = float(sa.loc[minor_mask, "probability"].sum())

            # Apply named branches
            for to_act, new_prob in named.items():
                mask = gw_mask & (sa["to_activity"] == to_act)
                if mask.any():
                    observations["routing_probabilities_df"].loc[mask, "probability"] = round(new_prob, 4)
                    if observations.get("prefix_tree"):
                        _apply_seq_to_prefix_tree(observations["prefix_tree"], gw, to_act, new_prob)

            # Scale minor branches proportionally so they fill the remaining budget
            if minor_total > 0 and others_budget > 0:
                scale = others_budget / minor_total
                for idx in sa.index[minor_mask]:
                    old_p = float(sa.at[idx, "probability"])
                    new_p = round(old_p * scale, 4)
                    observations["routing_probabilities_df"].at[idx, "probability"] = new_p
                    to_act = str(sa.at[idx, "to_activity"])
                    if observations.get("prefix_tree"):
                        _apply_seq_to_prefix_tree(observations["prefix_tree"], gw, to_act, new_p)
            elif others_budget == 0:
                # zero out minor branches entirely
                observations["routing_probabilities_df"].loc[minor_mask, "probability"] = 0.0

            # Final renormalisation to guard against floating point drift
            gw_total = observations["routing_probabilities_df"].loc[gw_mask, "probability"].sum()
            if gw_total > 0:
                observations["routing_probabilities_df"].loc[gw_mask, "probability"] = (
                    observations["routing_probabilities_df"].loc[gw_mask, "probability"] / gw_total
                ).round(4)

        elif t == "sequence":
            frm, to = mod["from_activity"], mod["to_activity"]
            mask = (
                (observations["routing_probabilities_df"]["from_activity"] == frm) &
                (observations["routing_probabilities_df"]["to_activity"] == to)
            )
            if not mask.any():
                raise ValueError(f"Cannot adjust missing direct transition: {frm} -> {to}")
            pt = observations.get("prefix_tree") or {}
            if pt and not any(
                key.split("|||")[-1] == frm and any(a == to for a, _ in edges)
                and any(a != to and p > 0 for a, p in edges)
                for key, edges in pt.items()
            ):
                raise ValueError(f"No alternative route exists for transition: {frm} -> {to}")
            change_type = mod.get("change_type", "set_value")
            if change_type in ("reduce_pct", "increase_pct"):
                current = float(observations["routing_probabilities_df"].loc[mask, "probability"].iloc[0]) if mask.any() else 0.0
                factor = (1.0 - mod["value"] / 100.0) if change_type == "reduce_pct" else (1.0 + mod["value"] / 100.0)
                new_prob = max(0.0, min(1.0, current * factor))
            else:
                new_prob = mod["value"] / 100.0
            observations["routing_probabilities_df"].loc[mask, "probability"] = round(new_prob, 4)
            # Pin the modified transition; scale other transitions from `frm` proportionally
            frm_mask = observations["routing_probabilities_df"]["from_activity"] == frm
            other_mask = frm_mask & ~mask
            other_total = float(observations["routing_probabilities_df"].loc[other_mask, "probability"].sum())
            remainder = max(0.0, 1.0 - new_prob)
            if other_total > 0:
                observations["routing_probabilities_df"].loc[other_mask, "probability"] = (
                    observations["routing_probabilities_df"].loc[other_mask, "probability"] / other_total * remainder
                ).round(4)
            # Propagate the same change into every prefix tree node whose last
            # activity is `frm` so the full-history routing reflects the modification.
            if observations.get("prefix_tree"):
                _apply_seq_to_prefix_tree(observations["prefix_tree"], frm, to, new_prob)

        elif t == "arrival":
            new_rate = observations["arrival_parameters"]["cases_per_day"] * _mod_factor(mod)
            observations["arrival_parameters"]["cases_per_day"] = round(new_rate, 2)
            observations["arrival_parameters"]["mean_inter_arrival_hours"] = round(24.0 / max(new_rate, 1e-9), 3)

        elif t == "resource_capacity":
            if "resource_capacity_dict" not in observations or observations["resource_capacity_dict"] is None:
                observations["resource_capacity_dict"] = {}
            observations["resource_capacity_dict"][mod["resource"]] = max(1, int(mod["value"]))

        elif t == "resource_share":
            ra = observations.get("resource_assignments_df")
            if ra is not None and not ra.empty:
                act, res = mod["activity"], mod["resource"]
                mask_ar = (ra["activity"] == act) & (ra["resource"] == res)
                current_prob = float(ra.loc[mask_ar, "probability"].iloc[0]) if mask_ar.any() else 0.0
                change_type = mod.get("change_type", "set_value")
                if change_type == "reduce_pct":
                    new_share = current_prob * (1.0 - mod["value"] / 100.0)
                elif change_type == "increase_pct":
                    new_share = current_prob * (1.0 + mod["value"] / 100.0)
                else:
                    new_share = mod["value"] / 100.0
                new_share = max(0.0, min(1.0, new_share))
                if mask_ar.any():
                    observations["resource_assignments_df"].loc[mask_ar, "probability"] = new_share
                else:
                    act_rows = ra[ra["activity"] == act]
                    mean_dur = float(act_rows["mean_duration_min"].mean()) if not act_rows.empty else 0.0
                    duration_observed = (
                        bool(act_rows["duration_observed"].fillna(False).any())
                        if "duration_observed" in act_rows.columns else mean_dur > 0
                    )
                    zero_share = (
                        float(act_rows["zero_duration_share"].dropna().iloc[0])
                        if "zero_duration_share" in act_rows.columns
                        and not act_rows["zero_duration_share"].dropna().empty
                        else float("nan")
                    )
                    is_instantaneous = (
                        bool(act_rows["is_instantaneous"].fillna(False).any())
                        if "is_instantaneous" in act_rows.columns else False
                    )
                    observations["resource_assignments_df"] = pd.concat([
                        observations["resource_assignments_df"],
                        pd.DataFrame([{"activity": act, "resource": res, "probability": new_share,
                                       "mean_duration_min": mean_dur, "std_duration_min": 0.0,
                                       "duration_observed": duration_observed,
                                       "duration_source": "activity_resource_copy",
                                       "duration_observation_count": 0,
                                       "zero_duration_share": zero_share,
                                       "is_instantaneous": is_instantaneous,
                                       "count": 0}])
                    ], ignore_index=True)
                # Pin the modified resource's share; scale others proportionally to fill remainder
                act_mask = observations["resource_assignments_df"]["activity"] == act
                other_mask = act_mask & (observations["resource_assignments_df"]["resource"] != res)
                other_total = float(observations["resource_assignments_df"].loc[other_mask, "probability"].sum())
                remainder = max(0.0, 1.0 - new_share)
                if other_total > 0:
                    observations["resource_assignments_df"].loc[other_mask, "probability"] = (
                        observations["resource_assignments_df"].loc[other_mask, "probability"] / other_total * remainder
                    ).round(4)
                elif remainder > 0:
                    # No other resources — just cap modified share at 1
                    observations["resource_assignments_df"].loc[act_mask & ~other_mask, "probability"] = 1.0

        elif t == "rework_rate":
            act = mod["activity"]
            factor = _mod_factor(mod)

            # 1. Update rework_parameters_df metadata columns + max_loops cap
            ra = observations.get("rework_parameters_df")
            if ra is not None and not ra.empty:
                mask = ra["activity"] == act
                if mask.any():
                    for col in ("rework_rate_pct", "rework_rate_given_visit_pct"):
                        if col in ra.columns:
                            observations["rework_parameters_df"].loc[mask, col] = (
                                ra.loc[mask, col] * factor
                            ).clip(0, 100).round(1)

            # Change the choice that enters a loop, not its mandatory return.
            # E.g. A -> RAI -> A is controlled at A -> RAI; RAI has no other exit.
            pt = observations.get("prefix_tree")
            if pt:
                def returns_to_target(dest, seen):
                    if dest == act:
                        return True
                    if dest in seen or len(seen) >= 20:
                        return False
                    outgoing = [(a, p) for a, p in pt.get(dest, []) if p > 0]
                    if not outgoing or (observations.get("terminal_probs") or {}).get(dest, 0) > 0:
                        return False
                    return all(returns_to_target(a, seen | {dest}) for a, _ in outgoing)

                # Classify all loop entrances before mutating any probabilities.
                entrances = {}
                for key, transitions in pt.items():
                    if act not in key.split("|||"):
                        continue
                    loop_dests = {a for a, _ in transitions if returns_to_target(a, set())}
                    if loop_dests and any(a not in loop_dests and p > 0 for a, p in transitions):
                        entrances[key] = loop_dests
                for key, loop_dests in entrances.items():
                    transitions = pt[key]
                    old_loop = sum(p for a, p in transitions if a in loop_dests)
                    other = sum(p for a, p in transitions if a not in loop_dests)
                    if other <= 0:
                        continue
                    scale = (1 - old_loop * factor) / other
                    pt[key] = [[a, round(p * (factor if a in loop_dests else scale), 6)]
                               for a, p in transitions]

            # Keep displayed first-order probabilities aligned with the actual
            # first-order prefix nodes, including the forward entrance to a loop.
            sa = observations.get("routing_probabilities_df")
            if sa is not None and pt:
                for source, group in sa.groupby("from_activity"):
                    probabilities = dict(pt.get(str(source), []))
                    for index, row in group.iterrows():
                        if row["to_activity"] in probabilities:
                            sa.at[index, "probability"] = probabilities[row["to_activity"]]

        elif t == "parallel_rate":
            act_a = mod.get("activity_a", mod.get("activity", ""))
            act_b = mod.get("activity_b", "")
            pp = observations.get("parallel_pairs_df")
            if pp is not None and not pp.empty:
                change_type = mod.get("change_type", "set_value")
                mask_ab = (pp["activity_a"] == act_a) & (pp["activity_b"] == act_b)
                mask_ba = (pp["activity_a"] == act_b) & (pp["activity_b"] == act_a)
                for mask in [mask_ab, mask_ba]:
                    if not mask.any():
                        continue
                    for col in ("a_parallel_with_b_pct", "b_parallel_with_a_pct"):
                        if col not in pp.columns:
                            continue
                        if change_type == "set_value":
                            observations["parallel_pairs_df"].loc[mask, col] = float(mod["value"])
                        else:
                            factor = _mod_factor(mod)
                            observations["parallel_pairs_df"].loc[mask, col] = (
                                pp.loc[mask, col] * factor
                            ).clip(0, 100).round(1)

        elif t == "max_visits":
            # Cap how many times an activity can occur per case.
            # value = max allowed visits (e.g. 2 means at most 2 times).
            # This overwrites the rework-derived cap in rework_parameters_df.
            act = mod.get("activity", "")
            cap = max(1, int(mod.get("value", 1)))
            ra = observations.get("rework_parameters_df")
            if ra is not None and not ra.empty and (ra["activity"] == act).any():
                observations["rework_parameters_df"].loc[ra["activity"] == act, "max_loops"] = cap - 1
            else:
                # Activity not in rework_parameters — add a row so the engine honours the cap
                new_row = pd.DataFrame([{
                    "activity": act, "cases_with_rework": 0, "cases_visiting": 0,
                    "rework_rate_pct": 0.0, "rework_rate_given_visit_pct": 0.0,
                    "mean_extra_loops": 0.0, "max_loops": cap - 1,
                }])
                observations["rework_parameters_df"] = pd.concat(
                    [ra if ra is not None else pd.DataFrame(), new_row], ignore_index=True
                )

    return observations


def _apply_seq_to_prefix_tree(prefix_tree: dict, from_act: str, to_act: str, new_prob: float) -> None:
    """
    Set the from_act → to_act probability to new_prob in every prefix tree node
    whose last activity is from_act, scaling all other outgoing edges proportionally.
    Only updates nodes where to_act already exists as an outgoing edge — the prefix
    tree never introduces transitions that weren't observed in the log.
    """
    SEP = "|||"
    for key in list(prefix_tree.keys()):
        last = key.split(SEP)[-1] if SEP in key else key
        if last != from_act:
            continue
        transitions = prefix_tree[key]
        acts_in_node = [a for a, _ in transitions]
        if to_act not in acts_in_node:
            continue
        other_total = sum(p for a, p in transitions if a != to_act)
        if other_total < 1e-9:
            continue  # single-exit node; no re-distribution possible
        scale = (1.0 - new_prob) / other_total
        prefix_tree[key] = [
            [a, round(new_prob if a == to_act else p * scale, 4)]
            for a, p in transitions
        ]


def _mod_factor(mod: dict) -> float:
    if mod["change_type"] == "reduce_pct":
        return max(0.0, 1 - mod["value"] / 100)
    if mod["change_type"] == "increase_pct":
        return 1 + mod["value"] / 100
    return 1.0


# ── Recommendations ───────────────────────────────────────────────────────────

def _round10(v: float, lo: float = 10.0, hi: float = 100.0) -> float:
    """Round v to the nearest 10, clamped to [lo, hi]. Zero is preserved as-is."""
    if v == 0.0:
        return 0.0
    return float(max(lo, min(hi, round(v / 10.0) * 10.0)))

def _stable_key(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:10]


def _severity_color(ratio: float) -> str:
    if ratio >= 3.0:
        return "red"
    if ratio >= 1.5:
        return "orange"
    if ratio <= 0.33:
        return "green"    # very strong improvement
    if ratio <= 0.67:
        return "green"
    return "blue"


def _observation_direction(ratio: float) -> str:
    """'negative' = condition worsens things (ratio > 1), 'positive' = condition improves."""
    return "negative" if ratio >= 1.0 else "positive"


def _rule_badge(rule_type: str, ratio: float = 1.0) -> str:
    negative_badges = {
        "rework_in_case":   "REWORK PENALTY",
        "resource_speed_gap": "RESOURCE GAP",
        "preceded_by":      "SLOW PATH",
        "resource":         "HIGH REWORK",
    }
    positive_badges = {
        "rework_in_case":   "LOW REWORK CASE",
        "resource_speed_gap": "SPEED ADVANTAGE",
        "preceded_by":      "FAST PATH",
        "resource":         "LOW REWORK",
    }
    other_badges = {
        "gate":             "GATE",
        "strong_implication": "IMPLIES",
        "exclusion":        "EXCLUDES",
    }
    direction = _observation_direction(ratio)
    badges = positive_badges if direction == "positive" else negative_badges
    return badges.get(rule_type) or other_badges.get(rule_type) or rule_type.upper()


def _collect_recommendations(observations: Dict[str, Any]) -> list:
    items = []
    ctr  = observations.get("conditional_time_rules")
    crr  = observations.get("conditional_rework_rules")
    esr  = observations.get("eventual_sequence_rules")
    rrr  = observations.get("resource_routing_rules")
    dps  = observations.get("decision_points_df")         # decision point branches
    crur = observations.get("conditional_routing_rules")  # conditional routing patterns
    ra = observations.get("resource_assignments_df")
    rw_df = observations.get("rework_parameters_df")

    def _rework_rate_suggestion(act: str, reduce_pct: float = 50.0):
        if rw_df is None or rw_df.empty:
            return None
        row = rw_df[rw_df["activity"] == act]
        if row.empty:
            return None
        col = "rework_rate_given_visit_pct" if "rework_rate_given_visit_pct" in rw_df.columns else "rework_rate_pct"
        cur = round(float(row[col].iloc[0]), 1)
        new = round(cur * (1 - reduce_pct / 100), 1)
        return {
            "type": "rework_rate", "activity": act,
            "change_type": "reduce_pct", "value": reduce_pct,
            "description": f"Reduce rework rate for '{act}' by {reduce_pct:.0f}% ({cur:.0f}% → {new:.0f}%)",
            "baseline_value": cur, "modified_value": new,
        }

    # Conditional time rules — only negative patterns (condition is slower than baseline)
    if ctr is not None and not ctr.empty:
        for _, row in ctr.iterrows():
            cond = row["condition_type"]
            act = row["activity"]
            # Skip positive outliers — condition is faster than baseline, nothing to fix
            if float(row["ratio"]) < 1.0:
                continue
            if cond == "rework_in_case":
                cond_m = row["conditional_mean_min"]
                base_m = row["baseline_mean_min"]
                saving = round((cond_m - base_m) / max(cond_m, 1) * 100)
                red_pct = _round10(saving * 0.5, lo=10.0, hi=50.0)
                rec = (f"Model a global {red_pct:.0f}% speedup at '{act}'. "
                       f"Rework cases average {cond_m:.0f} vs {base_m:.0f} working minutes. "
                       f"This scenario accelerates every execution, including clean cases.")
                sug = {"type": "time", "activity": act, "change_type": "reduce_pct", "value": float(red_pct),
                       "description": f"Reduce '{act}' by {red_pct:.0f}% (global activity speedup)",
                       "baseline_value": 0.0, "modified_value": 0.0}
            elif cond == "resource_speed_gap":
                parts = row["condition_value"].split(" vs ")
                slowest = parts[0] if len(parts) == 2 else "slowest"
                fastest = parts[1] if len(parts) == 2 else "fastest"
                red_pct = _round10((row["ratio"] - 1) / row["ratio"] * 50, lo=10.0, hi=40.0) if row["ratio"] > 0 else 10.0
                rec = (f"Model shifting work away from {slowest} to the other resources "
                       f"({row['baseline_mean_min']:.0f} working minutes on average). "
                       f"Reduce {slowest}'s share by 30% and simulate the effect on cycle time.")
                sug = {"type": "resource_share", "activity": act, "resource": slowest,
                       "change_type": "reduce_pct", "value": 30.0,
                       "description": f"Reduce '{slowest}' share for '{act}' by 30%",
                       "baseline_value": 0.0, "modified_value": 0.0}
            elif cond == "preceded_by":
                pred = row["condition_value"]
                red_pct = _round10((row["ratio"] - 1) / row["ratio"] * 50, lo=10.0, hi=40.0)
                rec = (f"Model a global {red_pct:.0f}% speedup at '{act}', prompted by the slower "
                       f"executions after '{pred}'. This scenario also speeds up other predecessor paths.")
                sug = {"type": "time", "activity": act, "change_type": "reduce_pct", "value": float(red_pct),
                       "description": f"Reduce '{act}' by {red_pct:.0f}% (global activity speedup)",
                       "baseline_value": 0.0, "modified_value": 0.0}
            else:
                rec = "Reduce this activity's duration to eliminate the observed performance deviation."
                sug = {"type": "time", "activity": act, "change_type": "reduce_pct", "value": 10.0,
                       "description": f"Reduce '{act}' by 10%", "baseline_value": 0.0, "modified_value": 0.0}

            items.append({
                "observation_type": "conditional_time", "rule_text": row["rule_text"],
                "impact": max(float(row["ratio"]), 1.0 / max(float(row["ratio"]), 0.001)), "badge": _rule_badge(cond, float(row["ratio"])), "direction": _observation_direction(float(row["ratio"])),
                "severity": _severity_color(row["ratio"]), "recommendation": rec,
                "suggestion": sug if sug else None,
                "requires_input": False, "stable_key": _stable_key(row["rule_text"]),
                "kpi_tags": ["cycle_time", "bottleneck"],
            })

    # Conditional rework rules — only negative patterns (condition causes more rework than baseline)
    if crr is not None and not crr.empty:
        for _, row in crr.iterrows():
            cond = row["condition_type"]
            act = row["activity"]
            cur_rate = row["conditional_rework_rate_pct"]
            # Skip positive outliers — this condition reduces rework, nothing to fix
            if float(row["ratio"]) < 1.0:
                continue
            sug = None  # default: no modification suggested; set per condition below

            red_pct = _round10((float(row["ratio"]) - 1) / float(row["ratio"]) * 50,
                               lo=10.0, hi=50.0)
            sug = _rework_rate_suggestion(act, red_pct)
            rec = (f"Model a global {red_pct:.0f}% rework reduction at '{act}', prompted by "
                   f"the elevated rate for {row['condition_value']}. "
                   f"This quality-improvement scenario applies to all cases visiting the activity.")

            items.append({
                "observation_type": "conditional_rework", "rule_text": row["rule_text"],
                "impact": max(float(row["ratio"]), 1.0 / max(float(row["ratio"]), 0.001)), "badge": _rule_badge(cond, float(row["ratio"])), "direction": _observation_direction(float(row["ratio"])),
                "severity": _severity_color(row["ratio"]), "recommendation": rec,
                "suggestion": sug, "requires_input": False, "intervention_scope": "global_activity",
                "stable_key": _stable_key(row["rule_text"]),
                "kpi_tags": ["rework_rate", "cycle_time"],
            })

    # Eventual sequence rules
    if esr is not None and not esr.empty:
        for _, row in esr.iterrows():
            p_event = float(row["p_eventually"])
            p_direct = float(row.get("p_directly", 0.0))
            act_a, act_b = row["activity_a"], row["activity_b"]
            cur_direct_pct = round(p_direct * 100, 1)
            default_pct = _round10(p_direct * 100, lo=10.0, hi=100.0)
            if row["rule_type"] == "gate":
                rec = (f"Enforce direct routing: '{act_a}'→'{act_b}' occurs in {p_event*100:.0f}% of cases. "
                       f"Adjust the direct rate ({cur_direct_pct:.0f}%) to control how often cases take this path.")
            elif row["rule_type"] == "strong_implication":
                rec = (f"Ensure '{act_b}' follows '{act_a}': currently occurs in {p_event*100:.0f}% of cases. "
                       f"Adjust the direct rate ({cur_direct_pct:.0f}%) to enforce this dependency more consistently.")
            else:
                rec = (f"Prevent cases from bypassing '{act_b}' after '{act_a}' (currently only {p_event*100:.0f}%). "
                       f"Increase the transition probability to route more cases through '{act_b}'.")

            direct_exists = any(
                key.split("|||")[-1] == act_a and any(dest == act_b for dest, _ in transitions)
                for key, transitions in (observations.get("prefix_tree") or {}).items()
            )
            if not direct_exists:
                items.append({
                    "observation_type": "sequence", "rule_text": row["rule_text"],
                    "impact": p_event * 1.5, "badge": _rule_badge(row["rule_type"]),
                    "severity": "blue", "recommendation":
                        f"Review the eventual dependency '{act_a}' → '{act_b}'. "
                        "An explicit process redesign is needed to enforce it; there is no direct route to adjust.",
                    "suggestion": None, "requires_input": False,
                    "unsupported_reason": "eventual_relation_has_no_direct_transition",
                    "stable_key": _stable_key(row["rule_text"]), "kpi_tags": ["conformance"],
                })
                continue
            items.append({
                "observation_type": "sequence", "rule_text": row["rule_text"],
                "impact": p_event * 1.5, "badge": _rule_badge(row["rule_type"]),
                "severity": _severity_color(p_event * 1.5), "recommendation": rec,
                "suggestion": {
                    "type": "sequence", "from_activity": act_a, "to_activity": act_b,
                    "change_type": "set_value", "value": default_pct,
                    "description": f"'{act_a}' → '{act_b}' set to {{value}}% (current: {cur_direct_pct:.1f}%)",
                    "baseline_value": cur_direct_pct, "modified_value": default_pct,
                },
                "requires_input": True,
                "input_config": {"label": "Set direct transition %", "min": 0.0, "max": 100.0,
                                 "default": default_pct, "step": 10.0},
                "stable_key": _stable_key(row["rule_text"]),
                "kpi_tags": ["cycle_time", "wait_time"],
            })

    # Resource routing rules
    if rrr is not None and not rrr.empty:
        for _, row in rrr.iterrows():
            act = row["activity"]
            resource = row["resource"]
            cond_pct = float(row["conditional_pct"])
            new_share = float(row["baseline_pct"])
            current_share = 0.0
            if ra is not None and not ra.empty:
                current = ra[ra["activity"].eq(act) & ra["resource"].eq(resource)]
                if not current.empty:
                    current_share = float(current.iloc[0]["probability"]) * 100
            rec = (f"Model a global '{resource}' share of {new_share:.1f}% at '{act}', "
                   f"the share observed after other predecessors. The observed concentration "
                   f"after '{row['predecessor']}' is {cond_pct:.1f}%; the scenario changes all predecessor paths.")

            items.append({
                "observation_type": "resource_routing", "rule_text": row["rule_text"],
                "impact": max(float(row["ratio"]), 1.0 / max(float(row["ratio"]), 0.001)), "badge": "RESOURCE ROUTING",
                "severity": _severity_color(row["ratio"]), "recommendation": rec,
                "suggestion": {
                    "type": "resource_share", "activity": act, "resource": resource,
                    "change_type": "set_value", "value": new_share,
                    "description": f"Redistribute '{resource}' for '{act}' → {new_share:.0f}%",
                    "baseline_value": current_share, "modified_value": new_share,
                },
                "requires_input": False, "stable_key": _stable_key(row["rule_text"]),
                "kpi_tags": ["resource_utilization", "wait_time"],
            })

    # Conditional routing rules — only when a condition biases routing toward a *slower* branch
    crur = observations.get("conditional_routing_rules")
    if crur is not None and not crur.empty:
        ta = observations.get("activity_durations_df")
        ta_map = {}
        if ta is not None and not ta.empty:
            ta_map = {str(r["activity"]): float(r["mean_minutes"]) for _, r in ta.iterrows()}

        if ra is not None and not ra.empty:
            for activity, group in ra.groupby("activity"):
                total = float(group["probability"].sum())
                if total > 0:
                    ta_map[str(activity)] = float((group["probability"] * group["mean_duration_min"]).sum() / total)
        remaining_cost = dict(ta_map)
        transitions = observations.get("routing_probabilities_df")
        if transitions is not None:
            # Finite 20-step expected service cost also handles loops safely.
            for _ in range(20):
                updated = dict(ta_map)
                for source, group in transitions.groupby("from_activity"):
                    continuation = 1.0 - float((observations.get("terminal_probs") or {}).get(source, 0))
                    updated[str(source)] = ta_map.get(str(source), 0) + continuation * sum(
                        float(r.probability) * remaining_cost.get(str(r.to_activity), 0)
                        for r in group.itertuples())
                remaining_cost = updated

        for _, row in crur.iterrows():
            gw        = str(row["gateway_activity"])
            branch    = str(row["branch_activity"])
            cond_type = str(row["condition_type"])
            cond_val  = str(row["condition_value"])
            ratio     = float(row["ratio"])

            # Only surface when the condition pushes MORE cases into this branch (ratio > 1)
            # AND that branch is slower than the gateway's average — i.e. a genuinely bad bias
            if ratio <= 1.0:
                continue
            branch_dur = remaining_cost.get(branch, 0)
            outgoing = transitions[transitions["from_activity"].eq(gw)] if transitions is not None else pd.DataFrame()
            gw_avg_dur = sum(float(r.probability) * remaining_cost.get(str(r.to_activity), 0)
                             for r in outgoing.itertuples())
            if branch_dur <= gw_avg_dur:
                continue  # biased branch is not slower — no improvement available
            base_pct  = float(row["baseline_pct"])
            cond_pct  = float(row["conditional_pct"])
            count     = int(row["count"])
            sev       = str(row.get("severity", "orange"))
            impact    = max(1.3, abs(ratio - 1.0) + 1.0)

            rec = (f"Model a global routing change at '{gw}': set the '{branch}' branch "
                   f"to {base_pct:.1f}%, the rate observed for other cases. "
                   f"This scenario changes every case at the gateway, not only {cond_val}.")
            current_branch = outgoing[outgoing["to_activity"].eq(branch)]
            current_pct = float(current_branch.iloc[0]["probability"]) * 100 if not current_branch.empty else 0.0
            sug = {"type": "sequence", "from_activity": gw, "to_activity": branch,
                   "change_type": "set_value", "value": base_pct,
                   "description": f"Global '{gw}' → '{branch}' probability {base_pct:.1f}%",
                   "baseline_value": current_pct, "modified_value": base_pct}

            items.append({
                "observation_type": "conditional_routing",
                "rule_text": row["rule_text"],
                "impact": round(impact, 2),
                "badge": "ROUTING BIAS",
                "severity": sev,
                "recommendation": rec,
                "suggestion": sug,
                "requires_input": False,
                "stable_key": _stable_key(row["rule_text"]),
                "kpi_tags": ["cycle_time", "wait_time"],
            })

    # Decision point recommendations — surface balanced gateways as explicit routing levers
    if dps is not None and not dps.empty:
        ta_map = {}
        ta = observations.get("activity_durations_df")
        if ta is not None and not ta.empty:
            ta_map = {str(r["activity"]): float(r["mean_minutes"]) for _, r in ta.iterrows()}

        for gw, grp in dps.groupby("gateway_activity"):
            gw = str(gw)
            branches = grp.sort_values("probability", ascending=False)
            if len(branches) < 2:
                continue
            dominant = branches.iloc[0]
            dom_pct  = float(dominant["probability_pct"])
            dom_act  = str(dominant["branch_activity"])
            # Only recommend when dominant branch takes ≥60% — a real decision worth surfacing
            if dom_pct < 60:
                continue
            # Estimate cycle-time difference between dominant and minority branch
            dom_dur = ta_map.get(dom_act, 0)
            minor_durs = [ta_map.get(str(b["branch_activity"]), 0) for _, b in branches.iloc[1:].iterrows()]
            avg_minor = sum(minor_durs) / max(len(minor_durs), 1)
            if dom_dur <= avg_minor:
                continue  # dominant path is already faster — no gain from shifting
            saving_per_case = (dom_pct / 100) * (dom_dur - avg_minor)
            avg_ct = observations.get("log_metrics", {}).get("avg_cycle_time_hours", 0) * 60 or 1
            impact = max(1.2, 1.0 + saving_per_case / avg_ct)
            rule_text = (
                f"At '{gw}', {dom_pct:.0f}% of cases route to '{dom_act}' "
                f"(avg {dom_dur:.0f} min) vs minority branches (avg {avg_minor:.0f} min)."
            )
            rec = (
                f"Enforce faster routing at '{gw}': shift cases away from '{dom_act}' "
                f"toward shorter alternatives — eliminates ~{saving_per_case:.0f} min/case. "
                f"Use the Decision Point modification to redistribute branch probabilities."
            )
            items.append({
                "observation_type": "decision_point_routing",
                "rule_text": rule_text,
                "impact": round(impact, 2),
                "badge": "ROUTING SHIFT",
                "severity": "orange" if dom_pct < 80 else "red",
                "recommendation": rec,
                "suggestion": {
                    "type": "decision_point",
                    "activity": gw,
                    "change_type": "set_value",
                    "value": 0,
                    "branches": [{"to_activity": str(b["branch_activity"]),
                                  "probability_pct": round(float(b["probability_pct"]) * 0.7 if b["branch_activity"] == dom_act else float(b["probability_pct"]) * 1.3, 1)}
                                 for _, b in branches.iterrows()],
                    "description": f"Shift '{gw}' routing away from '{dom_act}' by 30%",
                    "baseline_value": dom_pct, "modified_value": round(dom_pct * 0.7, 1),
                },
                "requires_input": False,
                "stable_key": _stable_key(f"dp_routing_{gw}_{dom_act}"),
                "kpi_tags": ["cycle_time"],
            })

    for item in items:
        suggestion = item.get("suggestion") or {}
        item["intervention_scope"] = {
            "time": "global_activity", "rework_rate": "global_activity",
            "resource_share": "global_activity_resource_share", "sequence": "global_transition",
            "decision_point": "global_gateway",
        }.get(suggestion.get("type"), "unsupported")

    return sorted(items, key=lambda x: -x["impact"])


def _collect_simulation_impact_recommendations(observations: Dict[str, Any]) -> list:
    """
    Recommendations grounded directly in how the simulation engine responds to modifications.
    Unlike conditional-rule recommendations (which surface statistical patterns), these target
    the levers with the highest expected KPI impact: capacity, dominant duration drivers,
    expensive rework, routing shortcuts, and demand-relief near saturation.
    """
    items = []

    ta        = observations.get("activity_durations_df")
    ra        = observations.get("resource_assignments_df")
    rw_df     = observations.get("rework_parameters_df")
    sa        = observations.get("routing_probabilities_df")
    cap_dict  = observations.get("resource_capacity_dict") or {}
    peak_dict = observations.get("peak_concurrent_dict") or {}
    arrival   = observations.get("arrival_parameters") or {}
    log_metrics = observations.get("log_metrics") or {}

    cpd         = float(arrival.get("cases_per_day", 1.0))
    avg_ct_min  = float(log_metrics.get("avg_cycle_time_hours", 0.0)) * 60.0
    total_cases = int(log_metrics.get("cases_count") or 1) or 1

    # ── 1. Capacity bottleneck at most saturated resource ────────────────────
    # Find the resource whose peak concurrent ≥ its capacity (was fully saturated
    # in the log).  Adding slots there directly eliminates queues; unlike resource-share
    # changes, this reduces total queue depth rather than just reshuffling who waits.
    if ra is not None and not ra.empty and cap_dict:
        # Exclude the synthetic "default" resource — it's not a real bottleneck
        real_cap = {k: v for k, v in cap_dict.items() if k != "default"}
        if real_cap:
            saturated = {
                res: peak_dict.get(res, 0) / max(real_cap[res], 1)
                for res in real_cap
                if peak_dict.get(res, 0) >= real_cap[res]
            }
            if not saturated:   # fallback: highest peak/capacity ratio
                saturated = {res: peak_dict.get(res, 0) / max(real_cap[res], 1) for res in real_cap}
            if saturated:
                top_res = max(saturated, key=saturated.__getitem__)
                cur_cap = real_cap[top_res]
                peak    = peak_dict.get(top_res, cur_cap)
                new_cap = max(cur_cap + 1, round(cur_cap * 1.5))
                res_rows = ra[ra["resource"] == top_res].sort_values(
                    "count" if "count" in ra.columns else "probability", ascending=False
                )
                top_act = str(res_rows.iloc[0]["activity"]) if not res_rows.empty else top_res
                rho = min(saturated[top_res], 0.98)
                rho_new = rho * cur_cap / new_cap
                wait_ratio = (rho / (1 - rho)) / max(rho_new / (1 - rho_new), 0.02)
                impact_score = min(4.0, max(1.6, wait_ratio))
                items.append({
                    "observation_type": "capacity_bottleneck",
                    "rule_text": (
                        f"'{top_res}' hit full capacity in the log ({peak} concurrent peak vs "
                        f"{cur_cap} slot{'s' if cur_cap > 1 else ''}) — cases queue whenever all "
                        f"slots are occupied."
                    ),
                    "impact": impact_score,
                    "badge": "CAPACITY BOTTLENECK",
                    "severity": "red" if saturated[top_res] >= 1.0 else "orange",
                    "recommendation": (
                        f"Eliminate queue buildup: add {new_cap - cur_cap} slot{'s' if new_cap - cur_cap > 1 else ''} to "
                        f"'{top_res}' (→ {new_cap} total). Every case routed to '{top_act}' will "
                        f"find a free slot instead of waiting — capacity expansion eliminates total queue depth "
                        f"rather than just reshuffling who waits."
                    ),
                    "suggestion": {
                        "type": "resource_capacity", "resource": top_res,
                        "change_type": "set_value", "value": float(new_cap),
                        "description": f"'{top_res}' capacity {cur_cap} → {new_cap} slots",
                        "baseline_value": float(cur_cap), "modified_value": float(new_cap),
                    },
                    "requires_input": False,
                    "stable_key": _stable_key(f"cap_bottleneck_{top_res}"),
                    "kpi_tags": ["wait_time", "resource_utilization", "cycle_time"],
                })

    # ── 2. Top cycle-time contributor (mean_min × visit_frequency) ───────────
    # The activity whose expected contribution per case is largest is the single
    # best target for duration reduction — every percent cut here moves cycle time.
    if ta is not None and not ta.empty and avg_ct_min > 0:
        ta2 = ta.copy()
        ta2["contribution_min"] = ta2["mean_minutes"] * ta2["count"] / total_cases
        top = ta2.sort_values("contribution_min", ascending=False).iloc[0]
        act         = str(top["activity"])
        contrib     = float(top["contribution_min"])
        mean_min    = float(top["mean_minutes"])
        contrib_pct = contrib / avg_ct_min * 100
        reduce_pct  = _round10(contrib_pct * 0.4, lo=10.0, hi=40.0)
        new_mean    = round(mean_min * (1 - reduce_pct / 100), 1)
        savings_pct = contrib_pct * reduce_pct / 100
        impact_score = max(1.4, 1.0 + savings_pct / 50)
        items.append({
            "observation_type": "top_duration_driver",
            "rule_text": (
                f"'{act}' is the largest single contributor to cycle time: "
                f"~{contrib:.0f} min/case ({contrib_pct:.0f}% of average cycle time), "
                f"driven by its {mean_min:.0f}-min mean and {int(top['count']):,} occurrences."
            ),
            "impact": impact_score,
            "badge": "TOP TIME DRIVER",
            "severity": "red" if contrib_pct > 30 else "orange",
            "recommendation": (
                f"Eliminate the dominant cycle time driver: a {reduce_pct:.0f}% reduction in '{act}' "
                f"directly saves ~{savings_pct:.1f}% of total average cycle time "
                f"({mean_min:.0f} → {new_mean:.0f} min). "
                f"Model automation, parallel tooling, or simplified steps — "
                f"no other single activity has more direct leverage on cycle time."
            ),
            "suggestion": {
                "type": "time", "activity": act,
                "change_type": "reduce_pct", "value": reduce_pct,
                "description": f"'{act}' −{reduce_pct:.0f}% duration (top cycle time driver)",
                "baseline_value": mean_min, "modified_value": new_mean,
            },
            "requires_input": False,
            "stable_key": _stable_key(f"top_duration_driver_{act}"),
            "kpi_tags": ["cycle_time", "bottleneck"],
        })

    # ── 3. Most expensive rework (rate × loops × duration) ───────────────────
    # Rework has a compound cost: each extra loop re-executes the activity AND
    # re-books the resource slot, potentially creating a second queue wait.
    if rw_df is not None and not rw_df.empty and ta is not None and not ta.empty and avg_ct_min > 0:
        rw_col = "rework_rate_given_visit_pct" if "rework_rate_given_visit_pct" in rw_df.columns else "rework_rate_pct"
        ta_map = {str(r["activity"]): float(r["mean_minutes"]) for _, r in ta.iterrows()}
        rw2 = rw_df.copy()
        rw2["mean_minutes"] = rw2["activity"].map(lambda a: ta_map.get(str(a), 0.0))
        rw2["cycle_cost"]   = rw2[rw_col] / 100.0 * rw2["mean_extra_loops"] * rw2["mean_minutes"]
        top_rw   = rw2.sort_values("cycle_cost", ascending=False).iloc[0]
        act      = str(top_rw["activity"])
        cost_min = float(top_rw["cycle_cost"])
        rate     = float(top_rw[rw_col])
        loops    = float(top_rw["mean_extra_loops"])
        mean_min = float(top_rw["mean_minutes"])
        cost_pct = cost_min / avg_ct_min * 100
        if cost_pct > 1.0:
            savings_pct  = cost_pct * 0.5
            impact_score = max(1.5, 1.0 + savings_pct / 30)
            items.append({
                "observation_type": "expensive_rework",
                "rule_text": (
                    f"Rework at '{act}' costs ~{cost_min:.0f} extra min/case on average "
                    f"({rate:.1f}% rate × {loops:.1f} avg loops × {mean_min:.0f} min/execution)."
                ),
                "impact": impact_score,
                "badge": "REWORK COST",
                "severity": "red" if cost_pct > 15 else "orange",
                "recommendation": (
                    f"Prevent rework compounding at '{act}': halving the rework rate saves ~{cost_min * 0.5:.0f} min/case. "
                    f"Rework reduction has a compound effect — fewer re-executions, fewer resource re-bookings, "
                    f"and reduced downstream queue buildup — especially visible with resource contention enabled."
                ),
                "suggestion": {
                    "type": "rework_rate", "activity": act,
                    "change_type": "reduce_pct", "value": 50.0,
                    "description": f"'{act}' rework −50% ({rate:.1f}% → {rate * 0.5:.1f}%)",
                    "baseline_value": round(rate, 1), "modified_value": round(rate * 0.5, 1),
                },
                "requires_input": False,
                "stable_key": _stable_key(f"expensive_rework_{act}"),
                "kpi_tags": ["rework_rate", "cycle_time", "wait_time"],
            })
    # ── 4. Routing fast-track: shift cases away from the longest branch ──────
    # Find the split point where the longest-duration successor could be partially
    # bypassed by routing more cases to a shorter alternative.  Reduces A→B_long
    # probability; engine renormalises the rest, routing more cases to B_short.
    if sa is not None and not sa.empty and ta is not None and not ta.empty and avg_ct_min > 0:
        ta_map = {str(r["activity"]): float(r["mean_minutes"]) for _, r in ta.iterrows()}
        best: Optional[dict] = None
        best_saving = 0.0
        for from_act, grp in sa.groupby("from_activity"):
            if len(grp) < 2:
                continue
            txns = sorted(
                [(str(r["to_activity"]), float(r["probability"])) for _, r in grp.iterrows()],
                key=lambda x: ta_map.get(x[0], 0.0), reverse=True,
            )
            long_act,  long_p  = txns[0]
            short_act, short_p = txns[-1]
            long_dur  = ta_map.get(long_act, 0.0)
            short_dur = ta_map.get(short_act, 0.0)
            if long_dur > 0 and short_dur < long_dur * 0.6 and long_p > 0.15:
                saving = 0.15 * (long_dur - short_dur)
                if saving > best_saving:
                    best_saving = saving
                    best = dict(
                        from_act=str(from_act), long_act=long_act, short_act=short_act,
                        long_p=long_p, long_dur=long_dur, short_dur=short_dur,
                    )
        if best:
            saving_pct   = best_saving / avg_ct_min * 100
            impact_score = max(1.2, 1.0 + saving_pct / 30)
            new_long_pct = _round10((best["long_p"] - 0.15) * 100, lo=10.0, hi=100.0)
            items.append({
                "observation_type": "routing_fast_track",
                "rule_text": (
                    f"After '{best['from_act']}', {best['long_p']*100:.0f}% of cases go to "
                    f"'{best['long_act']}' ({best['long_dur']:.0f} min). A shorter alternative "
                    f"'{best['short_act']}' ({best['short_dur']:.0f} min) is under-used."
                ),
                "impact": impact_score,
                "badge": "FAST TRACK",
                "severity": "orange",
                "recommendation": (
                    f"Enforce faster routing: reduce '{best['from_act']}' → '{best['long_act']}' from "
                    f"{best['long_p']*100:.0f}% to {new_long_pct:.0f}% — the engine "
                    f"auto-renormalises, routing those cases to shorter paths. "
                    f"Each rerouted case eliminates {best['long_dur'] - best['short_dur']:.0f} min. "
                    f"Set the target % below to match your redesign scenario."
                ),
                "suggestion": {
                    "type": "sequence",
                    "from_activity": best["from_act"], "to_activity": best["long_act"],
                    "change_type": "set_value", "value": new_long_pct,
                    "description": (
                        f"'{best['from_act']}'→'{best['long_act']}' "
                        f"{best['long_p']*100:.0f}% → {new_long_pct:.0f}%"
                    ),
                    "baseline_value": round(best["long_p"] * 100, 1),
                    "modified_value": new_long_pct,
                },
                "requires_input": True,
                "input_config": {
                    "label": f"'{best['from_act']}'→'{best['long_act']}' %",
                    "min": 0.0, "max": _round10(best["long_p"] * 100, lo=10.0, hi=100.0),
                    "default": new_long_pct, "step": 10.0,
                },
                "stable_key": _stable_key(f"fast_track_{best['from_act']}_{best['long_act']}"),
                "kpi_tags": ["cycle_time"],
            })

    # ── 5. Near-saturation demand relief ─────────────────────────────────────
    # Kingman's approximation: wait ∝ ρ/(1-ρ).  Near saturation (ρ > 0.75),
    # even a small arrival rate cut produces a disproportionate wait reduction.
    if cpd > 0 and ra is not None and not ra.empty and cap_dict and avg_ct_min > 0:
        res_throughputs = []
        for res, cap in cap_dict.items():
            if res == "default":
                continue
            rows = ra[ra["resource"] == res]
            if rows.empty or "mean_duration_min" not in rows.columns:
                continue
            mean_dur = float(rows["mean_duration_min"].mean())
            if mean_dur > 0:
                res_throughputs.append(cap * 1440.0 / mean_dur)
        if res_throughputs:
            bottleneck_tp = min(res_throughputs)
            rho = cpd / bottleneck_tp
            if rho > 0.72:
                reduce_pct = 20.0 if rho > 0.88 else 15.0 if rho > 0.80 else 10.0
                new_rate   = round(cpd * (1 - reduce_pct / 100), 1)
                rho_new    = rho * (1 - reduce_pct / 100)
                wait_ratio = (rho / (1 - rho)) / max(rho_new / max(1 - rho_new, 0.02), 0.01)
                impact_score = min(4.5, max(1.6, 1.0 + (wait_ratio - 1) * 0.5))
                items.append({
                    "observation_type": "arrival_demand_relief",
                    "rule_text": (
                        f"System utilisation is ~{rho*100:.0f}% ({cpd:.1f} cases/day arriving, "
                        f"bottleneck capacity ≈ {bottleneck_tp:.1f}/day). "
                        f"Above 75% utilisation, waiting time grows non-linearly with demand."
                    ),
                    "impact": impact_score,
                    "badge": "DEMAND RELIEF",
                    "severity": "red" if rho > 0.88 else "orange",
                    "recommendation": (
                        f"Prevent saturation-driven wait explosion: reducing arrival rate by {reduce_pct:.0f}% "
                        f"({cpd:.1f} → {new_rate:.1f}/day) eliminates ~{(wait_ratio - 1) * 100:.0f}% of average wait time "
                        f"due to queuing non-linearity near saturation (Kingman's formula). "
                        f"Model appointment scheduling, intake throttling, or demand smoothing."
                    ),
                    "suggestion": {
                        "type": "arrival",
                        "change_type": "reduce_pct", "value": reduce_pct,
                        "description": f"Arrival rate −{reduce_pct:.0f}% ({cpd:.1f} → {new_rate:.1f}/day)",
                        "baseline_value": cpd, "modified_value": new_rate,
                    },
                    "requires_input": False,
                    "stable_key": _stable_key(f"demand_relief_{cpd:.2f}"),
                    "kpi_tags": ["wait_time", "cycle_time", "resource_utilization"],
                })

    # ── 6. Parallel contention: high co-parallel rate + shared resource ──────────
    pp_df = observations.get("parallel_pairs_df")
    if (pp_df is not None and not pp_df.empty
            and ra is not None and not ra.empty
            and avg_ct_min > 0):
        top_res_map: Dict[str, str] = {}
        for act, grp in ra.groupby("activity"):
            top = grp.sort_values("probability", ascending=False).iloc[0]
            top_res_map[str(act)] = str(top["resource"])

        best_par: Optional[dict] = None
        best_impact = 0.0
        for _, row in pp_df.iterrows():
            a_s = str(row["activity_a"])
            b_s = str(row["activity_b"])
            rate_ab = float(row.get("a_parallel_with_b_pct", 0)) / 100.0
            if rate_ab < 0.30:
                continue
            res_a = top_res_map.get(a_s)
            res_b = top_res_map.get(b_s)
            if res_a and res_b and res_a == res_b and res_a != "default":
                dur_a = float(ta_map.get(a_s, 0))
                dur_b = float(ta_map.get(b_s, 0))
                contention_cost = rate_ab * min(dur_a, dur_b)
                impact = max(1.2, 1.0 + contention_cost / max(avg_ct_min, 1.0))
                if impact > best_impact:
                    best_impact = impact
                    best_par = {"a": a_s, "b": b_s, "res": res_a,
                                "rate_pct": round(rate_ab * 100, 0)}

        if best_par:
            a, b, res = best_par["a"], best_par["b"], best_par["res"]
            rate_pct = best_par["rate_pct"]
            items.append({
                "observation_type": "parallel_contention",
                "rule_text": (
                    f"'{a}' and '{b}' run concurrently in {rate_pct:.0f}% of cases, "
                    f"both handled by '{res}'. Concurrent execution double-books the resource, "
                    f"amplifying queue depth when both are in-flight together."
                ),
                "impact": round(best_impact, 2),
                "badge": "PARALLEL CONTENTION",
                "severity": "orange",
                "recommendation": (
                    f"Eliminate resource double-booking: serialising '{a}' before '{b}' prevents '{res}' "
                    f"from being occupied by both activities simultaneously. "
                    f"Set the co-parallel rate to 0% to enforce fully sequential execution."
                ),
                "suggestion": {
                    "type": "parallel_rate",
                    "activity_a": a, "activity_b": b,
                    "change_type": "set_value", "value": 0.0,
                    "description": f"'{a}' ∥ '{b}' serialised ({rate_pct:.0f}% → 0%)",
                    "baseline_value": rate_pct, "modified_value": 0.0,
                },
                "requires_input": False,
                "stable_key": _stable_key(f"parallel_contention_{a}_{b}"),
                "kpi_tags": ["wait_time", "resource_utilization"],
            })

    return sorted(items, key=lambda x: -x["impact"])


def _collect_case_attribute_recommendations(ca_observations: list, observations: Dict[str, Any]) -> list:
    """Generate actionable simulation recommendations from case-attribute observations."""
    items = []
    if not ca_observations:
        return items

    time_df = observations.get("activity_durations_df")
    rw_df   = observations.get("rework_parameters_df")
    res_df  = observations.get("resource_assignments_df")
    cap_dict = observations.get("resource_capacity_dict") or {}

    # Pre-compute top activity by mean duration and top by rework rate
    top_dur_act, top_dur_mean = None, 0.0
    if time_df is not None and not time_df.empty:
        row = time_df.sort_values("mean_minutes", ascending=False).iloc[0]
        top_dur_act  = str(row["activity"])
        top_dur_mean = float(row["mean_minutes"])

    rw_col = None
    if rw_df is not None and not rw_df.empty:
        rw_col = "rework_rate_given_visit_pct" if "rework_rate_given_visit_pct" in rw_df.columns else "rework_rate_pct"

    for observation in ca_observations:
        sub      = observation["sub_type"]
        attr     = observation["attribute"]
        val      = observation["attribute_value"]
        ratio    = float(observation["ratio"])
        n        = int(observation["n_cases"])
        n_total  = int(observation["n_total"])
        impact   = float(observation["impact"])
        severity = observation["severity"]
        rule_text = observation["rule_text"]
        cond_val = float(observation["conditional_value"])
        base_val = float(observation["baseline_value"])
        activity = observation.get("activity")

        # ── Slow segment ────────────────────────────────────────────────────────
        if sub == "cycle_time_effect" and ratio > 1.1 and top_dur_act:
            pct = _round10((ratio - 1.0) / ratio * 100 * 0.5, lo=10.0, hi=40.0)
            new_mean = round(top_dur_mean * (1 - pct / 100), 1)
            rec = (
                f"Model a global {pct:.0f}% speedup of '{top_dur_act}', prompted by the slow "
                f"{attr}='{val}' segment ({cond_val:.1f}h vs {base_val:.1f}h for other cases). "
                f"The change applies to all cases, including other segments."
            )
            items.append({
                "observation_type": "case_attribute", "rule_text": rule_text,
                "impact": impact, "badge": "SLOW SEGMENT", "severity": severity,
                "recommendation": rec,
                "suggestion": {
                    "type": "time", "activity": top_dur_act,
                    "change_type": "reduce_pct", "value": pct,
                    "description": f"Global '{top_dur_act}' duration −{pct:.0f}%",
                    "baseline_value": top_dur_mean, "modified_value": new_mean,
                },
                "requires_input": False, "stable_key": _stable_key(rule_text),
                "kpi_tags": ["cycle_time", "bottleneck"],
            })

        # ── High-rework segment ─────────────────────────────────────────────────
        elif sub == "rework_effect" and ratio > 1.1 and rw_col and rw_df is not None and not rw_df.empty:
            candidates = rw_df[rw_df["activity"].eq(activity)] if activity else pd.DataFrame()
            if candidates.empty:
                continue
            rw_row = candidates.iloc[0]
            rw_act   = str(rw_row["activity"])
            rw_rate  = float(rw_row[rw_col])
            reduce_pct = _round10((ratio - 1.0) / ratio * 100 * 0.5, lo=10.0, hi=60.0)
            new_rate = round(rw_rate * (1 - reduce_pct / 100), 1)
            rec = (
                f"Model a global {reduce_pct:.0f}% rework reduction on '{rw_act}', prompted by "
                f"{attr}='{val}' ({cond_val:.0f}% vs {base_val:.0f}% for other cases). "
                f"This scenario changes all cases visiting that activity."
            )
            items.append({
                "observation_type": "case_attribute", "rule_text": rule_text,
                "impact": impact, "badge": "HIGH REWORK", "severity": severity,
                "recommendation": rec,
                "suggestion": {
                    "type": "rework_rate", "activity": rw_act,
                    "change_type": "reduce_pct", "value": reduce_pct,
                    "description": f"Global '{rw_act}' rework −{reduce_pct:.0f}%",
                    "baseline_value": rw_rate, "modified_value": round(rw_rate * (1 - reduce_pct / 100), 1),
                } if rw_rate > 0 else None,
                "requires_input": False, "stable_key": _stable_key(rule_text),
                "kpi_tags": ["rework_rate", "cycle_time"],
            })

        # ── High-demand activity ─────────────────────────────────────────────────
        elif sub == "activity_presence" and ratio >= 1.2 and activity:
            top_res, baseline_cap = None, 1
            if res_df is not None and not res_df.empty:
                act_res = res_df[res_df["activity"] == activity].sort_values("probability", ascending=False)
                if not act_res.empty:
                    top_res      = str(act_res.iloc[0]["resource"])
                    baseline_cap = int(cap_dict.get(top_res, 1))
            extra_load = (ratio - 1.0) * (n / max(n_total, 1))
            new_cap    = max(baseline_cap + 1, round(baseline_cap * (1 + extra_load)))
            if top_res:
                rec = (
                    f"Prevent queue buildup for {attr}='{val}' cases: this segment triggers '{activity}' {ratio:.1f}× more often "
                    f"({cond_val:.0f}% vs {base_val:.0f}% of cases, n={n:,}). "
                    f"Expanding '{top_res}' capacity from {baseline_cap} to {new_cap} slots "
                    f"models extra capacity available to every case using that resource."
                )
                sug = {
                    "type": "resource_capacity", "resource": top_res,
                    "change_type": "set_value", "value": float(new_cap),
                    "description": f"'{top_res}' capacity {baseline_cap}→{new_cap} (extra demand from {attr}='{val}')",
                    "baseline_value": float(baseline_cap), "modified_value": float(new_cap),
                }
            else:
                rec = (
                    f"Prevent bottleneck for {attr}='{val}' cases: this segment triggers '{activity}' {ratio:.1f}× more often "
                    f"({cond_val:.0f}% vs {base_val:.0f}% of cases, n={n:,}). "
                    f"Add processing capacity to prevent '{activity}' from becoming a bottleneck for this segment."
                )
                sug = None
            items.append({
                "observation_type": "case_attribute", "rule_text": rule_text,
                "impact": impact, "badge": "HIGH DEMAND", "severity": "orange",
                "recommendation": rec, "suggestion": sug,
                "requires_input": False, "stable_key": _stable_key(rule_text),
                "kpi_tags": ["wait_time", "resource_utilization", "cycle_time"],
            })

        # A time reduction cannot enforce activity occurrence. Keep this as
        # an observation requiring process redesign, without a fictitious fix.
        elif sub == "activity_absence" and activity:
            items.append({
                "observation_type": "case_attribute", "rule_text": rule_text,
                "impact": impact, "badge": "SKIP RISK", "severity": "orange",
                "recommendation": f"Review why {attr}='{val}' cases skip '{activity}'. "
                    "Restoring the step requires an explicit routing change.",
                "suggestion": None, "requires_input": False,
                "unsupported_reason": "activity_occurrence_intervention_required",
                "stable_key": _stable_key(rule_text), "kpi_tags": ["conformance"],
            })

    return sorted(items, key=lambda x: -x["impact"])


# ── JSON-compatible conversion (numpy types) ──────────────────────────────────────────────

def _average_results(results: list) -> dict:
    """Average scalar metrics and per-activity breakdowns across multiple runs."""
    import numpy as np

    if len(results) == 1:
        r = results[0]
        for c in r.get("cases", []):
            c.pop("activities", None)
            c.pop("activity_durations", None)
            c.pop("activity_waits", None)
        return r

    scalar_keys = [
        "avg_cycle_time_hours", "median_cycle_time_hours",
        "p75_cycle_time_hours", "p90_cycle_time_hours",
        "avg_waiting_time_hours", "median_waiting_time_hours",
        "p75_waiting_time_hours", "p90_waiting_time_hours",
        "waiting_pct_of_cycle", "avg_service_time_hours",
        "rework_rate_pct", "avg_rework_acts", "cases_completed",
    ]

    avg: dict = {}
    for k in scalar_keys:
        vals = [r[k] for r in results if k in r]
        avg[k] = float(np.mean(vals)) if vals else 0.0

    avg["rework_cases"] = int(round(float(np.mean([r.get("rework_cases", 0) for r in results]))))

    # Per-activity dicts: average values across runs
    for dict_key in ("activity_time_breakdown", "activity_wait_breakdown"):
        all_acts: set = set()
        for r in results:
            all_acts |= set(r.get(dict_key, {}).keys())
        avg[dict_key] = {
            act: round(float(np.mean([r.get(dict_key, {}).get(act, 0.0) for r in results])), 2)
            for act in all_acts
        }

    # Bottlenecks: average per activity across runs
    all_bneck_acts: set = set()
    for r in results:
        all_bneck_acts |= {b["activity"] for b in r.get("bottlenecks", [])}

    bottlenecks = []
    for act in all_bneck_acts:
        rows = [b for r in results for b in r.get("bottlenecks", []) if b["activity"] == act]
        bottlenecks.append({
            "activity": act,
            "mean_duration_min":      round(float(np.mean([b["mean_duration_min"] for b in rows])), 1),
            "mean_wait_min":          round(float(np.mean([b["mean_wait_min"] for b in rows])), 1),
            "total_wait_share_pct":   round(float(np.mean([b["total_wait_share_pct"] for b in rows])), 1),
            "total_time_share_pct":   round(float(np.mean([b["total_time_share_pct"] for b in rows])), 1),
        })
    avg["bottlenecks"] = sorted(bottlenecks, key=lambda x: -x["total_wait_share_pct"])[:10]

    # Resource utilization: average pct across runs per resource
    all_res: set = set()
    for r in results:
        all_res |= set(r.get("resource_utilization", {}).keys())
    avg["resource_utilization"] = {
        res: round(float(np.mean([r.get("resource_utilization", {}).get(res, 0.0) for r in results])), 1)
        for res in all_res
    }

    # Combine cases from all runs for the case-level list (trimmed)
    avg["cases"] = []
    for r in results:
        for c in r.get("cases", []):
            c.pop("activities", None)
            c.pop("activity_durations", None)
            c.pop("activity_waits", None)
        avg["cases"].extend(r.get("cases", []))

    avg["num_runs"] = len(results)
    return avg


def _to_json_compatible(obj):
    import numpy as np
    if isinstance(obj, dict):
        return {k: _to_json_compatible(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_compatible(i) for i in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj
