"""Score ten raw logs per method/dataset and reproduce the five-metric table."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.paths import data_root, output_path
import argparse
import datetime
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from log_distance_measures.config import EventLogIDs
from log_distance_measures.n_gram_distribution import n_gram_distribution_distance
from log_distance_measures.absolute_event_distribution import absolute_event_distribution_distance
from log_distance_measures.circadian_event_distribution import circadian_event_distribution_distance
from log_distance_measures.relative_event_distribution import relative_event_distribution_distance
from log_distance_measures.cycle_time_distribution import cycle_time_distribution_distance

# ── paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = data_root() / "as_is/datasets"
RESULTS_DIR = data_root() / "as_is/reference/OBSERVE"
BASELINE_ROOT = data_root() / "as_is/baselines"
OUT_DIR = REPO_ROOT / "generated-results/as_is/comparison"

# ── metric configuration (identical to the main evaluation pipeline) ───────────
CTDD_BIN_SIZE = datetime.timedelta(hours=1)
N_GRAM_N = 3

LOG_IDS = EventLogIDs(
    case="case_id",
    activity="activity",
    start_time="start_time",
    end_time="end_time",
    resource="resource",
)

# Display label / abbreviation for each metric, in table row order.
METRICS = ["NGD", "AED", "CED", "RED", "CTD"]

# ── table layout ────────────────────────────────────────────────────────────────
# Our method's display name.  Kept configurable so the LaTeX macro can differ
# from the plain-text label used in CSV / Markdown.
OBSERVE = "OBSERVE"
OBSERVE_LATEX = r"\tool{}"   # paper macro; prints as the method's name in the PDF

# Canonical dataset name (== evaluation/data/ dir) → short column label.
DATASETS = {
    "cvs_pharmacy":  "CVS",
    "P2P":           "P2P",
    "BPIC_2012W":    "BPI12W",
    "BPIC_2017W":    "BPI17W",
}

# Method display order (last row = our method).
METHOD_ORDER = ["Simod", "DGEN", "RIMS", "AgentSim", OBSERVE]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("reproduce_comparison")


# ── per-method log location + schema ────────────────────────────────────────────
# For each method:
#   root        : directory holding one sub-directory per dataset
#   dirs        : canonical dataset name -> method-specific sub-directory name
#                 (None where the method has no logs for that dataset)
#   logs_subdir : extra path appended inside the dataset dir to reach the logs
#   col_map     : {standard_field: source_column} to normalise the sim log

COLS_SIMOD = {"case_id": "case_id", "activity": "activity",
              "start_time": "start_time", "end_time": "end_time",
              "resource": "resource"}
COLS_DGEN = {"case_id": "caseid", "activity": "task",
             "start_time": "start_timestamp", "end_time": "end_timestamp",
             "resource": "role"}
COLS_RIMS = {"case_id": "caseid", "activity": "task",
             "start_time": "start:timestamp", "end_time": "time:timestamp",
             "resource": "role"}
COLS_AGENTSIM = {"case_id": "case_id", "activity": "activity_name",
                 "start_time": "start_timestamp", "end_time": "end_timestamp",
                 "resource": "resource"}
# OBSERVE sim logs share Simod's schema.
COLS_OBSERVE = dict(COLS_SIMOD)

METHODS = {method: {"root": data_root() / "as_is" / ("reference/OBSERVE" if method == OBSERVE else "baselines/" + method),
    "dirs": {ds: ds for ds in DATASETS}, "logs_subdir": "", "col_map": cols}
    for method, cols in [("Simod", COLS_SIMOD), ("DGEN", COLS_DGEN), ("RIMS", COLS_RIMS), ("AgentSim", COLS_AGENTSIM), (OBSERVE, COLS_OBSERVE)]}

# Canonical test-log (ground truth) column map — shared by every method.
TEST_LOG_COLS = {"case_id": "case_id", "activity": "activity_name",
                 "start_time": "start_timestamp", "end_time": "end_timestamp",
                 "resource": "resource"}


# ── log loading + metric computation ──────────────────────────────────────────

for method, config in METHODS.items():
    config["root"] = data_root() / "as_is" / ("reference/OBSERVE" if method == OBSERVE else "baselines/" + method)
    config["dirs"] = {dataset: dataset for dataset in DATASETS}
    config["logs_subdir"] = ""

def _load_and_standardise(path: Path, col_map: dict) -> pd.DataFrame:
    """Load a CSV and return a df with exactly the LOG_IDS columns.

    Columns are renamed per ``col_map``; start/end times are parsed to tz-aware
    UTC datetimes; id/activity/resource are cast to str for consistent hashing.
    """
    raw = pd.read_csv(path)
    missing = [src for src in col_map.values() if src not in raw.columns]
    if missing:
        raise KeyError(f"{path}: missing source columns {missing}. "
                       f"Available: {list(raw.columns)}")

    df = pd.DataFrame({std: raw[src] for std, src in col_map.items()})
    for col in ("start_time", "end_time"):
        df[col] = pd.to_datetime(df[col], format="mixed", utc=True)
    for col in ("case_id", "activity", "resource"):
        df[col] = df[col].astype(str)
    return df


def _compute_metrics(test_log: pd.DataFrame, sim_log: pd.DataFrame) -> dict:
    """Compute the five comparison metrics for one simulated log."""
    return {
        "NGD": n_gram_distribution_distance(
            test_log, LOG_IDS, sim_log, LOG_IDS, n=N_GRAM_N),
        "AED": absolute_event_distribution_distance(
            test_log, LOG_IDS, sim_log, LOG_IDS),
        "CED": circadian_event_distribution_distance(
            test_log, LOG_IDS, sim_log, LOG_IDS),
        "RED": relative_event_distribution_distance(
            test_log, LOG_IDS, sim_log, LOG_IDS),
        "CTD": cycle_time_distribution_distance(
            test_log, LOG_IDS, sim_log, LOG_IDS, bin_size=CTDD_BIN_SIZE),
    }


def _find_sim_logs(method: str, dataset: str) -> list[Path]:
    """Return the sorted simulated_log_*.csv paths for one (method, dataset)."""
    cfg = METHODS[method]
    ds_dir = cfg["dirs"].get(dataset)
    if ds_dir is None:
        return []
    logs_dir = cfg["root"] / ds_dir
    if cfg["logs_subdir"]:
        logs_dir = logs_dir / cfg["logs_subdir"]
    return sorted(logs_dir.glob("simulated_log_*.csv"),
                  key=lambda p: int(p.stem.rsplit("_", 1)[-1]))


# ── core: compute all (method, dataset) cells from raw logs ───────────────────

def compute_table(datasets: dict[str, str],
                  methods: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score every (method, dataset) from its raw logs against the shared test log.

    Returns (long_df, per_run_df).  long_df has one row per (metric, method,
    dataset) with mean + std over the available runs.
    """
    test_cache: dict[str, pd.DataFrame] = {}
    for ds in datasets:
        tp = DATA_DIR / ds / "test_preprocessed.csv"
        if not tp.exists():
            raise FileNotFoundError(f"Missing test log: {ds}")
        test_cache[ds] = _load_and_standardise(tp, TEST_LOG_COLS)

    long_rows: list[dict] = []
    per_run_rows: list[dict] = []

    for method in methods:
        for ds, label in datasets.items():
            test_log = test_cache.get(ds)
            if test_log is None:
                continue

            sim_paths = _find_sim_logs(method, ds)
            if len(sim_paths) != 10:
                raise ValueError(f"{method}/{ds}: expected ten logs, found {len(sim_paths)}")
            if not sim_paths:
                log.warning("[%s/%s] no simulated logs found — cell = n/a",
                            method, label)
                continue

            runs: list[dict] = []
            for p in sim_paths:
                try:
                    sim_log = _load_and_standardise(p, METHODS[method]["col_map"])
                    m = _compute_metrics(test_log, sim_log)
                    runs.append(m)
                    per_run_rows.append({"method": method, "dataset": label,
                                         "run": int(p.stem.rsplit("_", 1)[-1]), **m})
                except Exception as exc:
                    log.error("[%s/%s] %s failed: %s", method, label, p.name, exc)

            if len(runs) != 10 or any(not np.isfinite(v) for r in runs for v in r.values()):
                raise ValueError(f"{method}/{ds}: ten successfully scored finite runs required")
            for metric in METRICS:
                vals = [r[metric] for r in runs if r[metric] == r[metric]]  # drop NaN
                long_rows.append({
                    "metric": metric, "method": method, "dataset": label,
                    "mean": float(np.mean(vals)) if vals else float("nan"),
                    "std": float(np.std(vals)) if vals else float("nan"),
                })
            log.info("[%s/%s] scored over %d runs", method, label, len(runs))

    long_df = pd.DataFrame(long_rows, columns=["metric", "method", "dataset", "mean", "std"])
    per_run_df = pd.DataFrame(per_run_rows)
    return long_df, per_run_df


