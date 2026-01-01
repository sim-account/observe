import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export interface UploadResponse {
  session_id: string
  row_count: number
  columns: string[]
  detected_mapping: Record<string, string | null>
}

export interface ColumnMapping {
  case_id: string
  activity: string
  start_time?: string
  end_time?: string
  resource?: string
  source?: string
}

export interface ModelSummary {
  activities: string[]
  resources: string[]
  start_activities: string[]
  end_activities: string[]
  cases_per_day: number
  rework_activities: { activity: string; rework_rate_pct: number; mean_extra_loops: number; max_loops: number }[]
  log_metrics: Record<string, unknown> | null
  parallel_activities: {
    activity: string
    parallel_rate_pct: number
    parallel_count: number
    total_count: number
    top_partners: { activity: string; co_pct: number; count: number; mean_overlap_min: number }[]
  }[]
}

export interface Modification {
  type: string
  activity?: string
  resource?: string
  change_type: string
  value: number
  description: string
  baseline_value: number
  modified_value: number
  from_activity?: string
  to_activity?: string
  activity_a?: string
  activity_b?: string
  branches?: { to_activity: string; probability_pct: number }[]
}

export interface SimulationRequest {
  sim_days: number
  warmup_days: number
  num_runs: number
  random_seed: number
  enable_resource_contention: boolean
  resource_routing?: 'probabilistic' | 'availability'
  modifications: Modification[]
  constraints?: Constraint[]
}

export interface ModificationAuditRecord {
  type: string
  activity: string
  matched: boolean
  // time
  old_mean_min?: number
  new_mean_min?: number
  pct_change?: number
  // resource_share
  resource?: string
  old_probability?: number
  new_probability?: number
  resource_mean_min?: number
  old_weighted_mean_min?: number
  new_weighted_mean_min?: number
  expected_service_pct_change?: number
  num_resources_at_activity?: number
  // rework_rate
  old_rate?: number
  new_rate?: number
  // resource_capacity
  old_capacity?: number
  new_capacity?: number
  peak_observed?: number
}

export interface SimulationResult {
  avg_cycle_time_hours: number
  median_cycle_time_hours: number
  p75_cycle_time_hours: number
  p90_cycle_time_hours: number
  avg_waiting_time_hours: number
  median_waiting_time_hours: number
  p75_waiting_time_hours: number
  p90_waiting_time_hours: number
  waiting_pct_of_cycle: number
  avg_service_time_hours: number
  rework_rate_pct: number
  rework_cases: number
  avg_rework_acts: number
  activity_time_breakdown: Record<string, number>
  activity_wait_breakdown: Record<string, number>
  bottlenecks: {
    activity: string
    mean_duration_min: number
    mean_wait_min: number
    total_wait_share_pct: number
    total_time_share_pct: number
  }[]
  cases_completed: number
  resource_utilization: Record<string, number>
  modification_audit?: ModificationAuditRecord[]
  constraint_results?: ConstraintResult[]
}

export interface Recommendation {
  observation_type: string
  rule_text: string
  impact: number
  badge: string
  severity: string
  recommendation: string
  suggestion: Modification | Modification[] | null
  requires_input: boolean
  input_config?: { label: string; min: number; max: number; default: number; step: number }
  stable_key: string
  kpi_tags?: string[]
}

export const uploadFile = async (file: File): Promise<UploadResponse> => {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<UploadResponse>('/upload', form)
  return data
}

export const mapColumns = async (session_id: string, column_mapping: ColumnMapping, case_log_case_id_col?: string): Promise<void> => {
  await api.post(`/session/${session_id}/map-columns`, { session_id, column_mapping, case_log_case_id_col })
}

export const getModelSummary = async (session_id: string): Promise<ModelSummary> => {
  const { data } = await api.get<ModelSummary>(`/session/${session_id}/model-summary`)
  return data
}

