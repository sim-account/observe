import unittest

import pandas as pd

from src.process_behaviour import (
    _compute_log_metrics,
    _extract_resource_assignments,
    _extract_residual_wait_parameters,
    _working_duration_minutes,
)


class WorkingDurationTests(unittest.TestCase):
    def test_excludes_night_and_weekend_time(self):
        start = pd.Timestamp("2023-03-31 14:45:00+00:00")  # Friday
        end = pd.Timestamp("2023-04-03 09:08:00+00:00")    # Monday

        duration = _working_duration_minutes(start, end, 7, 15, [0, 1, 2, 3, 4])

        self.assertEqual(duration, 143.0)  # Friday 15 min + Monday 128 min

    def test_24_7_calendar_preserves_elapsed_duration(self):
        start = pd.Timestamp("2023-03-31 14:45:00+00:00")
        end = pd.Timestamp("2023-04-03 09:08:00+00:00")

        duration = _working_duration_minutes(start, end, 0, 24, list(range(7)))

        self.assertEqual(duration, (end - start).total_seconds() / 60.0)

    def test_resource_assignments_use_resource_calendar_with_global_fallback(self):
        traces = pd.DataFrame([
            {
                "case_id": "1",
                "activity": "AML check",
                "resource": "Known",
                "start_time": pd.Timestamp("2023-03-31 14:45:00+00:00"),
                "end_time": pd.Timestamp("2023-04-03 09:08:00+00:00"),
            },
            {
                "case_id": "2",
                "activity": "Review",
                "resource": "Fallback",
                "start_time": pd.Timestamp("2023-03-31 15:30:00+00:00"),
                "end_time": pd.Timestamp("2023-04-03 08:30:00+00:00"),
            },
        ])
        calendars = {
            "global_start_hour": 7,
            "global_end_hour": 16,
            "global_working_days": [0, 1, 2, 3, 4],
            "resource_calendars": {"Known": (7, 15)},
            "resource_working_days": {"Known": [0, 1, 2, 3, 4]},
        }

        observations = _extract_resource_assignments(traces, calendars).set_index("resource")

        self.assertEqual(observations.loc["Known", "mean_duration_min"], 143.0)
        self.assertEqual(observations.loc["Fallback", "mean_duration_min"], 120.0)
        self.assertTrue(observations.loc["Known", "duration_observed"])
        self.assertEqual(observations.loc["Known", "duration_source"], "observed_interval")

    def test_activity_with_95_percent_zero_intervals_is_instantaneous(self):
        start = pd.Timestamp("2023-04-03 09:00:00+00:00")
        traces = pd.DataFrame([
            {
                "case_id": str(i),
                "activity": "System check",
                "resource": "System",
                "start_time": start + pd.Timedelta(minutes=i),
                "end_time": (
                    start + pd.Timedelta(minutes=i, seconds=1)
                    if i == 19
                    else start + pd.Timedelta(minutes=i)
                ),
            }
            for i in range(20)
        ])

        observation = _extract_resource_assignments(traces).iloc[0]

        self.assertEqual(observation["zero_duration_share"], 0.95)
        self.assertTrue(observation["is_instantaneous"])
        self.assertTrue(observation["duration_observed"])
        self.assertEqual(observation["duration_source"], "observed_instantaneous")
        self.assertEqual(observation["mean_duration_min"], 0.0)
        self.assertEqual(observation["std_duration_min"], 0.0)

    def test_activity_below_zero_threshold_keeps_small_positive_duration(self):
        start = pd.Timestamp("2023-04-03 09:00:00+00:00")
        traces = pd.DataFrame([
            {
                "case_id": str(i),
                "activity": "System check",
                "resource": "System",
                "start_time": start + pd.Timedelta(minutes=i),
                "end_time": (
                    start + pd.Timedelta(minutes=i, seconds=1)
                    if i >= 18
                    else start + pd.Timedelta(minutes=i)
                ),
            }
            for i in range(20)
        ])

        observation = _extract_resource_assignments(traces).iloc[0]

        self.assertEqual(observation["zero_duration_share"], 0.9)
        self.assertFalse(observation["is_instantaneous"])
        self.assertGreater(observation["mean_duration_min"], 0.0)

    def test_missing_timestamps_mean_unknown_duration(self):
        traces = pd.DataFrame([{
            "case_id": "1",
            "activity": "Recorded event",
            "resource": "System",
        }])

        observation = _extract_resource_assignments(traces).iloc[0]

        self.assertFalse(observation["duration_observed"])
        self.assertEqual(observation["duration_source"], "unavailable")
        self.assertTrue(pd.isna(observation["mean_duration_min"]))

    def test_negative_interval_is_invalid_not_zero(self):
        start = pd.Timestamp("2023-04-03 09:00:00+00:00")

        duration = _working_duration_minutes(
            start, start - pd.Timedelta(seconds=1), 0, 24, list(range(7))
        )

        self.assertTrue(pd.isna(duration))

    def test_log_metrics_include_training_start_timestamp(self):
        traces = pd.DataFrame([
            {
                "case_id": "1",
                "activity": "A",
                "start_time": pd.Timestamp("2019-03-25 08:00:00+00:00"),
                "end_time": pd.Timestamp("2019-03-25 08:01:00+00:00"),
            },
            {
                "case_id": "1",
                "activity": "B",
                "start_time": pd.Timestamp("2019-03-25 09:00:00+00:00"),
                "end_time": pd.Timestamp("2019-03-25 09:01:00+00:00"),
            },
        ])

        metrics = _compute_log_metrics(traces)

        self.assertEqual(metrics["log_start_iso"], "2019-03-25T08:00:00+00:00")


