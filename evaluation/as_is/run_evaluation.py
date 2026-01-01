"""Discover and regenerate ten OBSERVE logs per paper dataset, seeds 42–51."""
import sys
import logging
import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ── sys.path: make backend importable ────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from evaluation.paths import data_root, output_path
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.event_log_parser import detect_columns, parse_event_log
from src.process_behaviour import extract_process_behaviour
from src.simulation_engine import run_simulation, build_simulation_log, SimulationConfig

# ── evaluation metrics (log_distance_measures 2.2.0 submodule imports) ───────
try:
    from log_distance_measures.config import EventLogIDs
    from log_distance_measures.n_gram_distribution import n_gram_distribution_distance
    from log_distance_measures.absolute_event_distribution import absolute_event_distribution_distance
    from log_distance_measures.circadian_event_distribution import circadian_event_distribution_distance
    from log_distance_measures.relative_event_distribution import relative_event_distribution_distance
    from log_distance_measures.cycle_time_distribution import cycle_time_distribution_distance
    from log_distance_measures.case_arrival_distribution import case_arrival_distribution_distance
except ImportError as e:
    raise ImportError(
        f"Missing evaluation dependency: {e}. "
        "Install with: pip install log-distance-measures pm4py"
    ) from e

# ── constants ────────────────────────────────────────────────────────────────
SEEDS = list(range(42, 52))          # 10 seeds: 42–51
WARMUP_DAYS = 7                     # fallback when log boundaries are unavailable
CTDD_BIN_SIZE = datetime.timedelta(hours=1)

# ── ablation grid ─────────────────────────────────────────────────────────────
# Each key maps to the list of values to test for that parameter.
# warmup_days is passed as a positional arg to build_sim_config; all others are
# keyword-only overrides.  Add or remove entries here to extend the ablation.
ABLATION_GRID: dict[str, list] = {
    "resource_routing":           ["probabilistic", "availability"],
    "enable_resource_contention": [True, False],
    "enable_residual_wait":       [True, False],
    "warmup_days":                [0, 7, 14],
}

# Fixed values used for every dimension that is NOT currently being varied.
ABLATION_DEFAULTS: dict[str, object] = {
    "resource_routing":           "probabilistic",
    "enable_resource_contention": True,
    "enable_residual_wait":       True,
    "warmup_days":                WARMUP_DAYS,
}

LOG_IDS = EventLogIDs(
    case="case_id",
    activity="activity",
    start_time="start_time",
    end_time="end_time",
    resource="resource",
)

# Standard column names after normalisation
REQUIRED_FIELDS = {"case_id", "activity", "start_time", "end_time", "resource"}

# ── dataset registry ─────────────────────────────────────────────────────────
DATA_DIR = data_root() / "as_is/datasets"
RESULTS_DIR = REPO_ROOT / "generated-results/as_is/OBSERVE"

# column_override: only populated when detect_columns() fails to find a required field.
# Shape mirrors the column_mapping arg of parse_event_log:
#   { "case_id": "<actual_col>", "activity": "<actual_col>", ... }
DATASETS = [
    {"name": "ACR",              "column_override": None},
    {"name": "BPIC_2012W",       "column_override": None},
    {"name": "BPIC_2017W",       "column_override": None},
    {"name": "Confidential_1000","column_override": None},
    {"name": "Confidential_2000","column_override": None},
    {"name": "LoanApp",          "column_override": None},
    {"name": "P2P",              "column_override": None},
    {"name": "Production",       "column_override": None},
    {"name": "cvs_pharmacy",     "column_override": None},
    {"name": "JPMC",             "column_override": None},
]

# ── logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# U1/U2 — DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_column_mapping(df: pd.DataFrame, override: dict | None) -> dict:
    """Return a column mapping for df, using detect_columns() with fallback to override."""
    mapping = detect_columns(df)
    missing = REQUIRED_FIELDS - set(mapping.keys())

    if missing and override:
        for field in list(missing):
            if field in override:
                mapping[field] = override[field]
                missing.discard(field)

    if missing:
        raise ValueError(f"Column mapping incomplete — missing fields: {missing}. "
                         "Add a column_override entry for this dataset.")
    return mapping