export const runSimulation = async (session_id: string, req: SimulationRequest): Promise<SimulationResult> => {
  const { data } = await api.post<SimulationResult>(`/session/${session_id}/simulate`, req)
  return data
}

export async function runSimulationMulti(
  session_id: string,
  req: SimulationRequest,
  onProgress: (completed: number, total: number) => void,
): Promise<SimulationResult> {
  const total = Math.max(1, req.num_runs)
  const results: SimulationResult[] = []
  for (let i = 0; i < total; i++) {
    const r = await runSimulation(session_id, { ...req, num_runs: 1, random_seed: req.random_seed + i })
    results.push(r)
    onProgress(i + 1, total)
  }
  return averageResults(results)
}

function avgNum(results: SimulationResult[], key: keyof SimulationResult): number {
  const vals = results.map(r => r[key] as number)
  return vals.reduce((a, b) => a + b, 0) / vals.length
}

function avgDict(results: SimulationResult[], key: keyof SimulationResult): Record<string, number> {
  const allKeys = new Set(results.flatMap(r => Object.keys(r[key] as Record<string, number>)))
  const out: Record<string, number> = {}
  for (const k of allKeys) {
    const vals = results.map(r => ((r[key] as Record<string, number>)[k] ?? 0))
    out[k] = Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 100) / 100
  }
  return out
}

function averageResults(results: SimulationResult[]): SimulationResult {
  if (results.length === 1) return results[0]
  const scalarKeys: (keyof SimulationResult)[] = [
    'avg_cycle_time_hours', 'median_cycle_time_hours', 'p75_cycle_time_hours', 'p90_cycle_time_hours',
    'avg_waiting_time_hours', 'median_waiting_time_hours', 'p75_waiting_time_hours', 'p90_waiting_time_hours',
    'waiting_pct_of_cycle', 'avg_service_time_hours', 'rework_rate_pct', 'avg_rework_acts', 'cases_completed',
  ]
  const out = {} as SimulationResult
  for (const k of scalarKeys) out[k] = avgNum(results, k) as never
  out.rework_cases = Math.round(avgNum(results, 'rework_cases'))
  out.activity_time_breakdown = avgDict(results, 'activity_time_breakdown')
  out.activity_wait_breakdown = avgDict(results, 'activity_wait_breakdown')

  const allActs = new Set(results.flatMap(r => r.bottlenecks.map(b => b.activity)))
  out.bottlenecks = Array.from(allActs).map(act => {
    const rows = results.flatMap(r => r.bottlenecks.filter(b => b.activity === act))
    const m = (f: (b: typeof rows[0]) => number) => rows.reduce((a, b) => a + f(b), 0) / rows.length
    return {
      activity: act,
      mean_duration_min: Math.round(m(b => b.mean_duration_min) * 10) / 10,
      mean_wait_min: Math.round(m(b => b.mean_wait_min) * 10) / 10,
      total_wait_share_pct: Math.round(m(b => b.total_wait_share_pct) * 10) / 10,
      total_time_share_pct: Math.round(m(b => b.total_time_share_pct) * 10) / 10,
    }
  }).sort((a, b) => b.total_wait_share_pct - a.total_wait_share_pct).slice(0, 10)

  // Take modification_audit and constraint_results from the first run (deterministic)
  out.modification_audit = results[0].modification_audit
  out.constraint_results = results[0].constraint_results
  out.resource_utilization = avgDict(results, 'resource_utilization')

  return out
}

