import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import io
import math
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
import session_store
from routers.discovery import _rebuild_df

router = APIRouter()


def _nan_safe_records(df) -> list:
    """Convert DataFrame to records, replacing NaN/Inf with None for JSON compliance."""
    def _clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


@router.get("/session/{session_id}/statistical-learnings")
async def process_behaviour(session_id: str):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    observations_data = session.get("discovered_model")
    if observations_data is None:
        raise HTTPException(status_code=400, detail="Observations not yet extracted")

    def df_rows(key):
        df = _rebuild_df(observations_data.get(key))
        if df is None or df.empty:
            return []
        return _nan_safe_records(df)

    time_df = _rebuild_df(observations_data.get("activity_durations_df"))
    seq_df = _rebuild_df(observations_data.get("routing_probabilities_df"))
    resource_df = _rebuild_df(observations_data.get("resource_assignments_df"))

    return {
        "activity_durations": df_rows("activity_durations_df"),
        "routing_probabilities": df_rows("routing_probabilities_df"),
        "arrival_parameters": observations_data.get("arrival_parameters") or {},
        "resource_assignments": df_rows("resource_assignments_df"),
        "residual_wait_parameters": df_rows("residual_wait_parameters_df"),
        "rework_parameters": df_rows("rework_parameters_df"),
        "parallel_parameters": df_rows("parallel_parameters_df"),
        "parallel_pairs": df_rows("parallel_pairs_df"),
        "decision_points": df_rows("decision_points_df"),
        "resource_capacity_dict": observations_data.get("resource_capacity_dict") or {},
        "peak_concurrent_dict": observations_data.get("peak_concurrent_dict") or {},
        "calendars": observations_data.get("calendars") or {},
        "log_metrics": observations_data.get("log_metrics") or {},
        "activities": time_df["activity"].tolist() if time_df is not None and not time_df.empty else [],
        "resources": sorted(resource_df["resource"].unique().tolist()) if resource_df is not None and not resource_df.empty else [],
        "from_activities": sorted(seq_df["from_activity"].unique().tolist()) if seq_df is not None and not seq_df.empty else [],
        "case_attribute_behaviour": session.get("case_attribute_behaviour") or {},
    }


@router.get("/session/{session_id}/case-attribute-observations")
async def case_attribute_observations(session_id: str):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    observations = session.get("case_attribute_observations") or []
    attributes = sorted(list(set(a["attribute"] for a in observations)))
    sub_types = sorted(list(set(a["sub_type"] for a in observations)))
    return {
        "observations": observations,
        "count": len(observations),
        "attributes": attributes,
        "sub_types": sub_types,
    }

@router.get("/session/{session_id}/process-observations")
async def process_observations_rules(session_id: str):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    observations_data = session.get("discovered_model")
    if observations_data is None:
        raise HTTPException(status_code=400, detail="Observations not yet extracted")

    def df_rows(key):
        df = _rebuild_df(observations_data.get(key))
        if df is None or df.empty:
            return []
        return _nan_safe_records(df)

    def _clean_rules(rules: list) -> list:
        def _clean(v):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            return v
        return [{k: _clean(v) for k, v in r.items()} for r in rules]

    try:
        seq_rules = [r for r in df_rows("eventual_sequence_rules") if r.get("rule_type") != "strong_implication"]
        time_rules = df_rows("conditional_time_rules")
        rework_rules = df_rows("conditional_rework_rules")
        routing_rules = df_rows("resource_routing_rules")
        cond_routing_rules = _clean_rules(df_rows("conditional_routing_rules"))
        parallel_rules = _clean_rules(session.get("conditional_parallel_rules") or [])

        result = {
            "sequence_rules": seq_rules,
            "time_rules": time_rules,
            "rework_rules": rework_rules,
            "routing_rules": routing_rules,
            "conditional_routing_rules": cond_routing_rules,
            "parallel_rules": parallel_rules,
            "counts": {
                "sequence": len(seq_rules),
                "time": len(time_rules),
                "rework": len(rework_rules),
                "routing": len(routing_rules),
                "conditional_routing": len(cond_routing_rules),
                "parallel": len(parallel_rules),
            },
            "filters": {
                "sequence_activities": sorted(list(set(r["activity_a"] for r in seq_rules))),
                "sequence_types": sorted(list(set(r["rule_type"] for r in seq_rules))),
                "time_activities": sorted(list(set(r["activity"] for r in time_rules))),
                "time_condition_types": sorted(list(set(r["condition_type"] for r in time_rules))),
                "rework_activities": sorted(list(set(r["activity"] for r in rework_rules))),
                "rework_condition_types": sorted(list(set(r["condition_type"] for r in rework_rules))),
                "routing_activities": sorted(list(set(r["activity"] for r in routing_rules))),
                "routing_predecessors": sorted(list(set(r["predecessor"] for r in routing_rules))),
                "cond_routing_gateways": sorted(list(set(r["gateway_activity"] for r in cond_routing_rules))),
                "cond_routing_condition_types": sorted(list(set(r["condition_type"] for r in cond_routing_rules))),
                "parallel_activities": sorted(list(set(r["activity"] for r in parallel_rules))),
                "parallel_rule_types": sorted(list(set(r["rule_type"] for r in parallel_rules))),
            },
        }
        return JSONResponse(content=jsonable_encoder(result))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"process-observations failed: {type(e).__name__}: {e}")


