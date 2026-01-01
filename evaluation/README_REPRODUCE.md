# As-is simulation accuracy

Compare OBSERVE, Simod, DeepGenerator (DGEN), RIMS, and AgentSimulator (AgentSim) on CVS (cvs_pharmacy), P2P, BPI12W (BPIC_2012W), and BPI17W (BPIC_2017W). Lower distances indicate better fidelity.

Reanalysis scores all 200 supplied simulated logs against the same four held-out test logs:

    uv run --project backend --locked --group evaluation python evaluation/reproduce_comparison.py

It generates comparison_table.tex, comparison_wide.csv, comparison_long.csv, comparison_per_run.csv, and comparison_table.md in generated-results/as_is/comparison/. LaTeX uses the paper's \tool{} macro; define it as OBSERVE and load booktabs and multirow in a standalone document.

Regeneration discovers OBSERVE from prepared training logs and simulates ten logs with seeds 42–51:

    uv run --project backend --locked --group evaluation python evaluation/as_is/run_evaluation.py --skip-evaluation
    uv run --project backend --locked --group evaluation python evaluation/reproduce_comparison.py --observe-logs generated-results/as_is/OBSERVE --output generated-results/as_is/regenerated-comparison

External baseline tools are not rerun. Their saved logs are inputs with recorded hashes. The paper reports default baseline settings; this artifact cannot independently establish every external tool's original invocation or reproduce preprocessing from raw benchmarks. The prepared split is the executable input. The paper specifies chronological first-80%/last-20% case assignment and removal of cases spanning the split; validation checks supplied train/test boundaries and case overlap. That does not recover an absent preprocessing program.

Logs normalize to case_id, activity, start_time, end_time, resource, with UTC timestamps. Test columns: case_id, activity_name, start_timestamp, end_timestamp, resource. OBSERVE/Simod use the normalized schema; DGEN uses caseid, task, start_timestamp, end_timestamp, role; RIMS uses caseid, task, start:timestamp, time:timestamp, role; AgentSim uses case_id, activity_name, start_timestamp, end_timestamp, resource. Storage paths are uniform by method and canonical dataset; column differences remain explicit in code.

Metrics use log-distance-measures 2.2.0: NGD (n=3), absolute event distribution (AED), circadian event distribution (CED), relative event distribution (RED), and cycle-time distribution (CTD, one-hour bin). Means and population standard deviations use ten runs (ddof=0). Ascending dataset ranks average ties, then average over four datasets. Missing inputs, unsuccessful scoring, and nonfinite metrics prevent a complete table. Bolding retains tolerance 0.0001; rounding can display apparent ties without tied numerical ranks.

The lock pins Python 3.11, NumPy 2.4.6, pandas 2.3.3, SciPy 1.17.1, Matplotlib 3.11.2, pm4py 2.7.23.8, and log-distance-measures 2.2.0, plus all transitive dependencies in backend/uv.lock.

Optional flags: comparison --datasets, --methods, --output (--out synonym), --data-root, --observe-logs; generator --dataset, --skip-simulation, --skip-evaluation, --output, --data-root. Supplementary ablations are outside the four-dataset paper contract.

The generator preserves learned calendars, history routing, exponential arrivals, lognormal durations, contention, residual waiting, test-case population, timestamp normalization, and dynamic warm-up from training start to test start. See build_sim_config and derive_warmup_days for exact configuration. The extra arrival-distance metric and summary are supplementary, with four-dataset coverage.

The reference OBSERVE average ranks are 1.75, 1.75, 2.00, 1.25, 1.50 (NGD/AED/CED/RED/CTD). All supplied methods are scored uniformly from their raw logs; the generated artifacts are authoritative for this bundle.

Verify consistency across the five generated table formats, prepared split boundaries, and regenerated OBSERVE logs:

    uv run --project backend --locked --group evaluation python evaluation/as_is/verify.py

Run regeneration first. The verifier accepts --comparison and --regenerated to check other output locations. It writes its numerical verification report under generated-results/as_is/verification/.