export interface StatisticalLearnings {
  activity_durations: { activity: string; mean_minutes: number; std_minutes: number; count: number }[]
  routing_probabilities: { from_activity: string; to_activity: string; probability: number; count: number }[]
  arrival_parameters: { cases_per_day: number; mean_inter_arrival_hours?: number }
  resource_assignments: { activity: string; resource: string; probability: number; mean_duration_min: number; std_duration_min: number; count: number }[]
  residual_wait_parameters: {
    from_activity: string; activity: string; observations: number
    positive_wait_count: number; positive_wait_probability: number
    mean_positive_minutes: number; std_positive_minutes: number
    median_positive_minutes: number; p90_positive_minutes: number
    positive_wait_samples_min: number[]; context_supported: boolean
  }[]
  rework_parameters: {
    activity: string; cases_with_rework: number; cases_visiting: number
    rework_rate_pct: number; rework_rate_given_visit_pct: number
    mean_extra_loops: number; max_loops: number
  }[]
  parallel_parameters: {
    activity: string; total_count: number; parallel_count: number; parallel_rate_pct: number
    top_partner: string | null; top_partner_pct: number; top_partner_mean_overlap_min: number
  }[]
  parallel_pairs: {
    activity_a: string; activity_b: string; co_parallel_count: number
    a_parallel_with_b_pct: number; b_parallel_with_a_pct: number; mean_overlap_min: number
  }[]
  decision_points: {
    gateway_activity: string; branch_activity: string
    probability: number; probability_pct: number; count: number; is_dominant: boolean
  }[]
  resource_capacity_dict: Record<string, number>
  peak_concurrent_dict: Record<string, number>
  calendars: { global_start_hour: number; global_end_hour: number; resource_calendars: Record<string, [number, number]> }
  log_metrics: {
    cases_count?: number; avg_cycle_time_hours?: number; median_cycle_time_hours?: number
    p75_cycle_time_hours?: number; p90_cycle_time_hours?: number
    rework_rate_pct?: number; rework_cases?: number; parallel_service_pct?: number
  }
  activities: string[]
  resources: string[]
  from_activities: string[]
  case_attribute_behaviour?: CaseAttributeBehaviour
}

export interface RuleItem {
  rule_text: string
  impact?: number
  ratio?: number
  badge?: string
  severity?: string
  direction?: 'positive' | 'negative'   // positive = condition improves; negative = worsens
  count?: number
  condition_type?: string
  condition_value?: string
  rule_type?: string
  activity?: string; activity_a?: string; activity_b?: string
  predecessor?: string; resource?: string
  baseline_mean_min?: number; conditional_mean_min?: number
  baseline_rework_rate_pct?: number; conditional_rework_rate_pct?: number
  baseline_pct?: number; conditional_pct?: number
  p_eventually?: number; p_directly?: number
  n_cases_a?: number; n_cases_b_follows?: number
}

export interface ConditionalParallelRule {
  rule_type: 'always_parallel' | 'resource_conditional' | 'predecessor_conditional' | 'case_attr_conditional'
  activity: string
  partner: string | null
  condition_type: string | null
  condition_value: string | null
  parallel_rate_pct: number
  baseline_rate_pct: number | null
  ratio: number | null
  count: number
  severity: string
  badge: string
  rule_text: string
}

export interface ConditionalRoutingRule {
  gateway_activity: string
  branch_activity: string
  condition_type: 'predecessor' | 'resource'
  condition_value: string
  baseline_pct: number
  conditional_pct: number
  ratio: number
  count: number
  rule_text: string
  severity: string
}

export interface ObservationsRules {
  sequence_rules: RuleItem[]
  time_rules: RuleItem[]
  rework_rules: RuleItem[]
  routing_rules: RuleItem[]
  conditional_routing_rules: ConditionalRoutingRule[]
  parallel_rules: ConditionalParallelRule[]
  counts: { sequence: number; time: number; rework: number; routing: number; conditional_routing: number; parallel: number }
  filters: {
    sequence_activities: string[]; sequence_types: string[]
    time_activities: string[]; time_condition_types: string[]
    rework_activities: string[]; rework_condition_types: string[]
    routing_activities: string[]; routing_predecessors: string[]
    cond_routing_gateways: string[]; cond_routing_condition_types: string[]
    parallel_activities: string[]; parallel_rule_types: string[]
  }
}