def discover(train_path: Path, column_override: dict | None = None) -> dict:
    """Load training log, run detect_columns + parse_event_log, extract process behaviour.

    Returns the observations dict produced by extract_process_behaviour.
    """
    df = pd.read_csv(train_path)
    mapping = _resolve_column_mapping(df, column_override)

    traces = parse_event_log(df, mapping)

    n_cases = traces["case_id"].nunique()
    n_activities = traces["activity"].nunique()

    if n_activities < 2:
        raise ValueError(
            f"Only {n_activities} distinct activit(y|ies) found in {train_path}. "
            "Need at least 2 to build a simulation model."
        )

    log.info("  Train log: %d cases, %d activities", n_cases, n_activities)
    observations = extract_process_behaviour(traces)
    return observations


# ─────────────────────────────────────────────────────────────────────────────
# U3 — SIMULATIONCONFIG BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def derive_warmup_days(observations: dict, test_start: pd.Timestamp) -> int:
    """Return the elapsed whole days from training start to test start.

    Evaluation targets the state of the process at the test boundary. Replaying
    approximately the preceding process lifetime gives accumulating resource
    queues time to reach that state. WARMUP_DAYS remains the fallback for observations
    without a usable training start timestamp.
    """
    train_start_iso = (observations.get("log_metrics") or {}).get("log_start_iso")
    if not train_start_iso or pd.isna(test_start):
        return WARMUP_DAYS

    try:
        train_start = pd.to_datetime(train_start_iso, format="mixed", utc=True)
        test_start_utc = pd.to_datetime(test_start, utc=True)
    except (TypeError, ValueError):
        return WARMUP_DAYS

    elapsed_days = (test_start_utc - train_start).total_seconds() / 86400.0
    if elapsed_days < 0:
        return WARMUP_DAYS
    return max(0, int(round(elapsed_days)))


def build_sim_config(
    observations: dict,
    seed: int,
    sim_cases: int,
    warmup_days: int = WARMUP_DAYS,
    *,
    simulation_start: datetime.datetime | None = None,
    resource_routing: str | None = None,
    enable_resource_contention: bool | None = None,
    enable_residual_wait: bool | None = None,
) -> SimulationConfig:
    """Build a SimulationConfig for one simulation run.

    sim_cases: exact number of completed cases to simulate — set to the
               test log's unique case count for structural comparability.
    The num_cases field = warmup_cases + sim_cases.

    Keyword-only overrides (resource routing, resource contention, and residual
    waiting) default to the standard pipeline values when
    None, allowing the ablation study to vary individual parameters.
    """
    cases_per_day = float((observations.get("arrival_parameters") or {}).get("cases_per_day", 1.0))
    cases_per_day = max(cases_per_day, 1e-6)
    warmup_cases = max(0, round(cases_per_day * warmup_days))

    calendars = observations.get("calendars") or {}
    work_start = int(calendars.get("global_start_hour", 0))
    work_end   = int(calendars.get("global_end_hour", 24))
    working_days = calendars.get("global_working_days") or None
    resource_calendars = {
        str(rid): (int(s), int(e))
        for rid, (s, e) in (calendars.get("resource_calendars") or {}).items()
    }
    resource_working_days = {
        str(rid): list(map(int, days))
        for rid, days in (calendars.get("resource_working_days") or {}).items()
    }

    return SimulationConfig(
        num_cases=warmup_cases + sim_cases,
        warmup_cases=warmup_cases,
        random_seed=seed,
        enable_resource_contention=enable_resource_contention if enable_resource_contention is not None else True,
        enable_residual_wait=enable_residual_wait if enable_residual_wait is not None else True,
        resource_routing=resource_routing if resource_routing is not None else "probabilistic",
        work_start_hour=work_start,
        work_end_hour=work_end,
        working_days=working_days,
        resource_calendars=resource_calendars,
        resource_working_days=resource_working_days,
        case_attribute_behaviour={},
        simulation_start=simulation_start,
    )


# ─────────────────────────────────────────────────────────────────────────────
# U4 — SIMULATION LOOP
# ─────────────────────────────────────────────────────────────────────────────

