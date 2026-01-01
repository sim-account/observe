import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import unittest

import pandas as pd

from evaluation.as_is.run_evaluation import WARMUP_DAYS, derive_warmup_days


class EvaluationWarmupTests(unittest.TestCase):
    def test_derives_warmup_from_training_and_test_boundaries(self):
        observations = {
            "log_metrics": {
                "log_start_iso": "2019-03-25T08:00:00+00:00",
            },
        }
        test_start = pd.Timestamp("2019-05-20 18:52:41.928000+00:00")

        self.assertEqual(derive_warmup_days(observations, test_start), 56)

    def test_uses_fallback_without_training_boundary(self):
        self.assertEqual(
            derive_warmup_days({}, pd.Timestamp("2019-05-20 18:52:41+00:00")),
            WARMUP_DAYS,
        )


if __name__ == "__main__":
    unittest.main()
