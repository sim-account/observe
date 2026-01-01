"""Generate the four-opportunity paper figure and numerical summaries from saved trials."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evaluation.paths import data_root, output_path
import argparse
import hashlib
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
import numpy as np

from summarize import mean_interval, summarize

HERE = Path(__file__).resolve().parent

SHOW_LEGEND = False
ID_TOP_PT = 12
ID_H_PT = 9
PLOT_LABELS = [
    ('O1: Slow repetitions', 'Duration factor', '#7b4f9d'),
    ('O2: Slow clerk', 'Duration factor', '#2a6b9e'),
    ('O3: Skewed routing', 'RiskAnalyst-1 share', '#1d7a6b'),
    ('O4: Channel rework', 'Repetition probability', '#b86a26'),
]
SELECTION = [
    ("O1", "O1", "B", "Cond. time\n(rework cases)", "#805098", ["2.00", "2.50", "3.00"]),
    ("O2", "O2", "A", "Cond. time\n(resource)", "#28679C", ["1.50", "2.00", "3.00"]),
    ("O3", "O3", "B", "Res. routing", "#18766F", ["0.60", "0.75", "0.90"]),
    ("O4", "O4", "B", "Case attr.", "#B66923", ["0.35", "0.50", "0.65"]),
]


def prepare(path):
    trials = pd.read_csv(path / "trials.csv")
    settings = json.loads((path / "study_settings.json").read_text())
    if trials.error.notna().any() or trials.duplicated(["observation_type", "context", "strength", "seed"]).any():
        raise ValueError("Trial errors or duplicate identities require inspection")
    trials["model_loss_hours"] = trials.injected_cycle_hours - trials.baseline_cycle_hours
    rows = []
    for label, observation, context, _, _, levels in SELECTION:
        subset = trials[trials.observation_type.eq(observation) & trials.context.eq(context)]
        if set(subset.strength) != {float(level) for level in levels}:
            raise ValueError(f"Missing or unexpected strengths for {label}")
        for strength, group in subset.groupby("strength"):
            if len(group) != 10 or set(group.seed) != set(settings["seeds"]):
                raise ValueError(f"Expected ten complete log pairs for {label}, {strength}")
            executed = group[group.target_detected & group.recommendation_executed].dropna(
                subset=['baseline_cycle_hours', 'injected_cycle_hours',
                        'cycle_time_reduction_after_recommendation_hours'])
            remaining = (executed.injected_cycle_hours - executed.baseline_cycle_hours
                         - executed.cycle_time_reduction_after_recommendation_hours)
            rows.append({"opportunity": label, "observation_type": observation, "context": context,
                         "strength": strength, "source": "opportunities", **summarize(group),
                         **mean_interval(remaining, 'remaining_delay')})
    return pd.DataFrame(rows)


def bars(ax, x, data, prefix, **kwargs):
    mean = data[f"{prefix}_mean_h"].to_numpy()
    lower = data[f"{prefix}_ci95_lower_h"].to_numpy()
    upper = data[f"{prefix}_ci95_upper_h"].to_numpy()
    ax.bar(x, mean, yerr=np.vstack([mean - lower, upper - mean]),
           capsize=1.5, error_kw=dict(lw=0.7), **kwargs)


@plt.rc_context({
    "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
    "pdf.fonttype": 42,
})
def figure(data, output):
    """Paper layout, populated from paired detected/executed trials."""
    fig, axes = plt.subplots(1, 4, figsize=(4.8, 1.1))
    width = 0.38
    for ax, selection, labels in zip(axes, SELECTION, PLOT_LABELS):
        label, _, _, _, _, levels = selection
        title, xlabel, color = labels
        subset = data[data.opportunity.eq(label)].sort_values("strength")
        if len(subset) != 3 or not subset.n_trials.eq(10).all():
            raise ValueError(f"Expected three complete ten-seed levels for {label}")
        x = np.arange(len(levels))
        bars(ax, x - width / 2, subset, "injection_increase", width=width,
             color=color, alpha=0.35, edgecolor=color, lw=0.6)
        bars(ax, x + width / 2, subset, "remaining_delay", width=width,
             color=color, edgecolor=color, lw=0.6)
        ax.axhline(0, color="grey", lw=0.6, ls="--")
        ax.set_xticks(x, levels)
        ax.tick_params(axis="both", length=2, pad=1.5)
        ax.set_title(title, pad=2, fontsize=7)
        ax.set_xlabel(xlabel, labelpad=ID_H_PT, fontsize=6)
        ax.set_ylim(bottom=0)
        for xi, n, total in zip(x, subset.n_target_detected, subset.n_trials):
            ax.annotate(f"{100 * n / total:.0f}%", (xi, 0), xycoords=("data", "axes fraction"),
                        xytext=(0, -ID_TOP_PT), textcoords="offset points",
                        ha="center", va="top", fontsize=6,
                        color="0.45" if n == total else "black",
                        fontweight="normal" if n == total else "bold")
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, steps=[1, 2, 5, 10], integer=True))
        ax.spines[["top", "right"]].set_visible(False)
        # Mark a significant reduction when the paired injected-minus-revised
        # benefit interval lies above zero. Place each star over both bar CIs.
        significant = subset.benefit_ci95_lower_h.gt(0)
        inj_upper = subset.injection_increase_ci95_upper_h.to_numpy()
        rem_upper = subset.remaining_delay_ci95_upper_h.to_numpy()
        for xi, ok, upper in zip(x, significant, np.maximum(inj_upper, rem_upper)):
            if ok:
                ax.annotate("*", (xi, upper), xytext=(0, -1),
                            textcoords="offset points", ha="center", fontsize=9)
        ax.set_ylim(top=ax.get_ylim()[1] * 1.12)
    axes[0].set_ylabel("Delay vs.\ncontrol [h]", labelpad=2)
    axes[0].annotate("Identification\nrate:", (0, 0), xycoords="axes fraction",
                     xytext=(-3, -ID_TOP_PT - 3), textcoords="offset points",
                     ha="right", va="center", fontsize=6, color="0.45")
    if SHOW_LEGEND:
        handles = [Patch(facecolor="grey", alpha=0.35, edgecolor="grey", label="Injection impact"),
                   Patch(facecolor="grey", edgecolor="grey", label="Remaining delay")]
        fig.legend(handles=handles, loc="upper center", ncol=2, frameon=False,
                   bbox_to_anchor=(0.5, 1.07), handlelength=1.2, columnspacing=1.2)
    fig.tight_layout(pad=0.2, w_pad=0.5)
    name = "recommendation_detection_effect"
    fig.savefig(output / f"{name}.pdf", bbox_inches="tight", pad_inches=0.01, metadata={"Creator": "OBSERVE", "CreationDate": None, "ModDate": None})
    fig.savefig(output / f"{name}.png", dpi=200, bbox_inches="tight", pad_inches=0.01)
    plt.close(fig)


def results_overview(data):
    """Plotted delays and additional recovery diagnostics, one row per setting."""
    overview = data[["opportunity", "observation_type", "context", "strength", "source"]].copy()
    overview["log_pairs"] = data.n_trials
    overview["identified_log_pairs"] = data.n_target_detected
    overview["identification_rate_pct"] = 100 * data.n_target_detected / data.n_trials
    overview["evaluated_log_pairs"] = data.n_effect
    for column, source in {
        "injection_impact_mean_h": "injection_increase_mean_h",
        "injection_impact_ci95_lower_h": "injection_increase_ci95_lower_h",
        "injection_impact_ci95_upper_h": "injection_increase_ci95_upper_h",
        "remaining_delay_mean_h": "remaining_delay_mean_h",
        "remaining_delay_ci95_lower_h": "remaining_delay_ci95_lower_h",
        "remaining_delay_ci95_upper_h": "remaining_delay_ci95_upper_h",
        "recommendation_impact_mean_h": "benefit_mean_h",
        "recommendation_impact_ci95_lower_h": "benefit_ci95_lower_h",
        "recommendation_impact_ci95_upper_h": "benefit_ci95_upper_h",
    }.items():
        overview[column] = data[source]
    denominator = data.injection_increase_mean_h.where(data.injection_increase_mean_h > .1)
    overview["recovery_pct"] = 100 * data.benefit_mean_h / denominator
    overview["recovery_ci95_lower_pct"] = 100 * data.benefit_ci95_lower_h / denominator
    overview["recovery_ci95_upper_pct"] = 100 * data.benefit_ci95_upper_h / denominator
    overview["estimated_improvement"] = data.benefit_ci95_lower_h.gt(0)
    return overview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--results", type=Path, help="Regenerated opportunities directory")
    parser.add_argument("--output", type=Path, default=HERE.parents[1] / "generated-results/opportunities-analysis")
    args = parser.parse_args()
    inputs = data_root(args.data_root)
    results = args.results or inputs / "opportunities"
    output = output_path(args.output, inputs)
    data = prepare(results)
    output.mkdir(parents=True, exist_ok=True)
    figure(data, output)
    data.to_csv(output / "plotted_results.csv", index=False)
    overview = results_overview(data)
    overview.to_csv(output / "results.csv", index=False)
    metadata = {"bundle_version": "v1", "source_sha256": hashlib.sha256((results / "trials.csv").read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "layout": "remaining-delay", "identified": int(data.n_target_detected.sum()), "planned": int(data.n_trials.sum()),
        "positive_benefit_intervals": int(overview.estimated_improvement.sum()),
        "intervals": "Paired Student-t 95%, identified and executed pairs; recovery diagnostic scales benefit interval by fixed mean injection impact."}
    (output / "plot_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))
if __name__ == "__main__":
    main()