def simulate_dataset(
    dataset_name: str,
    observations: dict,
    test_path: Path,
    results_dir: Path,
) -> bool:
    """Run 10 simulation seeds for one dataset.  Returns True if all runs succeeded."""
    # Load test log to derive anchor and case count
    test_df = pd.read_csv(test_path)

    # Detect test log column mapping to find the start_time column name
    test_mapping = detect_columns(test_df)
    start_col = test_mapping.get("start_time", "start_timestamp")

    test_case_count = test_df["case_id"].nunique() if "case_id" in test_df.columns else test_df.iloc[:, 0].nunique()
    log.info("  Test log: %d unique cases", test_case_count)

    # Derive anchor: simulation timestamps start at test log's first event
    raw_starts = pd.to_datetime(test_df[start_col], format="mixed", utc=True)
    test_start = raw_starts.min()
    warmup_days = derive_warmup_days(observations, test_start)
    # warmup_days = 7
    log.info("  Warm-up: %d days (training start → test start)", warmup_days)

    results_dir.mkdir(parents=True, exist_ok=True)
    all_ok = True

    for i, seed in enumerate(SEEDS):
        try:
            config = build_sim_config(
                observations, seed=seed, sim_cases=test_case_count,
                warmup_days=warmup_days,
                simulation_start=test_start.to_pydatetime(),
            )
            result = run_simulation(observations, config)
            sim_log = build_simulation_log(result["cases"], config, log_end_iso=None)

            out = sim_log[["case_id", "activity", "start_time", "end_time", "resource"]]
            out_path = results_dir / f"simulated_log_{i}.csv"
            out.to_csv(out_path, index=False)

            n_sim_cases = out["case_id"].nunique()
            log.info("  Seed %d (run %d): %d cases, %d events → %s",
                     seed, i, n_sim_cases, len(out), out_path.name)
        except Exception as exc:
            log.error("  Seed %d (run %d) FAILED: %s", seed, i, exc)
            all_ok = False

    return all_ok


# ─────────────────────────────────────────────────────────────────────────────
# U5 — EVALUATION
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_test_log(test_df: pd.DataFrame) -> pd.DataFrame:
    """Rename test log timestamp columns to start_time/end_time so they match LOG_IDS."""
    rename = {}
    cols_lower = {c.lower(): c for c in test_df.columns}

    for raw, standard in [("start_timestamp", "start_time"), ("end_timestamp", "end_time")]:
        # handle various capitalisation variants
        for variant in [raw, raw.replace("_", ""), raw.title().replace("_", "")]:
            if variant in test_df.columns and standard not in test_df.columns:
                rename[variant] = standard
                break
            if variant.lower() in cols_lower and standard not in test_df.columns:
                rename[cols_lower[variant.lower()]] = standard
                break

    df = test_df.rename(columns=rename)

    # Ensure timestamps are parsed as datetime
    for col in ["start_time", "end_time"]:
        if col in df.columns and not pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = pd.to_datetime(df[col], format="mixed", utc=True)

    # Normalise activity column name
    if "activity" not in df.columns:
        for candidate in ["activity_name", "Activity", "concept:name"]:
            if candidate in df.columns:
                df = df.rename(columns={candidate: "activity"})
                break

    # Normalise resource column name
    if "resource" not in df.columns:
        for candidate in ["Resource", "org:resource", "resourceId"]:
            if candidate in df.columns:
                df = df.rename(columns={candidate: "resource"})
                break

    return df


