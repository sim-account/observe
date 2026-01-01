# Protocol and scope

The independent loan-process generator produces paired 1,000-case source logs using a Monday–Friday 09:00–17:00 calendar. One sequential random generator drives arrivals, attributes, routing, assignment, and durations. Equal seeds define paired stochastic realizations; routing changes can alter later random-number consumption.

| Opportunity | Context | Contrast | Control → strengths | Actual intervention |
|---|---|---|---|---|
| O1 | B | Check Credit History slower in cases repeating that activity | Duration factor 1 → 2, 2.5, 3 | Global activity-duration reduction, rounded/bounded from half the observed relative gap |
| O2 | A | Clerk-1 slower at Check Completeness | Duration factor 1 → 1.5, 2, 3 | Relative 30% reduction of the clerk's assignment share, redistributed to others |
| O3 | B | RiskAnalyst-1 concentrated after AML Check at Assess Risk | Share 0.5 → 0.6, 0.75, 0.9 | Global activity assignment share set to observed share after other predecessors |
| O4 | B | Branch-channel cases repeat Check Credit History more often | Repetition probability 0.08 → 0.35, 0.5, 0.65 | Global activity rework reduction, rounded/bounded from half the relative excess; visit caps retained |

The study evaluates four opportunities across three observation types, using conditional time for O1 and O2.

Each of twelve settings uses seeds 2036–2045 and shares ten clean controls. Independent manipulation measurements use the known calendar. The exact target matches activity, condition, attribute/value/subtype and adverse direction, appears in injected observations and is absent in controls. Discovery failures are errors, not misses. Event-level miners have separate eligibility gates; case-attribute comparisons require 20 cases per group. Tests use unadjusted alpha 0.05 and adverse-ratio eligibility around 1.2, with no multiple-testing correction.

Candidates come from the actual endpoint, including category caps and merging. Selection precedes simulation: descending impact, then stable key, then first actionable candidate. Each executed trial has one actionable matching candidate. The supplied settings specify numerical precision, resource ordering, random-number allocation, and calendars used for deterministic replay.

Control, injected, and revised models each simulate 1,300 cases: 300 warm-up cases excluded from exports/KPIs and 1,000 measured completions. Within-pair seeds match; assignment is probabilistic with contention, residual waiting, and learned calendars. There is one simulation draw per separately generated/discovered model, not ten fixed-model replications. Warm-up convergence is not established.

With control/injected/revised mean cycle times C/I/T, injection impact is mean(I−C), benefit is mean(I−T), remaining delay is mean(T−C). Per-setting paired two-sided 95% Student-t intervals use identified/executed pairs. Negative effects remain included. Identification retains all ten planned pairs; weakest O3 has seven effect pairs and three misses. Those misses (2042, 2043, 2045) fail both adverse-ratio and Fisher significance gates.

The figure shows light injection-impact bars, dark remaining-delay bars, identification labels, paired intervals, and stars for positive benefit intervals. Recovery is 100 × mean benefit / mean injection impact, a ratio of means. Its CSV interval diagnostic scales the benefit interval by a fixed mean injection impact without propagating denominator uncertainty. The current figure directly plots delay intervals. Median recovery over twelve setting ratios is about 36.4%.

Expected: 120 verified manipulations, no control target matches, 117 identifications/executions, three weakest-O3 misses, twelve summaries, ten positive benefit intervals, 247 distinct scenarios. Replay checks KPIs and event populations; regeneration checks generation, mining, selection, modifications, and paired estimates. Neither establishes externally validated intervention impacts, operational improvement, or practitioner perceptions.