# ── ranks ──────────────────────────────────────────────────────────────────────

def add_ranks(long_df: pd.DataFrame, datasets: list[str],
              methods: list[str]) -> pd.DataFrame:
    """Attach a per-(metric,dataset) rank and a per-(metric,method) average rank.

    Rank 1 = best (lowest mean).  Ties receive the average rank.  The returned
    frame gains a ``rank`` column (per cell) and an ``avg_rank`` column (the mean
    cell-rank of that method across datasets, repeated for every row of the
    (metric, method) group).
    """
    df = long_df.copy()
    df["rank"] = np.nan
    for metric in df["metric"].unique():
        for ds in datasets:
            mask = (df["metric"] == metric) & (df["dataset"] == ds)
            sub = df.loc[mask]
            if sub.empty:
                continue
            df.loc[mask, "rank"] = sub["mean"].rank(method="average")

    avg = (df.dropna(subset=["rank"])
             .groupby(["metric", "method"])["rank"].mean()
             .rename("avg_rank").reset_index())
    df = df.merge(avg, on=["metric", "method"], how="left")
    return df


# ── output: wide grid, LaTeX, Markdown ────────────────────────────────────────

def _cell(mean: float, std: float) -> str:
    if mean != mean:  # NaN
        return "n/a"
    return f"{mean:.2f} ({std:.2f})"


