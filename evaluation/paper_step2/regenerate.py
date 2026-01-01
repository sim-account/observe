"""Regenerate the exact four-opportunity cohort: 120 planned trials and ten shared controls.

Generate source logs, discover models and observations, select actual endpoint
recommendations before simulation, and export paired as-is/to-be evidence.
Run from the repository root: python evaluation/paper_step2/regenerate.py
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import t

# ── Backend imports ───────────────────────────────────────────────────────────

from simulation_helpers import (ROOT, TABLES, METRICS, parse_csv, write_json,
                       simulate_and_save, extract_process_behaviour,
                       extract_process_observations, serialize_model,
                       _collect_recommendations, _apply_modifications)
from recommendation_helpers import final_recommendations
from replay_helpers import check_replay
from src.case_attribute_extractor import extract_case_attribute_observations

# ── Generator imports ─────────────────────────────────────────────────────────

from generate_log import (generate, make_injection, measure_behavior,
                                   CONTEXT_TARGETS, InjectionSpec, make_control)
from study_measurements import source_cycle_hours, verify_injection

# ── Study configuration ───────────────────────────────────────────────────────

# Observation types mapped to their injection names and eligible contexts.
# Strengths are observation-specific (multipliers for duration/routing, probabilities for rework/routing).
OBSERVATION_CONFIGS: dict[str, dict] = {'O1': {'injection': 'O1',
        'contexts': ['B'],
        'strengths': [2.0, 2.5, 3.0],
        'detection_table': 'conditional_time_rules',
        'detection_condition_type': 'rework_in_case',
        'recommendation_observation_type': 'conditional_time',
        'goal_b': True},
 'O2': {'injection': 'O2',
        'contexts': ['A'],
        'strengths': [1.5, 2.0, 3.0],
        'detection_table': 'conditional_time_rules',
        'detection_condition_type': 'resource_speed_gap',
        'recommendation_observation_type': 'conditional_time',
        'goal_b': True},
 'O3': {'injection': 'O3',
        'contexts': ['B'],
        'strengths': [0.6, 0.75, 0.9],
        'detection_table': 'resource_routing_rules',
        'detection_condition_type': 'predecessor',
        'recommendation_observation_type': 'resource_routing',
        'goal_b': True},
 'O4': {'injection': 'O4',
        'contexts': ['B'],
        'strengths': [0.35, 0.5, 0.65],
        'detection_observation_type': 'case_attribute',
        'detection_sub_type': 'rework_effect',
        'recommendation_observation_type': 'case_attribute',
        'goal_b': True,
        'needs_case_attrs': True}}

# ── Helpers ───────────────────────────────────────────────────────────────────

# Exact paper cohort: four opportunities, one context each.
PAPER_CONFIGS = {key: {**OBSERVATION_CONFIGS[key], "contexts": [context]}
    for key, context in [("O1", "B"), ("O2", "A"), ("O3", "B"), ("O4", "B")]}

def _log_to_traces(events: pd.DataFrame) -> pd.DataFrame:
    """Convert generated event log to the format expected by the miner.

    generate() returns: case_id (str), activity, start_time (Timestamp tz-aware),
    end_time (Timestamp tz-aware), resource.
    parse_event_log / extract_process_observations expect the same columns plus a
    pre-computed duration_minutes column.  The parser computes it internally
    from start_time / end_time, so we just need to ensure column names match.
    """
    return events.copy()


def _save_events(events: pd.DataFrame, directory: Path) -> Path:
    path = directory / "events.csv"
    events.to_csv(path, index=False)
    return path


def _discover_model(events: pd.DataFrame) -> tuple[dict, dict]:
    """Run the full discovery pipeline.  Returns (combined_model, observations)."""
    traces, _ = parse_csv(_tmp_csv(events))
    behaviour = extract_process_behaviour(traces)
    observations = extract_process_observations(traces, strict=True)
    combined = {**behaviour, **{key: observations[key] for key in TABLES}}
    return combined, observations


_tmp_path: Path | None = None


def _tmp_csv(events: pd.DataFrame) -> Path:
    """Write events to a temp file (overwritten each call) and return its path."""
    global _tmp_path
    import tempfile, atexit
    if _tmp_path is None:
        fd, name = tempfile.mkstemp(suffix=".csv", prefix="loan_study_")
        import os; os.close(fd)
        _tmp_path = Path(name)
        atexit.register(lambda: _tmp_path.unlink(missing_ok=True))
    events.to_csv(_tmp_path, index=False)
    return _tmp_path


def _discover_model_from_path(csv_path: Path) -> tuple[dict, dict]:
    traces, _ = parse_csv(csv_path)
    behaviour = extract_process_behaviour(traces)
    observations = extract_process_observations(traces, strict=True)
    combined = {**behaviour, **{key: observations[key] for key in TABLES}}
    return combined, observations


def _extract_case_observations(events_path: Path, case_attrs: pd.DataFrame) -> list[dict]:
    """Run the case attribute extractor over a saved event log + case attributes."""
    traces, _ = parse_csv(events_path)
    return extract_case_attribute_observations(traces, case_attrs, case_id_col="case_id")


def _match_observation(observations: dict, cfg: dict, injection_name: str,
                       context: str) -> list[dict]:
    """Match the adverse target observation for one study opportunity."""
    if cfg.get("needs_case_attrs"):
        return []
    table = observations.get(cfg["detection_table"])
    if table is None or table.empty:
        return []
    target = CONTEXT_TARGETS[injection_name][context]
    if injection_name == "O1":
        mask = (table["activity"].eq(target)
                & table["condition_type"].eq("rework_in_case") & table["ratio"].gt(1))
    elif injection_name == "O2":
        activity, resource = target
        mask = (table["activity"].eq(activity)
                & table["condition_type"].eq("resource_speed_gap")
                & table["condition_value"].str.startswith(resource + " vs ") & table["ratio"].gt(1))
    elif injection_name == "O3":
        activity, predecessor, resource = target
        mask = (table["activity"].eq(activity) & table["predecessor"].eq(predecessor)
                & table["resource"].eq(resource) & table["ratio"].gt(1))
    else:
        return []
    return table.loc[mask].to_dict(orient="records")


def _match_case_attr_observation(ca_observations: list[dict], cfg: dict,
                               injection_name: str, context: str) -> list[dict]:
    """Match the activity-specific adverse case-attribute observation."""
    activity, value = CONTEXT_TARGETS[injection_name][context]
    attribute = "channel" if value == "branch" else "loan_amount"
    return [observation for observation in ca_observations
            if observation.get("sub_type") == cfg["detection_sub_type"]
            and observation.get("attribute") == attribute
            and observation.get("attribute_value") == value
            and float(observation.get("ratio", 1)) > 1
            and observation.get("activity") == activity]


def _effective_signature(model):
    keys = ("prefix_tree", "resource_assignments_df", "activity_durations_df", "arrival_parameters",
            "resource_capacity_dict", "parallel_pairs_df", "terminal_probs", "residual_wait_parameters_df")
    selected = {k: model[k] for k in keys if k in model}
    rw = model.get("rework_parameters_df")
    if rw is not None and not rw.empty:
        selected["rework_parameters_df"] = rw[["activity", "max_loops"]]
    return serialize_model(selected)


def _run_what_if(combined: dict, matched: list[dict], recommendation_observation_type: str,
                 run_args: SimpleNamespace, trial_dir: Path, injected_kpis: dict,
                 ca_observations: list[dict] = None
                 ) -> tuple[list[dict], list[dict]]:
    """Generate and execute what-if scenarios for the matched observations."""
    recommendations = final_recommendations(combined, ca_observations=ca_observations or [])
    texts = {entry["rule_text"] for entry in matched if "rule_text" in entry}

    # DEBUG: Save all recommendations and matching state
    write_json(trial_dir / "debug_all_recommendations.json", recommendations)

    selected = [rec for rec in recommendations
                if rec["observation_type"] == recommendation_observation_type and rec.get("rule_text") in texts]

    # Choose before seeing simulated outcomes; preserve all candidates on disk.
    selected = sorted(selected, key=lambda r: (-float(r.get("impact", 0)), r.get("stable_key", r.get("rule_text", ""))))
    outcomes = []
    chosen = next((i for i, r in enumerate(selected, 1) if r.get("suggestion")), None)
    for idx, rec in enumerate(selected, 1):
        if rec.get("suggestion") and idx != chosen:
            outcomes.append({"index": idx, "status": "not_selected_by_prespecified_policy"})
            continue
        modification = copy.deepcopy(rec.get("suggestion"))
        mode = "automatic"
        if rec.get("requires_input"):
            if rec.get("input_config", {}).get("default") is not None:
                modification = copy.deepcopy(rec["suggestion"])
                modification["value"] = rec["input_config"]["default"]
                mode = "analyst_input_using_tool_default"
            else:
                outcomes.append({"index": idx, "status": "requires_input_no_default"})
                continue
        if not modification:
            outcomes.append({"index": idx, "status": "no_modification",
                             "reason": rec.get("unsupported_reason", "no_actionable_change")})
            continue

        what_dir = trial_dir / f"what_if_{idx}"
        try:
            what_if = _apply_modifications(combined, [modification])
        except ValueError as exc:
            outcomes.append({"index": idx, "status": "unsupported_modification", "reason": str(exc)})
            continue
        if _effective_signature(what_if) == _effective_signature(combined):
            outcomes.append({"index": idx, "status": "no_effective_change"})
            continue
        _, what_kpis = simulate_and_save(what_if, run_args, what_dir)
        write_json(what_dir / "modification.json", {"mode": mode, "modification": modification})
        check_replay(what_dir)

        reduction = injected_kpis["avg_cycle_time_hours"] - what_kpis["avg_cycle_time_hours"]
        outcomes.append({
            "index": idx, "status": "executed", "mode": mode,
            "what_if_cycle_hours": what_kpis["avg_cycle_time_hours"],
            "cycle_time_reduction_after_recommendation_hours": reduction,
            "what_if_rework_pct": what_kpis.get("rework_rate_pct"),
        })
    return selected, outcomes


# ── Aggregate writers ─────────────────────────────────────────────────────────

def _write_aggregates(rows: list[dict], output: Path) -> None:
    frame = pd.DataFrame(rows)
    frame.to_csv(output / "trials.csv", index=False)

    groups = []
    for keys, group in frame.groupby(
            ["observation_type", "context", "strength"], dropna=False):
        row = {
            "observation_type": keys[0], "context": keys[1], "strength": keys[2],
            "trials": len(group),
        }
        for flag in ("target_detected", "target_detected_in_baseline",
                     "recommendation_generated", "cycle_loss_positive",
                     "cycle_improvement_positive", "injection_verified", "observation_detected",
                     "recommendation_returned", "recommendation_executed", "validated_recovery_positive"):
            row[flag + "_count"] = int(group[flag].fillna(False).sum())
        row["detection_rate"] = round(row["target_detected_count"] / len(group), 3)
        row["baseline_target_rate"] = round(row["target_detected_in_baseline_count"] / len(group), 3)

        for col in ("cycle_time_increase_after_injection_hours",
                    "cycle_time_reduction_after_recommendation_hours",
                    "source_cycle_loss_hours"):
            vals = group[col].dropna().to_numpy(dtype=float)
            row[col + "_n"] = len(vals)
            mean = float(np.mean(vals)) if len(vals) else None
            margin = (float(t.ppf(.975, len(vals) - 1) * np.std(vals, ddof=1) / np.sqrt(len(vals)))
                      if len(vals) > 1 else None)
            row[col + "_mean"] = mean
            row[col + "_ci_low"] = mean - margin if margin is not None else None
            row[col + "_ci_high"] = mean + margin if margin is not None else None

        # Compare like with like: recovery ratios only use valid paired trials.
        eligible = group[group["target_detected"] & group["goal_b_applicable"]
                         & group["source_cycle_loss_hours"].gt(0)
                         & group["cycle_time_increase_after_injection_hours"].gt(0)
                         & group["cycle_time_reduction_after_recommendation_hours"].notna()]
        ratios = (100 * eligible["cycle_time_reduction_after_recommendation_hours"]
                  / eligible["cycle_time_increase_after_injection_hours"])
        row["recovery_pct"] = float(ratios.mean()) if len(ratios) else None
        row["recovery_n"] = len(ratios)
        row["validated_recovery_rate"] = row["validated_recovery_positive_count"] / len(group)
        groups.append(row)

    pd.DataFrame(groups).to_csv(output / "conditions.csv", index=False)

    # Per-observation-type summary
    summary_rows = []
    for observation_type, group in frame.groupby("observation_type"):
        cfg = OBSERVATION_CONFIGS[observation_type]
        counts = {flag: int(group[flag].fillna(False).sum()) for flag in (
            "target_detected", "target_detected_in_baseline",
            "recommendation_generated", "cycle_loss_positive",
            "cycle_improvement_positive", "injection_verified", "observation_detected",
            "recommendation_executed", "validated_recovery_positive")}
        summary_rows.append({
            "observation_type": observation_type,
            "trials": len(group),
            "goal_b_applicable": cfg["goal_b"],
            "counts": counts,
            "errors": int(group["error"].notna().sum()),
        })

    write_json(output / "summary.json", {
        "observation_types": summary_rows,
        "total_trials": len(frame),
        "kpi_unit": "hours",
        "scope": ("Loan-application generator; inject→detect→recommend→what-if pipeline; "
                  "only listed observation types were run; paired controls and verified injections; "
                  "cycle recovery applies only where goal_b_applicable is true"),
    })


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "generated-results/opportunities")
    parser.add_argument("--n-cases", type=int, default=1000,
                        help="Generated cases per log.")
    parser.add_argument("--warmup-cases", type=int, default=300,
                        help="Simulator warm-up cases (excluded from KPIs and logs).")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(range(2036, 2046)))
    parser.add_argument("--observation-types", type=str, nargs="+", choices=list(PAPER_CONFIGS), default=None,
                        help="Restrict to specific observation types (default: all).")
    args = parser.parse_args()
    OBSERVATION_CONFIGS.clear()
    OBSERVATION_CONFIGS.update(PAPER_CONFIGS)
    if args.n_cases < 30 or args.warmup_cases < 0 or len(set(args.seeds)) != len(args.seeds):
        parser.error("Use at least 30 cases, nonnegative warmup, and unique seeds")
    from evaluation.paths import data_root, output_path
    args.output = output_path(args.output, data_root(args.data_root))
    args.output.mkdir(parents=True, exist_ok=False)

    source_paths = [Path(__file__),
                    Path(__file__).parent / "generate_log.py",
                    Path(__file__).parent / "study_measurements.py",
                    Path(__file__).parent / "simulation_helpers.py",
                    Path(__file__).parent / "recommendation_helpers.py",
                    ROOT / "backend/src/process_observations.py",
                    ROOT / "backend/src/case_attribute_extractor.py",
                    ROOT / "backend/src/process_behaviour.py",
                    ROOT / "backend/src/simulation_engine.py",
                    ROOT / "backend/routers/simulate.py"]
    write_json(args.output / "study_settings.json", {
        "schema_version": 2,
        "observation_configs": {a: OBSERVATION_CONFIGS[a] for a in (args.observation_types or list(OBSERVATION_CONFIGS))},
        "context_targets": CONTEXT_TARGETS,
        "recommendation_selection": "highest impact then stable key, before simulation",
        "n_cases": args.n_cases,
        "warmup_cases": args.warmup_cases,
        "seeds": args.seeds,
        "warmup_policy": ("Native SimulationConfig warmup_cases; engine excludes warm-up from "
                          "returned cases, exported logs, and aggregate KPIs"),
        "injection_method": "Data generator; approach discovers its own model from generated log",
        "baseline_policy": ("Paired controls retain enabling/background rework; identical controls are cached per seed"),
        "miner_fix": ("_conditional_routing_rules: duplicate gateway-computation block removed; "
                      "_test_condition loops for predecessor/resource moved inside for-gw loop; "
                      "observations 10 and 11 now enabled"),
        "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in source_paths},
    })

    observation_types_to_run = list(OBSERVATION_CONFIGS.keys())
    if args.observation_types:
        observation_types_to_run = [a for a in observation_types_to_run if a in args.observation_types]

    # ── One shared baseline per seed (no injection) ───────────────────────────
    # Generated once at the top level; every observation/context trial reuses the same
    # baseline log, simulation model, and KPIs for its seed.
    baselines_dir = args.output / "controls"
    baselines_dir.mkdir()
    baselines: dict[tuple[int, str], dict] = {}   # seed → {combined, obs, ca_observations, kpis}

    def get_baseline(seed, observation_type=None, context=None):
        control = make_control(observation_type, context) if observation_type else InjectionSpec()
        clean = control == InjectionSpec()
        control_id = "clean" if clean else hashlib.sha256(repr(control).encode()).hexdigest()[:12]
        key = (seed, control_id)
        if key in baselines:
            return baselines[key]
        baseline_dir = baselines_dir / (f"seed_{seed}" if clean else f"control_{control_id}_seed_{seed}")
        baseline_dir.mkdir()
        baseline_events, baseline_case_attrs = generate(args.n_cases, seed, control)
        baseline_events_path = _save_events(baseline_events, baseline_dir)
        baseline_case_attrs.to_csv(baseline_dir / "case_attributes.csv", index=False)
        combined, obs = _discover_model_from_path(baseline_events_path)
        write_json(baseline_dir / "simulation_model.json", serialize_model(combined))
        ca_observations = _extract_case_observations(baseline_events_path, baseline_case_attrs)
        write_json(baseline_dir / "case_attribute_observations.json", ca_observations)
        write_json(baseline_dir / "control_settings.json", {"seed": seed, "specification": repr(control)})
        run_args = SimpleNamespace(cases=args.n_cases, warmup_cases=args.warmup_cases, seed=seed)
        _, kpis = simulate_and_save(combined, run_args, baseline_dir / "simulation")
        check_replay(baseline_dir / "simulation")
        baselines[key] = {"combined": combined, "obs": obs, "ca_observations": ca_observations, "kpis": kpis,
                          "events": baseline_events, "case_attrs": baseline_case_attrs,
                          "directory": str(baseline_dir.relative_to(args.output))}
        print(f"Baseline ready: seed={seed} control={control_id}", flush=True)
        return baselines[key]

    rows: list[dict] = []

    for observation_type in observation_types_to_run:
        cfg = OBSERVATION_CONFIGS[observation_type]
        injection_name = cfg["injection"]
        observation_dir = args.output / "trials" / observation_type
        observation_dir.mkdir(parents=True)

        for context in cfg["contexts"]:
            ctx_dir = observation_dir


            for seed in args.seeds:
                run_args = SimpleNamespace(cases=args.n_cases,
                                           warmup_cases=args.warmup_cases,
                                           seed=seed)

                baseline = get_baseline(seed, injection_name, context)
                baseline_obs = baseline["obs"]
                baseline_ca_observations = baseline["ca_observations"]
                baseline_kpis = baseline["kpis"]
                baseline_measurement = measure_behavior(baseline["events"], baseline["case_attrs"], injection_name, context)

                for strength in cfg["strengths"]:
                    trial_id = (f"{observation_type}_ctx{context}_str{strength:g}_seed{seed}")
                    trial_dir = ctx_dir / f"strength_{strength:g}" / f"seed_{seed}"
                    trial_dir.mkdir(parents=True)

                    row: dict[str, Any] = {
                        "observation_type": observation_type,
                        "context": context,
                        "strength": strength,
                        "seed": seed,
                        "trial_id": trial_id,
                        "directory": str(trial_dir.relative_to(args.output)),
                        "error": None,
                        "baseline_directory": baseline["directory"],
                        "goal_b_applicable": cfg["goal_b"],
                        "injection_verified": False,
                        "observation_detected": False,
                        "recommendation_returned": False,
                        "recommendation_executed": False,
                        "validated_recovery_positive": False,
                        "source_baseline_cycle_hours": baseline_measurement["source_cycle_hours"],
                        "source_injected_cycle_hours": None,
                        "source_cycle_loss_hours": None,
                        # Goal A flags
                        "target_detected": False,
                        "target_detected_in_baseline": False,
                        # Goal B flags
                        "recommendation_generated": False,
                        "cycle_loss_positive": False,
                        "cycle_improvement_positive": False,
                        # KPIs
                        "baseline_cycle_hours": None,
                        "injected_cycle_hours": None,
                        "cycle_time_increase_after_injection_hours": None,
                        "cycle_time_reduction_after_recommendation_hours": None,
                    }

                    try:
                        # ── Injection ─────────────────────────────────────────
                        injection_spec = make_injection(injection_name, context, strength)
                        inj_events, inj_case_attrs = generate(args.n_cases, seed, injection_spec)
                        inj_events_path = _save_events(inj_events, trial_dir)
                        inj_case_attrs.to_csv(trial_dir / "case_attributes.csv", index=False)

                        write_json(trial_dir / "generator_settings.json", {
                            "injection": injection_name, "context": context,
                            "strength": strength, "seed": seed,
                            "n_cases": args.n_cases,
                        })
                        measurements = measure_behavior(inj_events, inj_case_attrs,
                                                        injection_name, context)
                        verified, reason = verify_injection(baseline_measurement, measurements)
                        row["injection_verified"] = verified
                        row["injection_verification_reason"] = reason
                        row["source_injected_cycle_hours"] = measurements["source_cycle_hours"]
                        row["source_cycle_loss_hours"] = measurements["source_cycle_hours"] - baseline_measurement["source_cycle_hours"]
                        write_json(trial_dir / "behavior_measurements.json",
                                    {"baseline": baseline_measurement, "injected": measurements,
                                     "verified": verified, "reason": reason})

                        # ── Discover model from injected log ──────────────────
                        inj_combined, inj_obs = _discover_model_from_path(inj_events_path)
                        write_json(trial_dir / "simulation_model.json",
                                    serialize_model(inj_combined))

                        # Case-attribute observations for observations 12–15
                        if cfg.get("needs_case_attrs"):
                            ca_observations = _extract_case_observations(inj_events_path, inj_case_attrs)
                            write_json(trial_dir / "case_attribute_observations.json", ca_observations)

                            # Match observations
                            matched = _match_case_attr_observation(
                                ca_observations, cfg, injection_name, context)
                            baseline_matched = _match_case_attr_observation(
                                baseline_ca_observations, cfg, injection_name, context)
                        else:
                            matched = _match_observation(inj_obs, cfg, injection_name, context)
                            baseline_matched = _match_observation(
                                baseline_obs, cfg, injection_name, context)

                        write_json(trial_dir / "matched_observations.json", matched)
                        row["observation_detected"] = bool(matched)
                        row["target_detected"] = bool(verified and matched and not baseline_matched)
                        write_json(trial_dir / "baseline_matched_observations.json", baseline_matched)
                        row["target_detected_in_baseline"] = bool(baseline_matched)

                        # ── Simulate from discovered model ────────────────────
                        _, inj_kpis = simulate_and_save(inj_combined, run_args,
                                                         trial_dir / "simulation")
                        check_replay(trial_dir / "simulation")

                        row["baseline_cycle_hours"] = baseline_kpis["avg_cycle_time_hours"]
                        row["injected_cycle_hours"] = inj_kpis["avg_cycle_time_hours"]
                        row["cycle_time_increase_after_injection_hours"] = (
                            inj_kpis["avg_cycle_time_hours"] - baseline_kpis["avg_cycle_time_hours"])
                        row["cycle_loss_positive"] = bool(
                            row["cycle_time_increase_after_injection_hours"] > 0)

                        # ── Goal B: what-if via recommendation ────────────────
                        what_if_results: list[dict] = []
                        if matched and verified:
                            observation_rtype = cfg["recommendation_observation_type"]
                            if observation_rtype is not None:  # only run what-if if a recommendation type is configured
                                ca_observations_for_whatif = ca_observations if cfg.get("needs_case_attrs") else None
                                selected, what_if_results = _run_what_if(
                                    inj_combined, matched, observation_rtype, run_args,
                                    trial_dir, inj_kpis, ca_observations=ca_observations_for_whatif)
                                write_json(trial_dir / "selected_recommendations.json", selected)
                                row["recommendation_returned"] = bool(selected)
                                row["recommendation_generated"] = any(bool(r.get("suggestion")) for r in selected)
                                # The recommendation was selected before simulation.
                                reductions = [o["cycle_time_reduction_after_recommendation_hours"]
                                              for o in what_if_results if o.get("status") == "executed"]
                                if reductions:
                                    benefit = reductions[0]
                                    row["recommendation_executed"] = True
                                    row["cycle_time_reduction_after_recommendation_hours"] = benefit
                                    row["cycle_improvement_positive"] = bool(benefit > 0)
                                    row["validated_recovery_positive"] = bool(
                                        row["target_detected"] and cfg["goal_b"] and benefit > 0
                                        and row["cycle_loss_positive"] and row["source_cycle_loss_hours"] > 0)

                        write_json(trial_dir / "what_if_results.json", what_if_results)
                        write_json(trial_dir / "summary.json", {
                            **row,
                            "observations": matched,
                            "what_if_results": what_if_results,
                        })

                    except Exception as exc:
                        row["error"] = f"{type(exc).__name__}: {exc}"
                        write_json(trial_dir / "summary.json", row)

                    rows.append(row)

                    n_done = len(rows)
                    if n_done % 9 == 0:
                        _write_aggregates(rows, args.output)
                        detected = sum(r["target_detected"] for r in rows)
                        print(f"Completed {n_done} trials; "
                              f"detected {detected}/{n_done} "
                              f"({100*detected/n_done:.0f}%)",
                              flush=True)

        _write_aggregates(rows, args.output)
        print(f"Finished observation type: {observation_type}", flush=True)

    _write_aggregates(rows, args.output)
    print(f"\nStudy complete: {len(rows)} trials total")
    summary = json.loads((args.output / "summary.json").read_text())
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
