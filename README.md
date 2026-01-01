# OBSERVE

OBSERVE discovers modifiable business process simulation parameters and process behavior observations from event logs, proposes scenario changes, and compares simulated as-is and to-be performance. This repository contains the FastAPI backend, React frontend, and code and reference outputs for both numerical evaluations.

## Run the application

Install Python 3.11, [uv](https://docs.astral.sh/uv/), and Node.js 20.19 or newer. From the repository root:

    uv sync --project backend --locked
    npm ci --prefix frontend
    ./start.sh

Open http://localhost:5173. Upload a CSV or Excel log, map its columns, inspect simulation parameters and observations, select or edit a recommendation, and compare simulations. Resources and paired timestamps are optional.

The application runs without evaluation data or API credentials. Natural-language scenarios use a local parser, with optional Anthropic configuration. Sessions expire after two hours. Downloaded logs contain the latest single simulation; frontend replications aggregate KPIs using incremented seeds.

## Reproduce the numerical evaluations

Install the locked numerical dependencies and the separately distributed data archive:

    uv sync --project backend --locked --group evaluation
    python evaluation/bundle.py install --archive /path/to/observe-evaluation-data-v1.tar.gz

The installer verifies archive and manifest checksums and refuses to overwrite existing data. Archive details are in [data_bundle.json](evaluation/data_bundle.json). A download URL will be added when the data is hosted.

Generate the as-is accuracy table and four-opportunity figure:

    uv run --project backend --locked --group evaluation python evaluation/reproduce_comparison.py
    uv run --project backend --locked --group evaluation python evaluation/paper_step2/plot_results.py

The accuracy table is written in LaTeX, Markdown, and CSV formats to generated-results/as_is/comparison/. The figure, numerical summaries, and plot metadata are written to generated-results/opportunities-analysis/.

See [as-is reproduction instructions](evaluation/README_REPRODUCE.md) and the [four-opportunity instructions](evaluation/paper_step2/README.md) and [protocol](evaluation/paper_step2/PROTOCOL.md) for regeneration and verification. Small reference tables and figures are included under each evaluation's reference/ directory.

Both evaluations accept --data-root, overriding OBSERVE_DATA_DIR; the default is observe-data/ in this repository. Generated outputs default to generated-results/ and are kept separate from the inputs. Both directories are Git-ignored.

## Tests and model behavior

    uv run --project backend --locked --group evaluation python -m unittest discover -s backend/tests
    npm run build --prefix frontend

The API tests cover upload, discovery, scenario execution, constraints, and exported logs. See [backend model and scenario behavior](backend/IMPLEMENTATION.md) for the shared numerical implementation.

[Practitioner-study evidence availability](study-evidence/README.md) is documented separately.
