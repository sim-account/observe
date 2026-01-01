import pandas as pd
from typing import Dict, Optional

COLUMN_ALIASES = {
    "case_id": ["ticketnumber", "caseid", "case_id", "traceid", "trace_id", "ticket", "ticketno", "caseidentifier"],
    "activity": ["status", "activity", "event", "task", "activityname", "activity_name", "conceptname", "eventtype"],
    "start_time": ["statusstart", "start_time", "starttime", "start", "startdate", "starttimestamp", "startts"],
    "end_time": ["statusend", "end_time", "endtime", "end", "complete", "completetime", "enddate", "endtimestamp"],
    "resource": ["sid", "resource", "user", "userid", "user_id", "agent", "worker", "performer",
                 "systemid", "system_id", "operator", "responsible", "assignee", "org:resource"],
    "source": ["source", "system", "channel", "application", "app", "platform",
               "sourcesystem", "source_system", "originator", "org:group"],
}


def detect_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    """Auto-detect standard column mapping from DataFrame columns."""
    col_normalized = {
        c.lower().replace(" ", "").replace("_", "").replace(":", ""): c
        for c in df.columns
    }
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = alias.replace("_", "").replace(":", "")
            if key in col_normalized:
                mapping[field] = col_normalized[key]
                break
    return mapping


def parse_event_log(df: pd.DataFrame, column_mapping: Dict[str, str]) -> pd.DataFrame:
    """Parse raw DataFrame into standard event log format with columns:
    case_id, activity, start_time (optional), end_time (optional), duration_minutes (optional).
    """
    result = pd.DataFrame()
    result["case_id"] = df[column_mapping["case_id"]].astype(str).str.strip()
    result["activity"] = df[column_mapping["activity"]].astype(str).str.strip()

    if "start_time" in column_mapping:
        result["start_time"] = pd.to_datetime(df[column_mapping["start_time"]], format="mixed", utc=True)
    if "end_time" in column_mapping:
        result["end_time"] = pd.to_datetime(df[column_mapping["end_time"]], format="mixed", utc=True)

    if "start_time" in result.columns and "end_time" in result.columns:
        result["duration_minutes"] = (
            (result["end_time"] - result["start_time"]).dt.total_seconds() / 60
        ).clip(lower=0)

    if "resource" in column_mapping:
        result["resource"] = df[column_mapping["resource"]].astype(str).str.strip()
    if "source" in column_mapping:
        result["source"] = df[column_mapping["source"]].astype(str).str.strip()

    if "start_time" in result.columns:
        result = result.sort_values(["case_id", "start_time"]).reset_index(drop=True)

    return result.dropna(subset=["case_id", "activity"])
