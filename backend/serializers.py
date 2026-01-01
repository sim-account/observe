from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


def df_to_dict(df: Optional[pd.DataFrame]) -> Optional[dict]:
    """Serialize a DataFrame to a JSON-safe dict (orient=split)."""
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return None
    # Convert timestamps to ISO strings
    d = df.copy()
    for col in d.select_dtypes(include=["datetimetz", "datetime64[ns]"]).columns:
        d[col] = d[col].astype(str)
    return {"columns": d.columns.tolist(), "data": _cast(d.values.tolist())}


def dict_to_df(data: Optional[dict]) -> Optional[pd.DataFrame]:
    """Reconstruct a DataFrame from a serialized dict."""
    if data is None:
        return None
    return pd.DataFrame(data["data"], columns=data["columns"])


def serialize_model(observations: Dict[str, Any]) -> Dict[str, Any]:
    """Convert observations dict (containing DataFrames) to a JSON-safe dict."""
    df_keys = [
        "activity_durations_df", "routing_probabilities_df",
        "resource_assignments_df", "rework_parameters_df", "handoff_observations_df",
        "residual_wait_parameters_df",
        "parallel_parameters_df", "parallel_pairs_df", "decision_points_df",
        "eventual_sequence_rules", "conditional_time_rules",
        "conditional_rework_rules", "resource_routing_rules", "conditional_routing_rules",
    ]
    result = {}
    for k, v in observations.items():
        if k in df_keys:
            result[k] = df_to_dict(v) if isinstance(v, pd.DataFrame) else None
        elif isinstance(v, (list, set)):
            result[k] = list(v)
        else:
            result[k] = _cast_scalar(v)
    return result


def deserialize_model(data: Dict[str, Any]) -> Dict[str, Any]:
    """Reconstruct observations dict from serialized JSON."""
    df_keys = [
        "activity_durations_df", "routing_probabilities_df",
        "resource_assignments_df", "rework_parameters_df", "handoff_observations_df",
        "residual_wait_parameters_df",
        "parallel_parameters_df", "parallel_pairs_df", "decision_points_df",
        "eventual_sequence_rules", "conditional_time_rules",
        "conditional_rework_rules", "resource_routing_rules", "conditional_routing_rules",
    ]
    result = {}
    for k, v in data.items():
        if k in df_keys:
            result[k] = dict_to_df(v)
        else:
            result[k] = v
    return result


def _cast(obj: Any) -> Any:
    if isinstance(obj, list):
        return [_cast(i) for i in obj]
    return _cast_scalar(obj)


def _cast_scalar(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if pd.isna(obj) if not isinstance(obj, (list, dict, str, type(None))) else False:
        return None
    return obj
