from __future__ import annotations

import argparse

import copy

from datetime import datetime

import hashlib

import json

from pathlib import Path

import numpy as np

import pandas as pd

from simulation_helpers import (ROOT, TABLES, parse_csv, write_json,
                       extract_process_behaviour, extract_process_observations,
                       _collect_recommendations, _apply_modifications,
                       SimulationConfig, run_simulation, build_simulation_log,
                       serialize_model)

from serializers import deserialize_model

def read(path):
    return json.loads(path.read_text())

def as_json(value):
    return json.loads(json.dumps(value, default=lambda v: v.item() if isinstance(v, np.generic) else v))

def saved_model(path):
    return deserialize_model(read(path))

def check_replay(directory):
    parameters = saved_model(directory / "simulation_model.json")
    settings = read(directory / "simulation_settings.json")
    settings["simulation_start"] = datetime.fromisoformat(settings["simulation_start"])
    config = SimulationConfig(**settings)
    result = run_simulation(parameters, config)
    kpis = read(directory / "kpis.json")
    for key, value in kpis.items():
        if isinstance(value, (int, float)):
            assert np.isclose(result[key], value, rtol=1e-12, atol=1e-12), f"Replayed KPI differs: {key}"
    assert result["resource_utilization"] == kpis["resource_utilization"]
    # Check all displayed bottleneck values; only exact-share ties can change presentation order.
    sort_key = lambda row: (-row["total_wait_share_pct"], row["activity"])
    assert sorted(result["bottlenecks"], key=sort_key) == sorted(kpis["bottlenecks"], key=sort_key)
    expected = build_simulation_log(result["cases"], config, None)
    actual = pd.read_csv(directory / "events.csv", dtype={"case_id": str})
    expected["case_id"] = expected["case_id"].astype(str)
    for field in ("start_time", "end_time"):
        expected[field] = pd.to_datetime(expected[field], format="mixed", utc=True)
        actual[field] = pd.to_datetime(actual[field], format="mixed", utc=True)
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False,
                                  check_exact=False, rtol=1e-12, atol=1e-8)
    return read(directory / "simulation_settings.json"), kpis

