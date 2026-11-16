# Data-Driven Simulation for Process Improvement: From Process Behavior Observations to Improvement Recommendations

This repository provides OBSERVE, the implementation accompanying the paper above. OBSERVE discovers a modifiable simulation model and process behavior observations from event logs, derives improvement recommendations, and compares simulated as-is and to-be KPIs to estimate the process-wide impact of selected recommendations. It includes the FastAPI backend, React frontend, and code and reference results for both numerical experiments.

## Abstract

Organizations continuously strive to improve their processes, for instance, by reducing cycle times or cutting costs. Analyzing the process data recorded by information systems supports this goal, but mostly reveals local improvement opportunities, such as activities that take longer when assigned to particular resources. Fixing such local problems, e.g., by reallocating work away from these resources, may merely shift the overload to others rather than improve the entire process. Business process simulation (BPS) can reveal such process-wide effects before implementing potential process changes, by simulating what-if scenarios that incorporate them. Yet, existing BPS approaches require analysts to manually translate each considered change into modifications of a simulation model, which typically limits the assessment to a few intuitively selected changes. To overcome this, we propose OBSERVE, an approach that automatically turns local improvement opportunities into improvement recommendations and reveals which of them improve the process as a whole. This is enabled through the incorporation of a novel data-driven simulation method, whose discovered models have parameters that directly correspond to the behaviors that recommendations commonly target. Our evaluation shows that OBSERVE achieves state-of-the-art as-is simulation accuracy, which provides a reliable basis for estimating the impact of recommendations. Moreover, in controlled experiments, OBSERVE identifies 98% of known improvement opportunities, and its recommendations yield estimated cycle-time improvements in ten of twelve settings. Finally, 20 practitioners from five companies found the recommendations useful for prioritizing changes.

## Run the application

Install Python 3.11, [uv](https://docs.astral.sh/uv/), and Node.js 20.19 or newer. From the repository root:

    uv sync --project backend --locked
    npm ci --prefix frontend
    ./start.sh

Open http://localhost:5173. Upload a CSV or Excel log, map its columns, inspect simulation parameters and observations, select or edit a recommendation, and compare simulations. Resources and paired timestamps are optional.

The application runs without evaluation data or API credentials. Natural-language scenarios use a local parser, with optional Anthropic configuration. Sessions expire after two hours. Downloaded logs contain the latest single simulation; frontend replications aggregate KPIs using incremented seeds.

## Reproduce the numerical evaluations

Download [observe-evaluation-data-v1.tar.gz (129 MB)](https://drive.google.com/file/d/14BOiA-VJPH8FLYiU_ZoxZkRJawY4zPWl/view?usp=sharing) from Google Drive. Then install the locked numerical dependencies and the archive, replacing the example path with the location of your downloaded file:

    uv sync --project backend --locked --group evaluation
    python evaluation/bundle.py install --archive /path/to/observe-evaluation-data-v1.tar.gz

The installer verifies archive and manifest checksums and refuses to overwrite existing data. Archive details are in [data_bundle.json](evaluation/data_bundle.json).

Reproduce the simulation-accuracy table (Experiment 1) and the opportunity-identification and recommendation-impact figure (Experiment 2):

    uv run --project backend --locked --group evaluation python evaluation/reproduce_comparison.py
    uv run --project backend --locked --group evaluation python evaluation/paper_step2/plot_results.py

The accuracy table is written in LaTeX, Markdown, and CSV formats to generated-results/as_is/comparison/. The figure, numerical summaries, and plot metadata are written to generated-results/opportunities-analysis/.

See [as-is reproduction instructions](evaluation/README_REPRODUCE.md) and the [four-opportunity instructions](evaluation/paper_step2/README.md) and [protocol](evaluation/paper_step2/PROTOCOL.md) for regeneration and verification. Small reference tables and figures are included under each evaluation's reference/ directory.

Both evaluations accept --data-root, overriding OBSERVE_DATA_DIR; the default is observe-data/ in this repository. Generated outputs default to generated-results/ and are kept separate from the inputs. The evaluation archive, extracted data, and generated outputs are excluded by .gitignore and will not be included in future commits.
