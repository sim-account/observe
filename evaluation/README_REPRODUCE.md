# Experiment 1: simulation accuracy

This experiment compares OBSERVE, Simod, DeepGenerator (DGEN), RIMS, and AgentSimulator (AgentSim) on CVS, P2P, BPI12W, and BPI17W. Lower log distances indicate higher accuracy. Follow the [main README](../README.md) to install the evaluation data. Run all commands below from the repository root.

## Recreate the results table

    uv run --project backend --locked --group evaluation python evaluation/reproduce_comparison.py

This scores 200 saved simulation logs against the four held-out test logs. Results are written to generated-results/as_is/comparison/ as LaTeX, Markdown, and CSV tables, including results for each run. The LaTeX table uses the paper's OBSERVE macro and requires booktabs and multirow.

## Rerun OBSERVE

    uv run --project backend --locked --group evaluation python evaluation/as_is/run_evaluation.py --skip-evaluation
    uv run --project backend --locked --group evaluation python evaluation/reproduce_comparison.py --observe-logs generated-results/as_is/OBSERVE --output generated-results/as_is/regenerated-comparison

The first command discovers models from the supplied training logs and generates ten simulation logs per dataset, using seeds 42–51. It uses the learned calendars, history routing, durations, resource contention, and residual waiting times. The number of measured cases matches the test log; the warm-up period spans the training start to the test start. See build_sim_config and derive_warmup_days in as_is/run_evaluation.py for the settings. The second command compares these new OBSERVE logs with the saved baseline logs.

External baseline tools are not rerun. Their saved outputs are supplied, but their original commands and complete preprocessing code are not included. The paper reports default baseline settings and chronological 80/20 case splits with cases spanning the boundary removed. The supplied training and test logs allow checking split boundaries and case overlap, but do not allow reproducing preprocessing from the raw benchmarks.

## Measures and settings

Metrics use log-distance-measures 2.2.0: n-gram distance (NGD, n=3), absolute event distribution (AED), circadian event distribution (CED), relative event distribution (RED), and cycle-time distribution (CTD, one-hour bins). Means and population standard deviations use ten runs. Ranks average numerical ties within each dataset, then average over the four datasets. Bolding uses a tolerance of 0.0001; values that look equal after rounding may have different ranks. Missing logs, failed scoring, or nonfinite values prevent generation of a complete table.

The locked dependencies are recorded in backend/uv.lock. Reference OBSERVE average ranks are 1.75, 1.75, 2.00, 1.25, and 1.50 for NGD, AED, CED, RED, and CTD. The reference tables contain results recomputed from the supplied logs. The arrival-distance summary and additional ablations are supplementary to the paper's four-dataset comparison.

Dataset directories are cvs_pharmacy, P2P, BPIC_2012W, and BPIC_2017W. Timestamps are normalized to UTC. The scoring code maps each method's columns as follows:

| Logs | Case | Activity | Start | End | Resource |
| --- | --- | --- | --- | --- | --- |
| OBSERVE, Simod | case_id | activity | start_time | end_time | resource |
| DGEN | caseid | task | start_timestamp | end_timestamp | role |
| RIMS | caseid | task | start:timestamp | time:timestamp | role |
| AgentSim, test logs | case_id | activity_name | start_timestamp | end_timestamp | resource |

## Verify the outputs

After rerunning OBSERVE, run:

    uv run --project backend --locked --group evaluation python evaluation/as_is/verify.py

This checks the five output table formats, prepared split boundaries, and regenerated OBSERVE logs. The report is saved under generated-results/as_is/verification/. Use --comparison and --regenerated to check outputs saved elsewhere.

Both scripts accept --data-root to choose an input directory instead of observe-data/ or OBSERVE_DATA_DIR. Use --output to choose where results are saved. The comparison also accepts --datasets, --methods, and --observe-logs; the generator accepts --dataset, --skip-simulation, and --skip-evaluation. Run either script with --help for its options.