class ResidualWaitObservationTests(unittest.TestCase):
    def test_excludes_closed_calendar_time(self):
        traces = pd.DataFrame([
            {
                "case_id": "1", "activity": "A", "resource": "R",
                "start_time": pd.Timestamp("2023-03-31 16:30:00+00:00"),
                "end_time": pd.Timestamp("2023-03-31 17:00:00+00:00"),
            },
            {
                "case_id": "1", "activity": "B", "resource": "R",
                "start_time": pd.Timestamp("2023-04-03 10:00:00+00:00"),
                "end_time": pd.Timestamp("2023-04-03 10:30:00+00:00"),
            },
        ])
        calendars = {
            "global_start_hour": 9, "global_end_hour": 17,
            "global_working_days": [0, 1, 2, 3, 4],
            "resource_calendars": {"R": (9, 17)},
            "resource_working_days": {"R": [0, 1, 2, 3, 4]},
        }

        observation = _extract_residual_wait_parameters(traces, calendars, {"R": 1}).iloc[0]

        self.assertEqual(observation["from_activity"], "A")
        self.assertEqual(observation["activity"], "B")
        self.assertEqual(observation["mean_positive_minutes"], 60.0)

    def test_subtracts_observed_resource_contention(self):
        traces = pd.DataFrame([
            {
                "case_id": "1", "activity": "A", "resource": "Other",
                "start_time": pd.Timestamp("2023-04-03 08:30:00+00:00"),
                "end_time": pd.Timestamp("2023-04-03 09:00:00+00:00"),
            },
            {
                "case_id": "1", "activity": "B", "resource": "R",
                "start_time": pd.Timestamp("2023-04-03 11:00:00+00:00"),
                "end_time": pd.Timestamp("2023-04-03 12:00:00+00:00"),
            },
            {
                "case_id": "2", "activity": "Busy", "resource": "R",
                "start_time": pd.Timestamp("2023-04-03 09:00:00+00:00"),
                "end_time": pd.Timestamp("2023-04-03 11:00:00+00:00"),
            },
        ])

        observation = _extract_residual_wait_parameters(traces, None, {"R": 1}).iloc[0]

        self.assertEqual(observation["positive_wait_probability"], 0.0)
        self.assertEqual(observation["mean_positive_minutes"], 0.0)

    def test_excludes_parallel_successors(self):
        traces = pd.DataFrame([
            {
                "case_id": "1", "activity": "A", "resource": "R1",
                "start_time": pd.Timestamp("2023-04-03 09:00:00+00:00"),
                "end_time": pd.Timestamp("2023-04-03 11:00:00+00:00"),
            },
            {
                "case_id": "1", "activity": "B", "resource": "R2",
                "start_time": pd.Timestamp("2023-04-03 10:00:00+00:00"),
                "end_time": pd.Timestamp("2023-04-03 12:00:00+00:00"),
            },
        ])

        observations = _extract_residual_wait_parameters(traces, None, {"R1": 1, "R2": 1})

        self.assertTrue(observations.empty)


if __name__ == "__main__":
    unittest.main()
