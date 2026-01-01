import unittest
from datetime import datetime, timedelta

import pandas as pd

from src.simulation_engine import (
    SimulationConfig,
    _add_working_minutes,
    _next_work_time,
    _work_to_calendar,
    build_simulation_log,
    run_simulation,
)


class BuildSimulationLogTests(unittest.TestCase):
    def test_default_calendar_epoch_is_monday(self):
        days = [0, 1, 2, 3, 4]

        self.assertEqual(_next_work_time(0.0, 420.0, 960.0, days), 420.0)
        self.assertEqual(_work_to_calendar(0.0, 420.0, 960.0, days), 420.0)

    def test_preserves_calendar_minute_offsets_with_restricted_calendar(self):
        config = SimulationConfig(
            work_start_hour=7,
            work_end_hour=16,
            working_days=[0, 1, 2, 3, 4],
        )
        cases = [{
            "case_id": 1,
            "case_attributes": {},
            "events": [{
                "activity": "Review",
                "start_wmin": 1920.0,  # Tuesday 08:00 on the DES calendar timeline
                "end_wmin": 1980.0,
                "resource": "Analyst",
                "wait_wmin": 0.0,
            }],
        }]

        log = build_simulation_log(cases, config, log_end_iso=None)
        anchor = datetime(2025, 1, 1)

        self.assertEqual(log.iloc[0]["start_time"], anchor + timedelta(minutes=1920))
        self.assertEqual(log.iloc[0]["end_time"], anchor + timedelta(minutes=1980))
        self.assertEqual(
            log.iloc[0]["end_time"] - log.iloc[0]["start_time"],
            timedelta(minutes=60),
        )

    def test_preserves_offsets_with_24_7_calendar(self):
        config = SimulationConfig()
        cases = [{
            "case_id": 1,
            "case_attributes": {},
            "events": [{
                "activity": "Review",
                "start_wmin": 600.0,
                "end_wmin": 660.0,
                "resource": "Analyst",
                "wait_wmin": 5.0,
            }],
        }]

        log = build_simulation_log(cases, config, log_end_iso=None)
        anchor = datetime(2025, 1, 1)

        self.assertEqual(log.iloc[0]["start_time"], anchor + timedelta(minutes=600))
        self.assertEqual(log.iloc[0]["end_time"], anchor + timedelta(minutes=660))

    def test_explicit_start_controls_calendar_phase_and_export(self):
        start = datetime(2023, 4, 20, 8, 0)  # Thursday
        config = SimulationConfig(
            work_start_hour=7,
            work_end_hour=16,
            working_days=[0, 1, 2, 3, 4],
            simulation_start=start,
        )
        cases = [{
            "case_id": 1,
            "case_attributes": {},
            "events": [{
                "activity": "Review",
                "start_wmin": 0.0,
                "end_wmin": 60.0,
                "resource": "Analyst",
                "wait_wmin": 0.0,
            }],
        }]

        log = build_simulation_log(cases, config, log_end_iso="2040-01-01T00:00:00")

        self.assertEqual(log.iloc[0]["start_time"], start)
        self.assertEqual(log.iloc[0]["end_time"], start + timedelta(hours=1))
        self.assertEqual(
            _next_work_time(2 * 1440.0, 7 * 60.0, 16 * 60.0, [0, 1, 2, 3, 4], start),
            95 * 60.0,  # Saturday 08:00 advances to Monday 07:00
        )

    def test_work_time_offsets_are_relative_to_explicit_start(self):
        start = datetime(2023, 4, 20, 8, 0)  # Thursday within a 07:00-16:00 shift
        days = [0, 1, 2, 3, 4]

        self.assertEqual(_work_to_calendar(0.0, 420.0, 960.0, days, start), 0.0)
        self.assertEqual(
            _work_to_calendar(540.0, 420.0, 960.0, days, start),
            1440.0,  # 8 working hours on Thursday + 1 on Friday -> Friday 08:00
        )
        self.assertEqual(
            _work_to_calendar(-120.0, 420.0, 960.0, days, start),
            -1020.0,  # 1 hour to Thursday 07:00, then 1 hour back to Wednesday 15:00
        )

    def test_add_working_minutes_crosses_closed_weekend_once(self):
        monday = datetime(2023, 4, 3, 0, 0)
        friday_1600 = 4 * 1440.0 + 16 * 60.0

        result = _add_working_minutes(
            friday_1600, 120.0, 9 * 60.0, 17 * 60.0,
            [0, 1, 2, 3, 4], monday,
        )

        self.assertEqual(result, 7 * 1440.0 + 10 * 60.0)  # Monday 10:00

    def test_warmup_successors_are_not_postponed_to_time_zero(self):
        activity_durations = pd.DataFrame([
            {"activity": "A", "mean_minutes": 60.0, "std_minutes": 0.0},
            {"activity": "B", "mean_minutes": 60.0, "std_minutes": 0.0},
        ])
        resource_assignments = pd.DataFrame([
            {
                "activity": activity,
                "resource": "R",
                "probability": 1.0,
                "mean_duration_min": 60.0,
                "std_duration_min": 0.0,
            }
            for activity in ["A", "B"]
        ])
        observations = {
            "activity_durations_df": activity_durations,
            "resource_assignments_df": resource_assignments,
            "arrival_parameters": {"cases_per_day": 0.01},
            "resource_capacity_dict": {"R": 1},
            "start_activities": ["A"],
            "end_activities": {"B"},
            "terminal_probs": {"B": 1.0},
            "prefix_tree": {"A": [("B", 1.0)]},
            "log_metrics": {},
        }
        config = SimulationConfig(
            num_cases=3,
            warmup_cases=2,
            random_seed=42,
            simulation_start=datetime(2023, 4, 20, 8, 0),
        )

        result = run_simulation(observations, config)
        measured_case = result["cases"][0]

        self.assertEqual(measured_case["events"][0]["start_wmin"], 0.0)
        self.assertEqual(measured_case["wait_minutes"], 0.0)

    def test_observed_zero_resource_duration_overrides_inter_event_fallback(self):
        observations = {
            "activity_durations_df": pd.DataFrame([{
                "activity": "Instant",
                "mean_minutes": 1000.0,
                "std_minutes": 0.0,
            }]),
            "resource_assignments_df": pd.DataFrame([{
                "activity": "Instant",
                "resource": "System",
                "probability": 1.0,
                "mean_duration_min": 0.0,
                "std_duration_min": 0.0,
                "duration_observed": True,
            }]),
            "arrival_parameters": {"cases_per_day": 1.0},
            "resource_capacity_dict": {"System": 1},
            "start_activities": ["Instant"],
            "end_activities": {"Instant"},
            "terminal_probs": {"Instant": 1.0},
            "prefix_tree": {},
            "log_metrics": {},
        }
        config = SimulationConfig(
            num_cases=1,
            simulation_start=datetime(2023, 4, 20, 8, 0),
        )

        event = run_simulation(observations, config)["cases"][0]["events"][0]

        self.assertEqual(event["start_wmin"], event["end_wmin"])

    def test_legacy_zero_resource_duration_keeps_inter_event_fallback(self):
        observations = {
            "activity_durations_df": pd.DataFrame([{
                "activity": "Unknown",
                "mean_minutes": 60.0,
                "std_minutes": 0.0,
            }]),
            # No duration_observed marker: this represents a legacy observation where
            # zero could mean either instantaneous or unavailable.
            "resource_assignments_df": pd.DataFrame([{
                "activity": "Unknown",
                "resource": "System",
                "probability": 1.0,
                "mean_duration_min": 0.0,
                "std_duration_min": 0.0,
            }]),
            "arrival_parameters": {"cases_per_day": 1.0},
            "resource_capacity_dict": {"System": 1},
            "start_activities": ["Unknown"],
            "end_activities": {"Unknown"},
            "terminal_probs": {"Unknown": 1.0},
            "prefix_tree": {},
            "log_metrics": {},
        }
        config = SimulationConfig(
            num_cases=1,
            random_seed=42,
            simulation_start=datetime(2023, 4, 20, 8, 0),
        )

        event = run_simulation(observations, config)["cases"][0]["events"][0]

        self.assertGreater(event["end_wmin"] - event["start_wmin"], 0.0)

    def test_residual_wait_delays_release_before_resource_queueing(self):
        observations = {
            "activity_durations_df": pd.DataFrame([
                {"activity": "A", "mean_minutes": 0.0, "std_minutes": 0.0},
                {"activity": "B", "mean_minutes": 30.0, "std_minutes": 0.0},
            ]),
            "resource_assignments_df": pd.DataFrame([
                {
                    "activity": "A", "resource": "R", "probability": 1.0,
                    "mean_duration_min": 0.0, "std_duration_min": 0.0,
                    "duration_observed": True,
                },
                {
                    "activity": "B", "resource": "R", "probability": 1.0,
                    "mean_duration_min": 30.0, "std_duration_min": 0.0,
                    "duration_observed": True,
                },
            ]),
            "residual_wait_parameters_df": pd.DataFrame([{
                "from_activity": "A", "activity": "B", "observations": 5,
                "positive_wait_probability": 1.0,
                "mean_positive_minutes": 60.0,
                "positive_wait_samples_min": [60.0],
                "context_supported": True,
            }]),
            "arrival_parameters": {"cases_per_day": 1440.0},
            "resource_capacity_dict": {"R": 1},
            "start_activities": ["A"],
            "end_activities": {"B"},
            "terminal_probs": {"B": 1.0},
            "prefix_tree": {"A": [("B", 1.0)]},
            "log_metrics": {},
        }
        config = SimulationConfig(
            num_cases=2, random_seed=42,
            simulation_start=datetime(2023, 4, 20, 8, 0),
        )

        result = run_simulation(observations, config)
        case = result["cases"][0]
        first, second = case["events"]

        self.assertEqual(second["start_wmin"] - first["end_wmin"], 60.0)
        self.assertEqual(second["residual_wait_wmin"], 60.0)
        self.assertEqual(second["resource_wait_wmin"], 0.0)
        # The first case's delayed B must not reserve R before it is released;
        # the second case can still execute its initial A during that interval.
        self.assertLess(result["cases"][1]["events"][0]["start_wmin"], 60.0)


if __name__ == "__main__":
    unittest.main()
