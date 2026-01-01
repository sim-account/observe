# Shared model and scenario implementation

The application and both numerical evaluations use the same discovery and simulation code. Activity durations, arrivals, routing history, resource assignments, calendars, rework, parallel overlaps, and case-attribute distributions form the simulation model. Observations and recommendation scores identify candidate changes; the simulator consumes the statistical model rather than the observation catalogue.

## Evaluated scenarios

| Opportunity | Discovery and comparison | Executable change |
| --- | --- | --- |
| O1: slow repetitions | Calendar-aware durations for repeating cases | Global activity-duration reduction |
| O2: slow resource | Calendar-aware durations against the disjoint resource complement | Relative resource-assignment reduction |
| O3: concentrated assignment | Compare assignments after a predecessor with other predecessors | Global assignment share set to the observed complement |
| O4: segment-associated rework | Activity-specific comparison among visiting cases against the complement segment | Normalized rework probabilities and aligned history routing; visit caps retained |

These implementations preserve the numerical definitions used by the four-opportunity evaluation. Scenario audits report the effective baseline, resulting parameters, and global intervention scope.

## General application behavior

Conditional-rework comparisons use first-execution exposure and disjoint reference populations. Conditional-routing tests apply eligibility gates and edit actual decision branches. Eventual-sequence observations without an executable direct transition remain visible without a fictitious scenario effect. Case-attribute comparisons use complement populations; recommendations change executable model parameters rather than inventing segment-specific duration effects.

The rework modifier handles multistep and branching return paths as well as self-loops. Unsupported modifications return HTTP 422. Resource- and terminal-aware downstream costs are ranking approximations, not evaluated accuracy claims.

## Interpretation

Durations are lognormal and arrivals exponential. Resource slots and calendars create contention. Case attributes are sampled independently and do not condition routing or durations. Post-run constraints check outcomes rather than enforce them. One backend request performs one simulation; the frontend aggregates replications using incremented seeds. Exported logs represent the latest run.