def build_wide(df: pd.DataFrame, datasets: list[str],
               methods: list[str]) -> pd.DataFrame:
    """Per-cell 'mean (std)' grid with an Avg-rank column, indexed (Metric, Method)."""
    rows, idx = [], []
    for metric in METRICS:
        for method in methods:
            row = {}
            for ds in datasets:
                sel = df[(df.metric == metric) & (df.method == method) & (df.dataset == ds)]
                if sel.empty:
                    row[ds] = "n/a"
                else:
                    row[ds] = _cell(sel.iloc[0]["mean"], sel.iloc[0]["std"])
            sel_any = df[(df.metric == metric) & (df.method == method)]
            row["Rank"] = (f"{sel_any.iloc[0]['avg_rank']:.2f}"
                           if not sel_any.empty and sel_any.iloc[0]["avg_rank"] == sel_any.iloc[0]["avg_rank"]
                           else "n/a")
            rows.append(row)
            idx.append((metric, method))
    midx = pd.MultiIndex.from_tuples(idx, names=["Metric", "Method"])
    return pd.DataFrame(rows, index=midx, columns=datasets + ["Rank"])


def _best_means(df: pd.DataFrame, datasets: list[str]) -> dict:
    """(metric, dataset) -> lowest mean; and (metric,) -> lowest avg_rank."""
    best = {}
    for metric in METRICS:
        for ds in datasets:
            sel = df[(df.metric == metric) & (df.dataset == ds)]["mean"].dropna()
            if not sel.empty:
                best[(metric, ds)] = sel.min()
        ar = df[df.metric == metric].drop_duplicates(["method"])["avg_rank"].dropna()
        if not ar.empty:
            best[(metric, "__rank__")] = ar.min()
    return best


def build_latex(df: pd.DataFrame, datasets: list[str], labels: list[str],
                methods: list[str]) -> str:
    best = _best_means(df, labels)

    def method_label(m):
        return OBSERVE_LATEX if m == OBSERVE else m

    def fmt(metric, method, ds):
        sel = df[(df.metric == metric) & (df.method == method) & (df.dataset == ds)]
        if sel.empty or sel.iloc[0]["mean"] != sel.iloc[0]["mean"]:
            return "n/a"
        mean, std = sel.iloc[0]["mean"], sel.iloc[0]["std"]
        s = f"{mean:.2f} ({std:.2f})"
        b = best.get((metric, ds))
        return r"\textbf{" + s + "}" if (b is not None and abs(mean - b) < 1e-4) else s

    def fmt_rank(metric, method):
        sel = df[(df.metric == metric) & (df.method == method)]
        if sel.empty or sel.iloc[0]["avg_rank"] != sel.iloc[0]["avg_rank"]:
            return "n/a"
        r = sel.iloc[0]["avg_rank"]
        b = best.get((metric, "__rank__"))
        s = f"{r:.2f}"
        return r"\textbf{" + s + "}" if (b is not None and abs(r - b) < 1e-4) else s

    col_spec = "ll" + "r" * (len(labels) + 1)
    L = [r"% Requires: \usepackage{booktabs}  \usepackage{multirow}",
         r"% \tool{} is the paper macro for our method (OBSERVE).",
         r"\begin{table}[t]", r"\centering", r"\small",
         r"\caption{Comparison of simulation quality (distance metrics; lower is "
         r"better). All values recomputed from raw simulated logs against a shared "
         r"test log.}",
         r"\label{tab:comparison}",
         r"\setlength{\tabcolsep}{3pt}",
         r"\begin{tabular}{" + col_spec + "}", r"\toprule",
         " & ".join([r"\textbf{Metric}", r"\textbf{Method}"]
                    + [r"\textbf{" + l + "}" for l in labels]
                    + [r"\textbf{Rank}"]) + r" \\", r"\midrule"]

    for mi, metric in enumerate(METRICS):
        if mi > 0:
            L.append(r"\midrule")
        for ri, method in enumerate(methods):
            parts = [r"\multirow{" + str(len(methods)) + r"}{*}{" + metric + "}" if ri == 0 else "",
                     method_label(method)]
            parts += [fmt(metric, method, ds) for ds in datasets]
            parts.append(fmt_rank(metric, method))
            L.append(" & ".join(parts) + r" \\")

    L += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(L)


