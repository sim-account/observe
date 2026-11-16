# Experiment 2 protocol

## Data generation

An independent simulator generates loan-process event logs using a Monday–Friday 09:00–17:00 calendar. Each source log has 1,000 cases. A single random-number generator samples arrivals, attributes, routing, assignments, and durations. Control and injected logs use matching seeds, although routing changes can alter subsequent random-number draws.

| Opportunity | Context | Injected change | Control → strengths | Recommendation |
| --- | --- | --- | --- | --- |
| O1 | B | Check Credit History takes longer in cases that repeat it | Duration factor 1 → 2, 2.5, 3 | Reduce duration across the activity; the reduction is rounded and bounded and derived from half the observed relative gap |
| O2 | A | Clerk-1 takes longer at Check Completeness | Duration factor 1 → 1.5, 2, 3 | Reduce the clerk's assignment share by 30% and redistribute it to other clerks |
| O3 | B | RiskAnalyst-1 receives more Assess Risk work after AML Check | Share 0.5 → 0.6, 0.75, 0.9 | Set the activity's assignment share to the share observed after other predecessors |
| O4 | B | Branch-channel cases repeat Check Credit History more often | Repetition probability 0.08 → 0.35, 0.5, 0.65 | Reduce rework across the activity; the reduction is rounded and bounded and derived from half the relative excess, with visit limits retained |

The four opportunities cover three observation types: O1 and O2 both use conditional time. Each of the twelve settings uses seeds 2036–2045 and shares the same ten control logs. Measurements of the injected changes use the known calendar.

## Identification and recommendation selection

A target observation must match the activity, condition, attribute value and subtype where applicable, and the adverse direction. It must appear in the injected log and be absent from its control. Discovery errors are reported separately from missed opportunities.

Event-level observation types have separate eligibility rules. Case-attribute comparisons require 20 cases per group. Tests use an unadjusted significance level of 0.05 and adverse-ratio thresholds around 1.2, without correction for multiple testing.

Recommendations come from the application's endpoint, including its category limits and merging. Before simulation, candidates are ordered by decreasing impact score and then stable key; the first candidate with a supported change is selected. Each executed trial uses one matching recommendation. The saved settings record numerical precision, resource ordering, random-number allocation, and calendars for replay.

## Simulation and analysis

The control, injected, and revised models each simulate 1,300 cases: 300 warm-up cases excluded from results, followed by 1,000 measured cases. Seeds match within each pair. Runs use probabilistic assignments, resource contention, residual waiting, and learned calendars. Each separately generated and discovered model is simulated once. The ten observations per setting therefore include variation in the generated logs and discovered models. The adequacy of the 300-case warm-up was not tested.

Let C, I, and T be the control, injected, and revised mean cycle times. Injection impact is mean(I−C), recommendation benefit is mean(I−T), and remaining delay is mean(T−C). Two-sided 95% Student-t confidence intervals use the paired differences from successfully identified and executed trials, including negative effects.

Identification rates use all ten planned pairs per setting. The weakest O3 setting has seven executed pairs and three misses, at seeds 2042, 2043, and 2045. Each miss fails both the adverse-ratio and Fisher-test significance thresholds.

The figure shows mean injection impact as light bars and remaining delay as dark bars, with their confidence intervals. Stars mark settings whose benefit interval lies above zero. Recovery is 100 × mean benefit / mean injection impact. Its CSV interval scales the benefit interval by the mean injection impact; it does not include uncertainty in that denominator. The figure plots delay intervals directly. Median recovery across the twelve settings is about 36.4%.

## Reference results and verification

The reference results contain 120 verified injections, no matching control observations, 117 identified and executed opportunities, three misses in the weakest O3 setting, twelve summaries, ten positive benefit intervals, and 247 distinct scenarios.

Replay checks KPIs and event counts. Regeneration checks log generation, discovery, recommendation selection, model changes, and paired estimates. These are simulation estimates from synthetic data; intervention effects in operating processes and practitioner perceptions are outside this experiment.