def evaluate_dataset(
    dataset_name: str,
    test_path: Path,
    results_dir: Path,
) -> dict:
    """Compute distance metrics for one dataset across 10 simulated logs.

    Returns a dict with mean and std for each metric, or NaN on failure.
    """
    nan_result = {
        "NGD_mean": float("nan"), "NGD_std": float("nan"),
        "AEDD_mean": float("nan"), "AEDD_std": float("nan"),
        "CADD_mean": float("nan"), "CADD_std": float("nan"),
        "CEDD_mean": float("nan"), "CEDD_std": float("nan"),
        "REDD_mean": float("nan"), "REDD_std": float("nan"),
        "CTDD_mean": float("nan"), "CTDD_std": float("nan"),
    }

    # Check simulated logs exist
    sim_paths = [results_dir / f"simulated_log_{i}.csv" for i in range(len(SEEDS))]
    missing = [p for p in sim_paths if not p.exists()]
    if missing:
        log.warning("  %d simulated log(s) missing — skipping evaluation", len(missing))
        return nan_result

    # Load and normalise test log
    try:
        test_raw = pd.read_csv(test_path)
        test_log = _normalise_test_log(test_raw)
    except Exception as exc:
        log.error("  Failed to load/normalise test log: %s", exc)
        return nan_result

    per_run: list[dict] = []
    for i, sim_path in enumerate(sim_paths):
        try:
            sim_log = pd.read_csv(sim_path)
            for col in ["start_time", "end_time"]:
                if col in sim_log.columns and not pd.api.types.is_datetime64_any_dtype(sim_log[col]):
                    sim_log[col] = pd.to_datetime(sim_log[col], format="mixed", utc=True)

            metrics = {
                "NGD":  n_gram_distribution_distance(test_log, LOG_IDS, sim_log, LOG_IDS),
                "AEDD": absolute_event_distribution_distance(test_log, LOG_IDS, sim_log, LOG_IDS),
                "CADD": case_arrival_distribution_distance(test_log, LOG_IDS, sim_log, LOG_IDS),
                "CEDD": circadian_event_distribution_distance(test_log, LOG_IDS, sim_log, LOG_IDS),
                "REDD": relative_event_distribution_distance(test_log, LOG_IDS, sim_log, LOG_IDS),
                "CTDD": cycle_time_distribution_distance(
                    test_log, LOG_IDS, sim_log, LOG_IDS, bin_size=CTDD_BIN_SIZE
                ),
            }
            per_run.append(metrics)
            log.info("  Run %d: NGD=%.4f  AEDD=%.4f CADD=%.4f  CEDD=%.4f  REDD=%.4f  CTDD=%.4f",
                     i, metrics["NGD"], metrics["AEDD"], metrics["CADD"], metrics["CEDD"],
                     metrics["REDD"], metrics["CTDD"])
        except Exception as exc:
            log.error("  Run %d metric computation failed: %s", i, exc)

    if not per_run:
        return nan_result

    result = {}
    for metric in ["NGD", "AEDD", "CADD", "CEDD", "REDD", "CTDD"]:
        values = [r[metric] for r in per_run if not (r[metric] != r[metric])]  # filter NaN
        result[f"{metric}_mean"] = float(np.mean(values)) if values else float("nan")
        result[f"{metric}_std"]  = float(np.std(values))  if values else float("nan")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(skip_simulation: bool = False, skip_evaluation: bool = False) -> pd.DataFrame:
    """Run the full pipeline for all datasets.

    Args:
        skip_simulation: If True, skip discovery + simulation (use existing CSVs).
        skip_evaluation: If True, skip metric computation (return empty summary).

    Returns:
        DataFrame with summary metrics (one row per dataset).
    """
    summary_rows = []

    for ds in DATASETS:
        name = ds["name"]
        train_path = DATA_DIR / name / "train_preprocessed.csv"
        test_path  = DATA_DIR / name / "test_preprocessed.csv"
        results_dir = RESULTS_DIR / name

        log.info("=" * 60)
        log.info("Dataset: %s", name)

        # ── Discovery + simulation ─────────────────────────────────────
        if not skip_simulation:
            try:
                log.info("  [Discovery] extracting process behaviour …")
                observations = discover(train_path, column_override=ds.get("column_override"))
                log.info("  [Simulation] running %d seeds …", len(SEEDS))
                simulate_dataset(name, observations, test_path, results_dir)
            except Exception as exc:
                log.error("  Discovery/simulation failed for %s: %s", name, exc)
                summary_rows.append({"dataset": name,
                                     "NGD_mean": float("nan"), "NGD_std": float("nan"),
                                     "AEDD_mean": float("nan"), "AEDD_std": float("nan"),
                                     "CADD_mean": float("nan"), "CADD_std": float("nan"),
                                     "CEDD_mean": float("nan"), "CEDD_std": float("nan"),
                                     "REDD_mean": float("nan"), "REDD_std": float("nan"),
                                     "CTDD_mean": float("nan"), "CTDD_std": float("nan")})
                continue

        # ── Evaluation ────────────────────────────────────────────────
        if not skip_evaluation:
            log.info("  [Evaluation] computing distance metrics …")
            metrics = evaluate_dataset(name, test_path, results_dir)
        else:
            metrics = {}

        row = {"dataset": name, **metrics}
        summary_rows.append(row)

    summary = pd.DataFrame(summary_rows)

    if not skip_evaluation and not summary.empty:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = RESULTS_DIR / "summary.csv"
        summary.to_csv(summary_path, index=False)
        log.info("=" * 60)
        log.info("Summary written to %s", summary_path)
        print("\n" + summary.to_string(index=False))

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _nan_metrics() -> dict:
    """Return a dict of NaN values for all 6 distance metrics (mean + std)."""
    return {f"{m}_{s}": float("nan")
            for m in ["NGD", "AEDD", "CADD", "CEDD", "REDD", "CTDD"]
            for s in ["mean", "std"]}


