# Data-Driven Simulation for Process Improvement: From Process Behavior Observations to Improvement Recommendations

OBSERVE turns improvement opportunities found in event logs into recommendations and uses simulation to estimate their process-wide effects. This repository contains the application and the code and results for the paper’s two numerical experiments.

[Watch the demo video](https://tinylink.net/EhICU).

## Abstract

Organizations continuously strive to improve their processes, for instance, by reducing cycle times or cutting costs. Analyzing the process data recorded by information systems supports this goal, but mostly reveals local improvement opportunities, such as activities that take longer when assigned to particular resources. Fixing such local problems, e.g., by reallocating work away from these resources, may merely shift the overload to others rather than improve the entire process. Business process simulation (BPS) can reveal such process-wide effects before implementing potential process changes, by simulating what-if scenarios that incorporate them. Yet, existing BPS approaches require analysts to manually translate each considered change into modifications of a simulation model, which typically limits the assessment to a few intuitively selected changes. To overcome this, we propose OBSERVE, an approach that automatically turns local improvement opportunities into improvement recommendations and reveals which of them improve the process as a whole. This is enabled through the incorporation of a novel data-driven simulation method, whose discovered models have parameters that directly correspond to the behaviors that recommendations commonly target. Our evaluation shows that OBSERVE achieves state-of-the-art as-is simulation accuracy, which provides a reliable basis for estimating the impact of recommendations. Moreover, in controlled experiments, OBSERVE identifies 98% of known improvement opportunities, and its recommendations yield estimated cycle-time improvements in ten of twelve settings. Finally, 20 practitioners from five companies found the recommendations useful for prioritizing changes.

## Run the application

Install Python 3.11, [uv](https://docs.astral.sh/uv/), and Node.js 20.19 or newer. From the repository root, run:

```bash
./start.sh
```

Open http://localhost:5173. Upload a CSV or Excel event log, map its columns, and select recommendations to compare as-is and to-be simulations. Resource information and paired start/end timestamps are optional.

## Evaluation

Download the [evaluation data (129 MB)](https://drive.google.com/file/d/14BOiA-VJPH8FLYiU_ZoxZkRJawY4zPWl/view?usp=sharing). From the repository root, run the following commands, adjusting the archive path if you saved it elsewhere:

```bash
uv sync --project backend --locked --group evaluation
python3 evaluation/bundle.py install --archive ~/Downloads/observe-evaluation-data-v1.tar.gz
```

To recreate the paper’s tables and figure from the saved simulation outputs:

```bash
# Experiment 1: simulation accuracy
uv run --project backend --locked --group evaluation python evaluation/reproduce_comparison.py

# Experiment 2: opportunity identification and recommendation impact
uv run --project backend --locked --group evaluation python evaluation/paper_step2/plot_results.py
```

Outputs are saved under `generated-results/`. Evaluation data and generated files are ignored by Git.

For instructions on rerunning the experiments, see:

- [Experiment 1: simulation accuracy](evaluation/README_REPRODUCE.md)
- [Experiment 2: opportunity identification and recommendation impact](evaluation/paper_step2/README.md)
