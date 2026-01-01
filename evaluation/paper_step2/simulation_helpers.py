from __future__ import annotations

import argparse

import copy

from dataclasses import asdict

from datetime import datetime

import hashlib

import json

from pathlib import Path

import sys

import numpy as np

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from serializers import serialize_model

from src.event_log_parser import detect_columns, parse_event_log

from src.process_behaviour import extract_process_behaviour

from src.process_observations import extract_process_observations

from src.simulation_engine import SimulationConfig, run_simulation, build_simulation_log

from routers.simulate import _apply_modifications, _collect_recommendations

FIELDS = ("case_id", "activity", "start_time", "end_time", "resource")

TABLES = ("eventual_sequence_rules", "conditional_time_rules",
          "conditional_rework_rules", "resource_routing_rules",
          "conditional_routing_rules")

METRICS = ("avg_cycle_time_hours", "avg_waiting_time_hours",
           "avg_service_time_hours", "rework_rate_pct", "cases_completed")

def write_json(path, value):
    def convert(item):
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, (datetime, pd.Timestamp)):
            return item.isoformat()
        raise TypeError(f"Cannot serialize {type(item).__name__}")
    path.write_text(json.dumps(value, default=convert, indent=2, allow_nan=False) + "\n")

def parse_csv(path):
    raw = pd.read_csv(path, dtype=str)
    mapping = detect_columns(raw)
    missing = set(FIELDS) - mapping.keys()
    if missing:
        raise ValueError(f"Missing mapped fields: {sorted(missing)}")
    # Only event fields enter discovery; exported audit columns are not case attributes.
    return parse_event_log(raw, {field: mapping[field] for field in FIELDS}), mapping

def config_for(parameters, cases, warmup, seed):
    calendars = parameters.get("calendars") or {}
    return SimulationConfig(
        num_cases=cases + warmup, warmup_cases=warmup, random_seed=seed,
        simulation_start=datetime(2025, 1, 6),
        enable_resource_contention=True, resource_routing="probabilistic",
        enable_residual_wait=True,
        work_start_hour=int(calendars.get("global_start_hour", 0)),
        work_end_hour=int(calendars.get("global_end_hour", 24)),
        working_days=calendars.get("global_working_days"),
        resource_calendars=calendars.get("resource_calendars") or {},
        resource_working_days=calendars.get("resource_working_days") or {},
    )

def simulate_and_save(parameters, args, directory):
    directory.mkdir()
    config = config_for(parameters, args.cases, args.warmup_cases, args.seed)
    result = run_simulation(parameters, config)
    events = build_simulation_log(result["cases"], config, None)
    events.to_csv(directory / "events.csv", index=False)
    # Preserve the tool's aggregate KPI payload without independently averaging cases.
    kpis = {key: value for key, value in result.items() if key != "cases"}
    write_json(directory / "kpis.json", kpis)
    write_json(directory / "simulation_settings.json", asdict(config))
    write_json(directory / "simulation_model.json", serialize_model(parameters))
    return events, kpis