# ─────────────────────────────────────────────────────────────────────────────
# ABLATION STUDY
# ─────────────────────────────────────────────────────────────────────────────

def _simulate_dataset_ablation(
    dataset_name: str,
    observations: dict,
    test_path: Path,
    results_dir: Path,
    override_kwargs: dict,
) -> bool:
    """Like simulate_dataset but applies per-parameter overrides to build_sim_config.

    override_kwargs must contain all keys from ABLATION_DEFAULTS so that every
    parameter is explicitly set (no reliance on build_sim_config defaults).
    """
    test_df = pd.read_csv(test_path)
    test_mapping = detect_columns(test_df)
    start_col = test_mapping.get("start_time", "start_timestamp")
    test_case_count = (test_df["case_id"].nunique() if "case_id" in test_df.columns
                       else test_df.iloc[:, 0].nunique())
    raw_starts = pd.to_datetime(test_df[start_col], format="mixed", utc=True)
    test_start = raw_starts.min()
    results_dir.mkdir(parents=True, exist_ok=True)

    warmup_override = override_kwargs.get("warmup_days")
    warmup_days = (
        derive_warmup_days(observations, test_start)
        if warmup_override is None
        else int(warmup_override)
    )
    log.info("    Warm-up: %d days", warmup_days)
    extra_kwargs = {k: v for k, v in override_kwargs.items() if k != "warmup_days"}

    all_ok = True
    for i, seed in enumerate(SEEDS):
        try:
            config = build_sim_config(
                observations, seed=seed, sim_cases=test_case_count,
                warmup_days=warmup_days,
                simulation_start=test_start.to_pydatetime(),
                **extra_kwargs,
            )
            result = run_simulation(observations, config)
            sim_log = build_simulation_log(result["cases"], config, log_end_iso=None)
            out = sim_log[["case_id", "activity", "start_time", "end_time", "resource"]]
            out.to_csv(results_dir / f"simulated_log_{i}.csv", index=False)
            log.info("    Seed %d (run %d): %d cases, %d events",
                     seed, i, out["case_id"].nunique(), len(out))
        except Exception as exc:
            log.error("    Seed %d (run %d) FAILED: %s", seed, i, exc)
            all_ok = False
    return all_ok


