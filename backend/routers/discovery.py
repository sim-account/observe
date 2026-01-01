import io
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from fastapi import APIRouter, HTTPException

import session_store
from models import MapColumnsRequest, ModelSummaryResponse
from serializers import serialize_model
from src.event_log_parser import parse_event_log
from src.process_behaviour import extract_process_behaviour, extract_case_attribute_behaviour
from src.process_observations import extract_process_observations, _extract_conditional_parallel_rules
from src.case_attribute_extractor import extract_case_attribute_observations, derive_case_log_from_event_log

router = APIRouter()


@router.post("/session/{session_id}/map-columns")
async def map_columns(session_id: str, body: MapColumnsRequest):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    raw_json = session.get("raw_df")
    if raw_json is None:
        raise HTTPException(status_code=400, detail="No uploaded file in session")

    df = pd.read_json(io.StringIO(raw_json), orient="split")
    mapping_dict = body.column_mapping.model_dump(exclude_none=True)
    try:
        traces = parse_event_log(df, mapping_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Column mapping failed: {e}")

    try:
        behaviour = extract_process_behaviour(traces)
        observations_dict = extract_process_observations(traces)
        observations = {
            **behaviour,
            "eventual_sequence_rules":   observations_dict["eventual_sequence_rules"],
            "conditional_time_rules":    observations_dict["conditional_time_rules"],
            "conditional_rework_rules":  observations_dict["conditional_rework_rules"],
            "resource_routing_rules":    observations_dict["resource_routing_rules"],
            "conditional_routing_rules": observations_dict["conditional_routing_rules"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Observation extraction failed: {e}")

    serialized = serialize_model(observations)
    session_store.update(session_id, discovered_model=serialized, baseline_model=serialized,
                         traces=traces.to_json(orient="split", date_format="iso"))

    # ── Case attribute observations ───────────────────────────────────────────────────
    # Priority: explicitly uploaded case log > extra columns in the event log
    case_log_raw = session.get("case_log_raw")
    try:
        if case_log_raw:
            case_df = pd.read_json(io.StringIO(case_log_raw), orient="split")
            case_id_col = body.case_log_case_id_col or session.get("case_log_case_id_col") or case_df.columns[0]
            if body.case_log_case_id_col:
                session_store.update(session_id, case_log_case_id_col=body.case_log_case_id_col)
        else:
            # Derive case attributes from unmapped columns in the original event log
            used_cols = set(body.column_mapping.model_dump(exclude_none=True).values())
            case_df = derive_case_log_from_event_log(df, body.column_mapping.case_id, used_cols)
            case_id_col = "case_id"

        if case_df is not None:
            ca_observations = extract_case_attribute_observations(traces, case_df, case_id_col)
            session_store.update(session_id, case_attribute_observations=ca_observations)
            try:
                cab = extract_case_attribute_behaviour(traces, case_df, case_id_col)
                session_store.update(session_id, case_attribute_behaviour=cab)
            except Exception as e:
                import traceback
                print(f"[WARN] case_attribute_behaviour extraction failed: {e}", flush=True)
                traceback.print_exc()
    except Exception as e:
        import traceback
        print(f"[WARN] case attribute extraction failed: {e}", flush=True)
        traceback.print_exc()

    # ── Conditional parallel rules ─────────────────────────────────────────
    try:
        par_pairs_df = _rebuild_df(serialized.get("parallel_pairs_df"))
        case_log_raw2 = session_store.get(session_id).get("case_log_raw")
        if case_log_raw2:
            cdf2 = pd.read_json(io.StringIO(case_log_raw2), orient="split")
            cid2 = body.case_log_case_id_col or session_store.get(session_id).get("case_log_case_id_col") or cdf2.columns[0]
        else:
            used_cols2 = set(body.column_mapping.model_dump(exclude_none=True).values())
            cdf2 = derive_case_log_from_event_log(df, body.column_mapping.case_id, used_cols2)
            cid2 = "case_id"
        cond_par = _extract_conditional_parallel_rules(
            traces, parallel_pairs_df=par_pairs_df, case_df=cdf2, case_id_col=cid2
        )
        session_store.update(session_id, conditional_parallel_rules=(
            cond_par.to_dict(orient="records") if cond_par is not None and not cond_par.empty else []
        ))
    except Exception as e:
        import traceback
        print(f"[WARN] conditional parallel rules extraction failed: {e}", flush=True)
        traceback.print_exc()

    return {"ok": True, "activities": observations.get("activity_durations_df", pd.DataFrame()).get("activity", pd.Series()).tolist() if observations.get("activity_durations_df") is not None else []}



@router.get("/session/{session_id}/model-summary", response_model=ModelSummaryResponse)
async def model_summary(session_id: str):
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    observations_data = session.get("discovered_model")
    if observations_data is None:
        raise HTTPException(status_code=400, detail="Observations not yet extracted")

    time_df = _rebuild_df(observations_data.get("activity_durations_df"))
    resource_df = _rebuild_df(observations_data.get("resource_assignments_df"))
    rework_df = _rebuild_df(observations_data.get("rework_parameters_df"))
    start_acts = observations_data.get("start_activities") or []
    end_acts = observations_data.get("end_activities") or []
    arrival = observations_data.get("arrival_parameters") or {}
    log_metrics = dict(observations_data.get("log_metrics") or {})
    calendars = observations_data.get("calendars") or {}
    log_metrics["global_start_hour"] = calendars.get("global_start_hour", 0)
    log_metrics["global_end_hour"]   = calendars.get("global_end_hour", 24)

    activities = time_df["activity"].tolist() if time_df is not None and not time_df.empty else []
    resources = sorted(resource_df["resource"].unique().tolist()) if resource_df is not None and not resource_df.empty else []

    rework_activities = []
    if rework_df is not None and not rework_df.empty:
        col = "rework_rate_given_visit_pct" if "rework_rate_given_visit_pct" in rework_df.columns else "rework_rate_pct"
        for _, row in rework_df.iterrows():
            rework_activities.append({
                "activity": row["activity"],
                "rework_rate_pct": float(row[col]),
                "mean_extra_loops": float(row.get("mean_extra_loops", 1.0)),
                "max_loops": int(row.get("max_loops", 1)),
            })

    # ── Parallel activities ────────────────────────────────────────────────────
    parallel_act_df = _rebuild_df(observations_data.get("parallel_parameters_df"))
    parallel_pairs_df = _rebuild_df(observations_data.get("parallel_pairs_df"))

    parallel_activities = []
    if parallel_act_df is not None and not parallel_act_df.empty:
        for _, row in parallel_act_df.iterrows():
            act = row["activity"]
            # Gather all partners from pairs df
            partners = []
            if parallel_pairs_df is not None and not parallel_pairs_df.empty:
                mask_a = parallel_pairs_df["activity_a"] == act
                mask_b = parallel_pairs_df["activity_b"] == act
                for _, pr in parallel_pairs_df[mask_a].iterrows():
                    partners.append({
                        "activity": pr["activity_b"],
                        "co_pct": float(pr["a_parallel_with_b_pct"]),
                        "count": int(pr["co_parallel_count"]),
                        "mean_overlap_min": float(pr["mean_overlap_min"]),
                    })
                for _, pr in parallel_pairs_df[mask_b].iterrows():
                    partners.append({
                        "activity": pr["activity_a"],
                        "co_pct": float(pr["b_parallel_with_a_pct"]),
                        "count": int(pr["co_parallel_count"]),
                        "mean_overlap_min": float(pr["mean_overlap_min"]),
                    })
                partners.sort(key=lambda x: -x["count"])

            parallel_activities.append({
                "activity": act,
                "parallel_rate_pct": float(row["parallel_rate_pct"]),
                "parallel_count": int(row["parallel_count"]),
                "total_count": int(row["total_count"]),
                "top_partners": partners,
            })

    return ModelSummaryResponse(
        activities=activities,
        resources=resources,
        start_activities=list(start_acts),
        end_activities=list(end_acts),
        cases_per_day=float(arrival.get("cases_per_day", 0)),
        rework_activities=rework_activities,
        log_metrics=log_metrics,
        parallel_activities=parallel_activities,
    )


def _rebuild_df(data):
    if data is None:
        return None
    import pandas as pd
    return pd.DataFrame(data["data"], columns=data["columns"])
