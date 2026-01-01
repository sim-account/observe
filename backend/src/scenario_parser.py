"""
Optional natural-language scenario parser.
Called by the parse-scenario endpoint; falls back to regex parser if unavailable.
"""
import os
import re
import json
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger("scenario_parser")

_SYSTEM = """You are a business process simulation assistant.
Parse a user's natural language scenario into a structured JSON object.

## Engine capabilities (enforced during simulation)
- max_visits: cap how many times an activity occurs per case
- time: increase or reduce an activity's mean duration by a percentage
- resource_capacity: increase or reduce a resource's capacity by % or add N slots
- rework_rate: increase or reduce the rework rate at an activity by %
- arrival: increase or reduce the case arrival rate by %

## Post-run checks only (cannot enforce during routing)
- time_span: from activity A to B must not exceed / must be at least N days/hours
- activity_presence: an activity must always occur in each case
- activity_absence: an activity must never occur in each case
- resource_utilisation: a resource's utilisation must not exceed X%
- rework_rate_constraint: the overall rework rate must not exceed X%

## Output schema (JSON only, no markdown wrapper)
{
  "intent": "<max_visits|time|resource_capacity|rework_rate|arrival|time_span|activity_presence|activity_absence|resource_utilisation|rework_rate_constraint|unknown>",
  "activity": "<closest matching activity name from the provided list, or null>",
  "activity_a": "<for time_span: the FROM activity, or null>",
  "activity_b": "<for time_span: the TO activity, or null>",
  "resource": "<closest matching resource name from the provided list, or null>",
  "number": <numeric value, or null>,
  "unit": "<days|hours|minutes|pct|slots|count, or null>",
  "direction": "<increase|reduce, or null>",
  "operator": "<lte|gte, or null — for constraint intents only>",
  "segment_value": "<case attribute value mentioned in the text, or null>",
  "ambiguous_activities": ["<other close activity name matches>"],
  "notes": ["<any approximation or ambiguity notes>"]
}

Rules:
- Match activity and resource names semantically using the provided lists.
  Examples: 'reviews' -> 'Glow: Review'; 'approvals' -> 'Glow: Underwriting - Credit Approval'; 'preparation' -> 'Glow: Prepare'.
- Users may phrase observations using action verbs like 'ensure', 'enforce', 'prevent', 'eliminate', 'guarantee', 'avoid' — these are valid and should be mapped to the appropriate intent. E.g. 'ensure no more than 2 reviews' -> max_visits; 'eliminate 30% of preparation time' -> time (reduce); 'enforce Submit to Close under 30 days' -> time_span; 'prevent Review from occurring' -> activity_absence.
- If multiple activities match similarly, put the best one in 'activity' and others in 'ambiguous_activities'.
- Natural language numbers: 'cut in half' = 50% reduction; 'double' = 100% increase; 'a month' = 30 days; 'a week' = 7 days; 'an hour' = 60 min; '10 percent' = 10 (unit: pct).
- Segment-scoped observations like 'for region 1 cases' or 'in region 1' are valid — extract the segment into segment_value and still map to the appropriate intent. If no specific resource is named for a capacity observation, leave resource null; direction should be 'increase'.
- For resource_utilisation: if the observation mentions a group ('resources in region 1', 'all resources') rather than a specific resource name, set resource to null. The constraint will apply to all resources globally.
- Relative activity references in time_span observations: 'first review activity' → match to the first/primary review-related activity in the list; 'final activity of the process' or 'end of the process' or 'last activity' → match to the last/terminal activity in the list (look for activities like 'Complete', 'Closed', 'Approved', 'Funds Available', 'Done', etc.); 'start of the process' or 'first activity' → match to the first activity in the list.
- For time_span intent with relative references, always extract activity_a and activity_b by matching semantically to the provided activity list.
- If you cannot determine the intent confidently, set intent to 'unknown'.
- Output ONLY the JSON object — no explanation, no markdown fences."""


