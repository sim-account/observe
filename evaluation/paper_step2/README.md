# Experiment 2: opportunity identification and recommendation impact

This experiment tests four improvement opportunities at three strengths each, using ten seeds per setting. It uses the same backend as the application and Experiment 1. Follow the [main README](../../README.md) to install the evaluation data, and run the commands below from the repository root.

## Recreate the figure and tables

    uv run --project backend --locked --group evaluation python evaluation/paper_step2/plot_results.py

This analyses the saved trials and writes the paper figure, twelve-setting summaries, and plot metadata to generated-results/opportunities-analysis/. Reference tables and figures are included in reference/.

## Check the saved trials

    uv run --project backend --locked --group evaluation python evaluation/paper_step2/verify.py --rediscover --replay

This checks the injected changes, selected recommendations, modified models, settings, and KPIs. It rediscovers observations in all 130 source and control logs and reruns all 247 distinct simulations.

## Rerun the experiment

    uv run --project backend --locked --group evaluation python evaluation/paper_step2/regenerate.py
    uv run --project backend --locked --group evaluation python evaluation/paper_step2/plot_results.py --results generated-results/opportunities --output generated-results/regenerated-opportunities-analysis
    uv run --project backend --locked --group evaluation python evaluation/paper_step2/verify.py --results generated-results/opportunities --output generated-results/regenerated-opportunities-checks

The default settings reproduce the paper's 120 trials and ten shared controls. The generator requires a new output directory to avoid overwriting earlier trials. Use --output to choose another directory.

The saved data in observe-data/opportunities/ includes input logs, models, observations, recommendations, simulation settings, trial status, KPIs, and simulated logs, including the three missed opportunities. study_settings.json contains the configuration, and manifest.json lists the opportunities, strengths, and seeds.

Use --observation-types O1 O2 O3 O4 to select opportunities. Options --seeds, --n-cases, and --warmup-cases support additional experiments; changing them produces a different experiment from the paper. All three scripts accept --data-root and --output; analysis and verification also accept --results. Run a script with --help for its options.

See [study protocol](PROTOCOL.md) for the injection definitions, recommendation selection, and statistical analysis.
