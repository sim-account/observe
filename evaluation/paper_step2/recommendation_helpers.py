from __future__ import annotations

import argparse

import asyncio

import hashlib

import json

from pathlib import Path

from types import SimpleNamespace

import numpy as np

import pandas as pd

from scipy.stats import t

from simulation_helpers import serialize_model

import session_store

from routers.simulate import observations_recommendations

from replay_helpers import check_replay

def final_recommendations(combined, ca_observations=None):
    """Use the endpoint's actual merging, category limits, and ranking in memory."""
    session_id = "resource-injection-study"
    session_store.create(session_id)
    session_store.update(session_id, baseline_model=serialize_model(combined))
    if ca_observations:
        session_store.update(session_id, case_attribute_observations=ca_observations)
    return asyncio.run(observations_recommendations(session_id))["recommendations"]

