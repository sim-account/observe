from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel


class ColumnMapping(BaseModel):
    case_id: str
    activity: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    resource: Optional[str] = None
    source: Optional[str] = None


class MapColumnsRequest(BaseModel):
    session_id: str
    column_mapping: ColumnMapping
    case_log_case_id_col: Optional[str] = None


class Modification(BaseModel):
    type: str  # "time", "resource_share", "rework_rate", "arrival", "resource_capacity",
               # "sequence", "parallel_rate", "decision_point", "max_visits", "case_attribute_dist"
    activity: Optional[str] = None
    resource: Optional[str] = None
    change_type: str = "reduce_pct"
    value: float = 0.0
    description: str = ""
    baseline_value: float = 0.0
    modified_value: float = 0.0
    # sequence-specific
    from_activity: Optional[str] = None
    to_activity: Optional[str] = None
    # parallel_rate-specific
    activity_a: Optional[str] = None
    activity_b: Optional[str] = None
    # decision_point-specific: list of {to_activity, probability_pct}
    branches: Optional[List[Dict[str, Any]]] = None


class Constraint(BaseModel):
    type: Literal[
        'time_span', 'activity_presence', 'activity_absence',
        'activity_count', 'resource_utilisation', 'rework_rate'
    ]
    text: str                           # original user text, shown in UI
    activity_a: Optional[str] = None   # time_span: from activity
    activity_b: Optional[str] = None   # time_span: to activity
    activity: Optional[str] = None     # presence/absence/count
    resource: Optional[str] = None     # resource_utilisation
    operator: Literal['lte', 'gte'] = 'lte'
    threshold: float = 0.0
    unit: Optional[str] = None         # 'minutes' | 'hours' | 'days' | 'count' | 'pct'


class ParseScenarioRequest(BaseModel):
    text: str


class SimulationRequest(BaseModel):
    sim_days: int = 30
    warmup_days: int = 7
    num_runs: int = 2
    random_seed: int = 42
    enable_resource_contention: bool = True
    resource_routing: str = "probabilistic"  # "probabilistic" | "availability"
    modifications: List[Modification] = []
    constraints: List[Constraint] = []


class UploadResponse(BaseModel):
    session_id: str
    row_count: int
    columns: List[str]
    detected_mapping: Dict[str, Optional[str]]


class ModelSummaryResponse(BaseModel):
    activities: List[str]
    resources: List[str]
    start_activities: List[str]
    end_activities: List[str]
    cases_per_day: float
    rework_activities: List[Dict[str, Any]]
    log_metrics: Optional[Dict[str, Any]]
    parallel_activities: List[Dict[str, Any]] = []
