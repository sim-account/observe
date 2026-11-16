# Simulation model and scenario changes

The application and both numerical experiments share the discovery and simulation code. The model contains activity durations, arrivals, recent-activity routing probabilities, resource assignments and capacities, calendars, rework, parallel execution probabilities, residual waiting times, and case-attribute distributions. Observations provide evidence for recommendations; simulations use the model parameters.

## Changes tested in Experiment 2

| Opportunity | Comparison | Scenario change |
| --- | --- | --- |
| O1: slow repetitions | Activity durations in cases that repeat the activity versus those that do not | Reduce duration for all executions of the activity |
| O2: slow resource | Activity durations for one resource versus all other resources | Reduce that resource's assignment share and redistribute it to the others |
| O3: concentrated assignment | Resource assignments after one predecessor versus other predecessors | Set the activity's resource share to the share observed after other predecessors |
| O4: segment-associated rework | Repetition rates among cases that visit the activity, comparing one attribute value with the other values | Reduce the activity's rework probabilities and update its routing probabilities; retain visit limits |

Duration comparisons account for working calendars. These scenario changes apply across the activity or resource, including cases outside the observed context. The modification audit reports the before and after parameter values and the scope of the change.

## Other supported changes

Conditional-rework observations compare cases by the predecessor or resource of the activity's first execution. Conditional-routing observations compare branch probabilities between a context and the remaining cases; their scenarios change the gateway's routing probabilities globally. Eventual-sequence observations can produce routing changes when the corresponding direct transition exists. An observation without such a transition can still be inspected, but has no suggested routing change.

Rework changes support self-loops and return paths through multiple activities or branches. Unsupported changes return HTTP 422. Recommendation ranking uses approximate downstream costs; ranking scores are not measured KPI improvements.

## Assumptions and limitations

Durations are lognormal and arrivals are exponential. Resource capacities and calendars determine when activities can start. Case attributes are sampled independently and do not affect routing or durations. Case-attribute recommendations change activity or resource parameters across cases.

Constraints are checked after simulation; they do not enforce behavior during a run. Each backend request runs one simulation. The frontend averages results over runs with successive seeds, while the downloadable event log contains only the latest run.