def _strip_fences(text: str) -> str:
    return re.sub(r'^```\w*\n?', '', text.strip()).rstrip('`').strip()


def parse_observation_with_llm(
    text: str,
    activities: List[str],
    resources: List[str],
    attr_values: Dict[str, List[str]],
    segment_note_fn,
    cab: dict,
    end_activities: Optional[List[str]] = None,
    start_activities: Optional[List[str]] = None,
) -> Optional[dict]:
    """
    Returns a parse-scenario response dict, or None if LLM is unavailable / returns unknown.
    Caller should fall back to the regex parser when None is returned.
    """
    api_key  = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    model    = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-latest")

    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        log.warning("anthropic SDK not installed — falling back to regex parser")
        return None

    try:
        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        user_msg = (
            f"Known activities: {json.dumps(activities[:40])}\n"
            f"Start activities (first in process): {json.dumps((start_activities or activities[:3])[:5])}\n"
            f"End activities (last in process): {json.dumps((end_activities or activities[-3:])[:5])}\n"
            f"Known resources: {json.dumps(resources[:20])}\n"
            f"Case attribute values: {json.dumps({a: v[:6] for a, v in list(attr_values.items())[:5]})}\n\n"
            f"Parse this observation: {json.dumps(text)}"
        )

        client = anthropic.Anthropic(**client_kwargs)
        response = client.messages.create(
            model=model,
            max_tokens=400,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = _strip_fences(response.content[0].text)
        parsed = json.loads(raw)
    except Exception as e:
        log.warning(f"LLM parse error: {e}")
        return None

    intent = parsed.get("intent", "unknown")
    if intent == "unknown":
        return None

    notes: List[str]  = list(parsed.get("notes") or [])
    act       = parsed.get("activity")
    act_a     = parsed.get("activity_a")
    act_b     = parsed.get("activity_b")
    res       = parsed.get("resource")
    num       = parsed.get("number")
    unit      = parsed.get("unit")
    direction = parsed.get("direction") or "reduce"
    operator  = parsed.get("operator") or "lte"
    ambiguous = list(parsed.get("ambiguous_activities") or [])
    seg_val   = parsed.get("segment_value")

    if ambiguous:
        notes.append(f"Matched '{act}'. Other similar: {', '.join(repr(a) for a in ambiguous[:2])}.")
    if seg_val:
        note = segment_note_fn(text, cab, attr_values)
        if note:
            notes.append(note)

    def _m(t: str, **kw) -> dict:
        return {"type": t, "baseline_value": 0.0, "modified_value": float(num or 0),
                "change_type": "set_value", "value": float(num or 0),
                "description": "", **kw}

    conf = "high" if not ambiguous else "medium"

    # ── engine-enforced intents ──────────────────────────────────────────────
    if intent == "max_visits" and act and num is not None:
        cap = int(num)
        return {
            "understood_as": f"'{act}' at most {cap} time(s) per case",
            "modifications": [_m("max_visits", activity=act, change_type="set_value",
                                 value=float(cap), modified_value=float(cap),
                                 description=f"'{act}' at most {cap} time(s) per case")],
            "constraint": None, "notes": notes, "engine_enforced": True, "confidence": conf,
        }

    if intent == "time" and act and num is not None:
        ct = "reduce_pct" if direction == "reduce" else "increase_pct"
        return {
            "understood_as": f"{direction} '{act}' duration by {num:.0f}%",
            "modifications": [_m("time", activity=act, change_type=ct, value=float(num),
                                 modified_value=float(num),
                                 description=f"'{act}' duration {direction} {num:.0f}%")],
            "constraint": None, "notes": notes, "engine_enforced": True, "confidence": conf,
        }

    if intent == "resource_capacity" and num is not None:
        r = res or "all resources"
        ct = "increase_pct" if direction == "increase" else ("set_value" if unit == "slots" else "reduce_pct")
        sfx = f"+{int(num)} slot(s)" if unit == "slots" else f"capacity {direction} {num:.0f}%"
        return {
            "understood_as": f"{direction} capacity of '{r}'",
            "modifications": [_m("resource_capacity", resource=r, change_type=ct, value=float(num),
                                 modified_value=float(num), description=f"'{r}' {sfx}")],
            "constraint": None, "notes": notes, "engine_enforced": True, "confidence": "medium",
        }

    if intent == "rework_rate" and act and num is not None:
        ct = "reduce_pct" if direction == "reduce" else "increase_pct"
        return {
            "understood_as": f"{direction} rework at '{act}' by {num:.0f}%",
            "modifications": [_m("rework_rate", activity=act, change_type=ct, value=float(num),
                                 modified_value=float(num),
                                 description=f"'{act}' rework {direction} {num:.0f}%")],
            "constraint": None, "notes": notes, "engine_enforced": True, "confidence": conf,
        }

    if intent == "arrival" and num is not None:
        ct = "reduce_pct" if direction == "reduce" else "increase_pct"
        return {
            "understood_as": f"{direction} arrival rate by {num:.0f}%",
            "modifications": [{"type": "arrival", "change_type": ct, "value": float(num),
                                "description": f"Arrival rate {direction} {num:.0f}%",
                                "baseline_value": 0.0, "modified_value": float(num)}],
            "constraint": None, "notes": notes, "engine_enforced": True, "confidence": "high",
        }

    # ── post-run constraint intents ──────────────────────────────────────────
    if intent == "time_span" and act_a and act_b and num is not None:
        op_sym = "≤" if operator == "lte" else "≥"
        notes.append("Engine cannot enforce time-span constraints — checked post-run.")
        return {
            "understood_as": f"from '{act_a}' to '{act_b}' {op_sym} {num} {unit or 'days'}",
            "modifications": [],
            "constraint": {"type": "time_span", "text": text, "activity_a": act_a, "activity_b": act_b,
                           "operator": operator, "threshold": float(num), "unit": unit or "days"},
            "notes": notes, "engine_enforced": False, "confidence": "high",
        }

    if intent == "activity_absence" and act:
        notes.append("Checked post-run — engine does not prevent activities during routing.")
        return {
            "understood_as": f"'{act}' must not occur",
            "modifications": [],
            "constraint": {"type": "activity_absence", "text": text, "activity": act,
                           "operator": "lte", "threshold": 0.0, "unit": "pct"},
            "notes": notes, "engine_enforced": False, "confidence": conf,
        }

    if intent == "activity_presence" and act:
        notes.append("Checked post-run — engine does not force activities during routing.")
        return {
            "understood_as": f"'{act}' must occur in every case",
            "modifications": [],
            "constraint": {"type": "activity_presence", "text": text, "activity": act,
                           "operator": "gte", "threshold": 0.0, "unit": "pct"},
            "notes": notes, "engine_enforced": False, "confidence": conf,
        }

    if intent == "resource_utilisation" and num is not None:
        # resource may be null when the observation refers to a group ("resources in region 1")
        res_label = f"'{res}'" if res else "all resources"
        seg_note = segment_note_fn(text, cab, attr_values) if seg_val else None
        if seg_note:
            notes.append(seg_note)
        notes.append(f"Checked post-run against simulated resource utilisation.")
        return {
            "understood_as": f"{res_label} utilisation ≤ {num}%",
            "modifications": [],
            "constraint": {"type": "resource_utilisation", "text": text, "resource": res,
                           "operator": operator, "threshold": float(num), "unit": "pct"},
            "notes": notes, "engine_enforced": False, "confidence": "high" if res else "medium",
        }

    if intent == "rework_rate_constraint" and num is not None:
        notes.append("Checked post-run against simulated rework rate.")
        return {
            "understood_as": f"rework rate ≤ {num}%",
            "modifications": [],
            "constraint": {"type": "rework_rate", "text": text,
                           "operator": operator, "threshold": float(num), "unit": "pct"},
            "notes": notes, "engine_enforced": False, "confidence": "high",
        }

    return None  # let regex handle it