@router.patch("/session/{session_id}/case-attribute-behaviour")
async def update_case_attribute_behaviour(session_id: str, body: dict):
    """Accept manual distribution overrides from the UI.

    Body: { attr_name: { values: [{value, share}] } }
    Shares are renormalised server-side so they don't need to sum to exactly 1.
    """
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    current = dict(session.get("case_attribute_behaviour") or {})
    for attr, overrides in body.items():
        if attr not in current:
            raise HTTPException(status_code=400, detail=f"Unknown attribute: {attr!r}")
        # Expect exactly one value being pinned
        new_vals = overrides.get("values", [])
        if not new_vals:
            continue
        pinned = new_vals[0]
        pinned_val = str(pinned["value"])
        pinned_share = max(0.0, min(1.0, float(pinned.get("share", 0))))

        existing_values = current[attr].get("values", [])
        others = [v for v in existing_values if v["value"] != pinned_val]
        other_total = sum(v["share"] for v in others)
        remainder = max(0.0, 1.0 - pinned_share)

        updated = [{"value": pinned_val, "share": round(pinned_share, 4),
                    "n_cases": next((v["n_cases"] for v in existing_values if v["value"] == pinned_val), 0)}]
        for v in others:
            scaled = (v["share"] / other_total * remainder) if other_total > 0 else (remainder / len(others) if others else 0)
            updated.append({**v, "share": round(scaled, 4)})

        attr_data = dict(current[attr])
        attr_data["values"] = sorted(updated, key=lambda x: -x["share"])
        current[attr] = attr_data

    session_store.update(session_id, case_attribute_behaviour=current)
    return {"ok": True, "case_attribute_behaviour": current}


@router.post("/session/{session_id}/case-attribute-behaviour/reset")
async def reset_case_attribute_behaviour(session_id: str):
    """Reset a segment's multipliers to the originally mined values."""
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # Re-mine from stored traces and case log
    import io as _io
    from src.process_behaviour import extract_case_attribute_behaviour
    from src.case_attribute_extractor import derive_case_log_from_event_log
    traces_json = session.get("traces")
    if not traces_json:
        raise HTTPException(status_code=400, detail="Traces not available")
    traces = pd.read_json(_io.StringIO(traces_json), orient="split")
    case_log_raw = session.get("case_log_raw")
    if case_log_raw:
        case_df = pd.read_json(_io.StringIO(case_log_raw), orient="split")
        case_id_col = session.get("case_log_case_id_col") or case_df.columns[0]
    else:
        case_df = None
    if case_df is None:
        raise HTTPException(status_code=400, detail="No case log available")
    cab = extract_case_attribute_behaviour(traces, case_df, case_id_col)
    session_store.update(session_id, case_attribute_behaviour=cab)
    return {"ok": True, "case_attribute_behaviour": cab}


@router.get("/session/{session_id}/simulation-log")
async def download_simulation_log(session_id: str, format: str = "csv"):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    log_json = session.get("last_simulation_log")
    if not log_json:
        raise HTTPException(status_code=404, detail="No simulation log available — run a simulation first")

    df = pd.read_json(io.StringIO(log_json), orient="split")

    if format == "json":
        return JSONResponse(content={"columns": df.columns.tolist(), "rows": df.to_dict(orient="records")})

    # Default: CSV download
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=simulation_log.csv"},
    )
