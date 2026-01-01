# Four-opportunity study

Use the same locked backend as the application and as-is evaluation. Run commands from the repository root. Install the external bundle as described in the main README.

Reanalyse saved trials to generate the paper figure and twelve-setting tables:

    uv run --project backend --locked --group evaluation python evaluation/paper_step2/plot_results.py

Verify manipulation, candidate selection, modified models, settings, and KPIs; rediscover all 130 source/control logs and replay all 247 distinct simulations:

    uv run --project backend --locked --group evaluation python evaluation/paper_step2/verify.py --rediscover --replay

Regenerate the exact cohort:

    uv run --project backend --locked --group evaluation python evaluation/paper_step2/regenerate.py
    uv run --project backend --locked --group evaluation python evaluation/paper_step2/plot_results.py --results generated-results/opportunities --output generated-results/regenerated-opportunities-analysis
    uv run --project backend --locked --group evaluation python evaluation/paper_step2/verify.py --results generated-results/opportunities --output generated-results/regenerated-opportunities-checks

Regeneration refuses an existing output directory. Its default is four contexts × three strengths × ten seeds: 120 trials and ten cached controls. --observation-types O1 O2 O3 O4 runs the specified study opportunities. --seeds, --n-cases, and --warmup-cases permit supplementary experiments; changed settings are not exact paper reproduction. All modes accept --data-root and --output; reanalysis/replay also accept --results.

The generator includes the scientific constants and injection definitions used by the study and its validity tests. Simulation, recommendation, and summarization helpers support regeneration and replay.

Small tables and the paper figure live in reference/. Inputs/models/observations/recommendations/settings/statuses/KPIs/logs live in observe-data/opportunities/. All misses are retained. study_settings.json records the study configuration; manifest.json lists opportunities, strengths, and seeds. See [PROTOCOL.md](PROTOCOL.md).