def build_markdown(wide: pd.DataFrame, datasets: list[str]) -> str:
    cols = datasets + ["Rank"]
    out = ["| Metric | Method | " + " | ".join(cols) + " |",
           "|---|---|" + "|".join(["---"] * len(cols)) + "|"]
    for (metric, method), row in wide.iterrows():
        out.append(f"| {metric} | {method} | " + " | ".join(str(row[c]) for c in cols) + " |")
    return "\n".join(out)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", nargs="+", default=list(DATASETS.keys()),
                    help=f"Canonical dataset names. Default: {list(DATASETS.keys())}")
    ap.add_argument("--methods", nargs="+", default=METHOD_ORDER,
                    help=f"Methods to include. Default: {METHOD_ORDER}")
    ap.add_argument("--output", "--out", dest="out", default=str(REPO_ROOT / "generated-results/as_is/comparison"),
                    help=f"Output directory. Default: {OUT_DIR}")
    ap.add_argument("--data-root", type=Path)
    ap.add_argument("--observe-logs", type=Path, help="Regenerated OBSERVE logs root")
    args = ap.parse_args()
    global DATA_DIR
    inputs = data_root(args.data_root)
    DATA_DIR = inputs / "as_is/datasets"
    for method, cfg in METHODS.items():
        cfg["root"] = (args.observe_logs if method == OBSERVE and args.observe_logs else
                       inputs / "as_is" / ("reference/OBSERVE" if method == OBSERVE else "baselines/" + method))
        cfg["dirs"] = {ds: ds for ds in DATASETS}
        cfg["logs_subdir"] = ""


    if set(args.datasets) - DATASETS.keys() or set(args.methods) - METHODS.keys():
        ap.error("Unknown dataset or method")
    datasets = {ds: DATASETS.get(ds, ds) for ds in args.datasets}
    labels = list(datasets.values())
    methods = args.methods
    out_dir = output_path(args.out, inputs)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("Computing %d methods x %d datasets from raw simulated logs …",
             len(methods), len(datasets))
    long_df, per_run_df = compute_table(datasets, methods)
    long_df = add_ranks(long_df, labels, methods)

    wide = build_wide(long_df, labels, methods)
    latex = build_latex(long_df, labels, labels, methods)
    markdown = build_markdown(wide, labels)

    (out_dir / "comparison_long.csv").write_text(
        long_df.to_csv(index=False))
    wide.to_csv(out_dir / "comparison_wide.csv")
    per_run_df.to_csv(out_dir / "comparison_per_run.csv", index=False)
    (out_dir / "comparison_table.tex").write_text(latex)
    (out_dir / "comparison_table.md").write_text(markdown + "\n")

    log.info("=" * 70)
    log.info("Wrote outputs to %s", out_dir)
    for f in ["comparison_long.csv", "comparison_wide.csv", "comparison_per_run.csv",
              "comparison_table.tex", "comparison_table.md"]:
        log.info("  %s", f)

    # Console summary.
    print("\n=== Per-cell means (mean (std)) ===")
    print(wide.to_string())
    print("\n=== Average rank per metric (lower = better) ===")
    rank_tbl = (long_df.drop_duplicates(["metric", "method"])
                .pivot(index="metric", columns="method", values="avg_rank")
                .reindex(index=METRICS, columns=methods).round(2))
    print(rank_tbl.to_string())


if __name__ == "__main__":
    main()
