from pathlib import Path
import os
ROOT = Path(__file__).resolve().parents[1]
def data_root(explicit=None):
    return Path(explicit or os.environ.get("OBSERVE_DATA_DIR") or ROOT / "observe-data").resolve()
def output_path(explicit, inputs):
    result = Path(explicit).resolve()
    if result == inputs or inputs in result.parents:
        raise ValueError("Output must be outside the immutable data bundle")
    return result