def run_ablation(
    skip_simulation: bool = False,
    skip_evaluation: bool = False,
) -> dict:
    """Run ablation study: vary one parameter at a time, all others at ABLATION_DEFAULTS.

    For each (param, value) combination in ABLATION_GRID, runs the full
    discover → simulate → evaluate pipeline for every dataset.

    Results layout:
        generated-results/as_is/OBSERVE/ablation/<param>=<value>/<dataset>/simulated_log_*.csv
        generated-results/as_is/OBSERVE/ablation/<param>=<value>/summary.csv
        generated-results/as_is/OBSERVE/ablation/ablation_summary.csv  ← combined table

    Returns:
        dict mapping condition label (e.g. "warmup_days=7") → summary DataFrame.
    """
    ablation_root = RESULTS_DIR / "ablation"
    all_summaries: dict[str, pd.DataFrame] = {}

    for param_name, values in ABLATION_GRID.items():
        for value in values:
            label = f"{param_name}={value}"
            cond_root = ablation_root / label
            log.info("=" * 60)
            log.info("ABLATION  %s", label)

            # Build a full override dict: defaults for every param, then set the
            # one being varied to the current value.
            override_kwargs = dict(ABLATION_DEFAULTS)
            override_kwargs[param_name] = value
            if param_name != "warmup_days":
                # Non-warm-up ablations should use the same dataset-specific
                # boundary state as the standard evaluation pipeline.
                override_kwargs["warmup_days"] = None

            summary_rows = []

            for ds in DATASETS:
                name = ds["name"]
                train_path = DATA_DIR / name / "train_preprocessed.csv"
                test_path  = DATA_DIR / name / "test_preprocessed.csv"
                results_dir = cond_root / name

                log.info("  Dataset: %s", name)

                if not skip_simulation:
                    try:
                        observations = discover(train_path, column_override=ds.get("column_override"))
                        log.info("  [Simulation] running %d seeds …", len(SEEDS))
                        _simulate_dataset_ablation(name, observations, test_path, results_dir, override_kwargs)
                    except Exception as exc:
                        log.error("  Failed %s / %s: %s", label, name, exc)
                        summary_rows.append({"dataset": name, **_nan_metrics()})
                        continue

                if not skip_evaluation:
                    log.info("  [Evaluation] computing distance metrics …")
                    metrics = evaluate_dataset(name, test_path, results_dir)
                else:
                    metrics = {}

                summary_rows.append({"dataset": name, **metrics})

            summary = pd.DataFrame(summary_rows)
            cond_root.mkdir(parents=True, exist_ok=True)
            summary.to_csv(cond_root / "summary.csv", index=False)
            log.info("  Condition summary → %s", cond_root / "summary.csv")
            all_summaries[label] = summary

    # Write a combined table with a leading "condition" column so the whole
    # ablation can be analysed in one CSV.
    if all_summaries:
        combined = pd.concat(
            [df.assign(condition=label)[["condition"] + list(df.columns)]
             for label, df in all_summaries.items()],
            ignore_index=True,
        )
        ablation_root.mkdir(parents=True, exist_ok=True)
        combined.to_csv(ablation_root / "ablation_summary.csv", index=False)
        log.info("=" * 60)
        log.info("Combined ablation summary → %s", ablation_root / "ablation_summary.csv")
        print("\n" + combined.to_string(index=False))

    return all_summaries


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Observations-based BPS evaluation pipeline")
    parser.add_argument("--skip-simulation", action="store_true",
                        help="Skip discovery + simulation; use existing simulated_log_*.csv files")
    parser.add_argument("--skip-evaluation", action="store_true",
                        help="Skip metric computation (only run discovery + simulation)")
    parser.add_argument("--ablation", action="store_true",
                        help="Run ablation study (vary resource_routing, "
                             "resource contention, residual waiting, and warmup one at a time). "
                             "Results go to generated-results/as_is/OBSERVE/ablation/")
    parser.add_argument("--dataset", metavar="NAME", nargs="+",
                            help="Run only the named dataset(s) (e.g. --dataset ACR LoanApp). "
                                 "Defaults to all datasets in DATASETS.")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "generated-results/as_is/OBSERVE")
    args = parser.parse_args()
    DATA_DIR = data_root(args.data_root) / "as_is/datasets"
    RESULTS_DIR = output_path(args.output, data_root(args.data_root))


    DATASETS[:] = [ds for ds in DATASETS if ds["name"] in {"cvs_pharmacy", "P2P", "BPIC_2012W", "BPIC_2017W"}]
    if args.dataset:
        valid_names = {ds["name"] for ds in DATASETS}
        unknown = set(args.dataset) - valid_names
        if unknown:
            parser.error(f"Unknown dataset(s): {', '.join(sorted(unknown))}. "
                            f"Available: {', '.join(sorted(valid_names))}")
        DATASETS[:] = [ds for ds in DATASETS if ds["name"] in set(args.dataset)]

    if args.ablation:
        run_ablation(
            skip_simulation=args.skip_simulation,
            skip_evaluation=args.skip_evaluation,
        )
    else:
        run_pipeline(
            skip_simulation=args.skip_simulation,
            skip_evaluation=args.skip_evaluation,
        )