export interface CaseAttributeObservation {
  observation_type: string
  sub_type: 'cycle_time_effect' | 'rework_effect' | 'activity_presence' | 'activity_absence'
  attribute: string
  attribute_value: string
  activity?: string
  n_cases: number
  n_total: number
  ratio: number
  baseline_value: number
  conditional_value: number
  unit: 'hours' | 'pct'
  impact: number
  severity: 'red' | 'orange' | 'blue'
  rule_text: string
}

export interface CaseAttributeObservationsResponse {
  observations: CaseAttributeObservation[]
  count: number
  attributes: string[]
  sub_types: string[]
}

export const uploadCaseLog = async (
  session_id: string,
  file: File
): Promise<{ columns: string[]; detected_case_id: string; row_count: number }> => {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post(`/session/${session_id}/upload-case-log`, form)
  return data
}

export const getCaseAttributeObservations = async (session_id: string): Promise<CaseAttributeObservationsResponse> => {
  const { data } = await api.get<CaseAttributeObservationsResponse>(`/session/${session_id}/case-attribute-observations`)
  return data
}

export const getRecommendations = async (session_id: string): Promise<Recommendation[]> => {
  const { data } = await api.get<{ recommendations: Recommendation[] }>(`/session/${session_id}/observations-recommendations`)
  return data.recommendations
}

export const getProcessBehaviour = async (session_id: string): Promise<StatisticalLearnings> => {
  const { data } = await api.get<StatisticalLearnings>(`/session/${session_id}/statistical-learnings`)
  return data
}
/** @deprecated use getProcessBehaviour */
export const getStatisticalLearnings = getProcessBehaviour

export interface CaseAttributeValue {
  value: string
  share: number
  n_cases: number
}

export interface CaseAttributeSegment {
  n_total: number
  values: CaseAttributeValue[]
}

export type CaseAttributeBehaviour = Record<string, CaseAttributeSegment>

export interface ParseScenarioResponse {
  understood_as: string | null
  modifications: Modification[]
  constraint?: Constraint
  notes?: string[]
  engine_enforced: boolean
  confidence: 'high' | 'medium' | 'low'
  reason?: string
  guidance?: string
}

export interface Constraint {
  type: 'time_span' | 'activity_presence' | 'activity_absence' | 'activity_count' | 'resource_utilisation' | 'rework_rate'
  text: string
  activity_a?: string
  activity_b?: string
  activity?: string
  resource?: string
  operator: 'lte' | 'gte'
  threshold: number
  unit?: 'minutes' | 'hours' | 'days' | 'count' | 'pct'
}

export interface ConstraintResult {
  text: string
  type: string
  passed: boolean
  violation_pct?: number
  actual_value?: number
  threshold: number
  unit?: string
}

export const updateCaseAttributeBehaviour = async (
  session_id: string,
  overrides: Record<string, { values: CaseAttributeValue[] }>
): Promise<{ case_attribute_behaviour: CaseAttributeBehaviour }> => {
  const { data } = await api.patch(`/session/${session_id}/case-attribute-behaviour`, overrides)
  return data
}

export const resetCaseAttributeBehaviour = async (
  session_id: string
): Promise<{ case_attribute_behaviour: CaseAttributeBehaviour }> => {
  const { data } = await api.post(`/session/${session_id}/case-attribute-behaviour/reset`)
  return data
}

export const parseScenario = async (
  session_id: string,
  text: string,
): Promise<ParseScenarioResponse> => {
  const { data } = await api.post<ParseScenarioResponse>(`/session/${session_id}/parse-scenario`, { text })
  return data
}

export const downloadSimulationLog = (session_id: string): string =>
  `/api/session/${session_id}/simulation-log?format=csv`

export const getObservationsRules = async (session_id: string): Promise<ObservationsRules> => {
  const { data } = await api.get<ObservationsRules>(`/session/${session_id}/process-observations`)
  return data
}
