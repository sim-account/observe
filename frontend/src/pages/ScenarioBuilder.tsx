import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getRecommendations, getProcessBehaviour, runSimulationMulti, parseScenario, updateCaseAttributeBehaviour, type Recommendation, type SimulationRequest, type StatisticalLearnings, type ParseScenarioResponse, type Constraint, type CaseAttributeBehaviour, type CaseAttributeValue } from '@/api/client'
import { useSession, type SimConfig } from '@/store/sessionStore'
import { Loader2, Play, Trash2, Plus, Lightbulb, AlertTriangle, Sliders, ChevronDown, ChevronRight, Info, Lock, Sparkles } from 'lucide-react'
import Paginator from '@/components/Paginator'
import { ConfidenceBadge } from '@/pages/Observations'

const RECS_PAGE = 3

type ManualTab = 'duration' | 'arrival' | 'resource' | 'capacity' | 'sequence' | 'rework' | 'max_visits' | 'parallel' | 'decision_point' | 'case_attributes'

const BADGE_COLORS: Record<string, string> = {
  red: 'bg-gray-100 text-gray-900',
  orange: 'bg-gray-100 text-gray-900',
  green: 'bg-gray-100 text-gray-900',
  blue: 'bg-gray-100 text-gray-900',
}

const MOD_TYPE_COLORS: Record<string, string> = {
  time: 'bg-gray-100 text-gray-900',
  resource_share: 'bg-gray-100 text-gray-900',
  resource_capacity: 'bg-gray-100 text-gray-900',
  rework_rate: 'bg-gray-100 text-gray-900',
  arrival: 'bg-gray-100 text-gray-900',
  sequence: 'bg-gray-100 text-gray-900',
  parallel_rate: 'bg-gray-100 text-gray-900',
  decision_point: 'bg-gray-100 text-gray-900',
  case_attribute_dist: 'bg-gray-100 text-gray-900',
  max_visits: 'bg-gray-100 text-gray-900',
}

const BADGE_TOOLTIPS: Record<string, string> = {
  "REWORK PENALTY": "This activity takes longer in cases where it is repeated.",
  "RESOURCE GAP": "Duration differs between resources performing this activity. Test reallocating work to see the process-wide effect.",
  "PREDECESSOR EFFECT": "Activity duration differs depending on the preceding activity.",
  "RESOURCE EFFECT": "Repetition rates differ between resources performing the activity.",
  "GATE": "This activity occurs in only some cases.",
  "IMPLIES": "When activity A occurs, B usually appears later in the same case.",
  "EXCLUDES": "These activities rarely occur in the same case.",
  "RESOURCE ROUTING": "Resource assignment differs depending on the preceding activity.",
  "SLOW SEGMENT": "Cases with this attribute value have longer cycle times than other cases.",
  "HIGH REWORK": "Cases with this attribute value repeat the activity more often than other cases.",
  "HIGH DEMAND": "Cases with this attribute value visit the activity more often than other cases.",
  "SKIP RISK": "Cases with this attribute value visit the activity less often than other cases.",
  "CAPACITY BOTTLENECK": "Observed concurrent work reaches the resource’s estimated capacity. Test adding capacity to see its effect on waiting.",
  "TOP TIME DRIVER": "This activity has the largest estimated processing-time contribution, based on its frequency and mean duration.",
  "REWORK COST": "Repeated executions add processing time and may increase resource waiting.",
  "FAST TRACK": "The next activities have different average durations. Test changing their routing probabilities.",
  "DEMAND RELIEF": "Estimated demand is high relative to resource capacity. Test a lower arrival rate to see its effect on waiting.",
  "PARALLEL CONTENTION": "These activities overlap and commonly use the same resource. Test reducing their parallel execution.",
  "ROUTING BIAS": "Branch probabilities differ by predecessor or resource. The suggested change applies across the gateway.",
  "ROUTING SHIFT": "The most common next activity takes longer than the alternatives. Test changing the branch probabilities."
}


const OBSERVATION_TYPE_CATEGORY: Record<string, string> = {
  conditional_time: 'time', top_duration_driver: 'time', arrival_demand_relief: 'time',
  resource_routing: 'resource', capacity_bottleneck: 'resource',
  sequence: 'control_flow', routing_fast_track: 'control_flow', parallel_contention: 'control_flow',
  conditional_routing: 'control_flow', decision_point_routing: 'control_flow',
  conditional_rework: 'rework', expensive_rework: 'rework',
  case_attribute: 'case_attribute',
}

const CATEGORY_EMPTY_REASON: Record<string, string> = {
  resource: 'Resource recommendations need resource information and completion timestamps in the event log.',
  control_flow: 'No routing or parallel-execution recommendations were found.',
}



function computeAutoAdjust(
  config: SimConfig,
  mods: { type: string }[],
): {
  overrides: Partial<SimConfig>
  warnings: string[]   // will be auto-changed → amber
  status: string[]     // already correct → shown as info
} {
  const overrides: Partial<SimConfig> = {}
  const warnings: string[] = []
  const status: string[] = []

  const hasCapacity = mods.some(m => m.type === 'resource_capacity')
  const hasShare = mods.some(m => m.type === 'resource_share')
  const hasArrival = mods.some(m => m.type === 'arrival')
  const needsContention = hasCapacity || hasShare || hasArrival

  if (needsContention) {
    if (!config.enable_resource_contention) {
      overrides.enable_resource_contention = true
      const triggers = [
        hasCapacity && 'capacity',
        hasShare && 'resource share',
        hasArrival && 'arrival rate',
      ].filter(Boolean).join(', ')
      warnings.push(`Resource waiting will be enabled for changes to ${triggers}.`)
    } else {
      const triggers = [
        hasCapacity && 'capacity',
        hasShare && 'resource share',
        hasArrival && 'arrival rate',
      ].filter(Boolean).join(', ')
      status.push(`Resource waiting is enabled for changes to ${triggers}.`)
    }
  }

  const effectiveContention = overrides.enable_resource_contention ?? config.enable_resource_contention
  if (hasShare && effectiveContention) {
    if (config.resource_routing !== 'probabilistic') {
      overrides.resource_routing = 'probabilistic'
      warnings.push('Resource assignment will use probabilities so the changed assignment shares take effect.')
    } else {
      status.push('Resource assignment uses the specified probabilities.')
    }
  }

  return { overrides, warnings, status }
}

function Toggle({ value, onChange }: { value: boolean; onChange: () => void }) {
  return (
    <div onClick={onChange} className={`relative inline-flex w-10 h-5 rounded-none cursor-pointer transition-colors ${value ? 'bg-gray-800' : 'bg-gray-300'}`}>
      <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-none bg-white shadow transition-transform ${value ? 'translate-x-5' : ''}`} />
    </div>
  )
}

function Section({ title, open, onToggle, children }: { title: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
      <button onClick={onToggle} className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-50 transition-colors">
        <span className="font-medium text-sm text-gray-700">{title}</span>
        {open ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
      </button>
      {open && <div className="border-t border-gray-100">{children}</div>}
    </div>
  )
}

function InfoTooltip({ text }: { text: string }) {
  return (
    <span className="relative group inline-flex items-center ml-1">
      <Info className="w-3 h-3 text-gray-400 cursor-help" />
      <span className="absolute bottom-full mb-1.5 hidden group-hover:block w-64 bg-gray-800 text-white text-xs rounded-none px-3 py-2 leading-relaxed shadow-lg z-[200] pointer-events-none whitespace-normal text-left"
        style={{ left: '50%', transform: 'translateX(-50%)', maxWidth: 'min(16rem, calc(100vw - 2rem))' }}>
        {text}
      </span>
    </span>
  )
}

function CaseAttributeDistributions({
  sessionId,
  cab,
  onUpdated,
  onAddModification,
}: {
  sessionId: string
  cab: CaseAttributeBehaviour
  onUpdated: (updated: CaseAttributeBehaviour) => void
  onAddModification: (m: import('@/api/client').Modification) => void
}) {
  const attrs = Object.keys(cab)
  const [selAttr, setSelAttr] = useState(attrs[0] ?? '')
  const [selValue, setSelValue] = useState('')
  const [inputPct, setInputPct] = useState('')
  const [saving, setSaving] = useState(false)
  const [local, setLocal] = useState<CaseAttributeBehaviour>(cab)

  useEffect(() => {
    setLocal(cab)
    setSelAttr(Object.keys(cab)[0] ?? '')
    setSelValue('')
    setInputPct('')
  }, [cab])

  if (!attrs.length) {
    return <p className="text-sm text-gray-400 text-center py-4">No case attribute data available.</p>
  }

  const currentAttrData = local[selAttr]
  const currentValues = currentAttrData?.values ?? []
  const learnedAttrData = cab[selAttr]
  const learnedValue = learnedAttrData?.values.find(v => v.value === selValue)
  const learnedPct = learnedValue ? (learnedValue.share * 100).toFixed(1) : ''

  function handleAttrChange(attr: string) {
    setSelAttr(attr)
    setSelValue('')
    setInputPct('')
  }

  function handleValueChange(val: string) {
    setSelValue(val)
    const cur = currentValues.find(v => v.value === val)
    setInputPct(cur ? (cur.share * 100).toFixed(1) : '')
  }

  async function applyChange() {
    if (!selAttr || !selValue || inputPct === '') return
    const newShare = Math.max(0, Math.min(1, (parseFloat(inputPct) || 0) / 100))

    setSaving(true)
    try {
      const res = await updateCaseAttributeBehaviour(sessionId, {
        [selAttr]: { values: [{ value: selValue, share: newShare, n_cases: learnedValue?.n_cases ?? 0 }] }
      })
      setLocal(res.case_attribute_behaviour)
      onUpdated(res.case_attribute_behaviour)
      setInputPct((newShare * 100).toFixed(1))

      // Add as a modification card so user can see it in the list
      onAddModification({
        type: 'case_attribute_dist',
        activity: selAttr,
        resource: selValue,
        change_type: 'set_value',
        value: Math.round(newShare * 10000) / 100,  // store as pct
        description: `'${selAttr}' = '${selValue}' → ${(newShare * 100).toFixed(1)}%`,
        baseline_value: Math.round((learnedValue?.share ?? 0) * 10000) / 100,
        modified_value: Math.round(newShare * 10000) / 100,
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs text-gray-500 mb-1">Attribute</label>
        <select
          value={selAttr}
          onChange={e => handleAttrChange(e.target.value)}
          className="w-full border border-gray-200 rounded-none px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600"
        >
          {attrs.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">Value</label>
        <select
          value={selValue}
          onChange={e => handleValueChange(e.target.value)}
          className="w-full border border-gray-200 rounded-none px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600"
        >
          <option value="">— select a value —</option>
          {currentValues.map(v => (
            <option key={v.value} value={v.value}>{v.value}</option>
          ))}
        </select>
      </div>

      {selValue && (
        <div>
          <label className="block text-xs text-gray-500 mb-1">
            Probability (%)
            {learnedPct && <span className="ml-1 text-gray-400 font-normal">· learned: {learnedPct}%</span>}
          </label>
          <div className="flex gap-2">
            <input
              type="number"
              min="0"
              max="100"
              step="1"
              value={inputPct}
              onChange={e => setInputPct(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applyChange()}
              className="flex-1 border border-gray-200 rounded-none px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600"
              placeholder="e.g. 20"
            />
            <button
              onClick={applyChange}
              disabled={saving || inputPct === ''}
              className="px-4 py-1.5 rounded-none bg-gray-800 text-white text-sm font-medium hover:bg-gray-900 disabled:opacity-40"
            >
              {saving ? '…' : 'Apply'}
            </button>
          </div>
          <p className="text-xs text-gray-400 mt-1">Other values are redistributed proportionally.</p>
        </div>
      )}

      {selAttr && (
        <div className="bg-gray-50 rounded-none px-3 py-2 space-y-1">
          {currentValues.map(v => {
            const learned = cab[selAttr]?.values.find(lv => lv.value === v.value)
            const changed = learned && Math.abs(v.share - learned.share) > 0.0001
            return (
              <div key={v.value} className="flex items-center gap-2 text-xs">
                <span className={`flex-1 truncate ${v.value === selValue ? 'font-semibold text-gray-900' : 'text-gray-600'}`}>
                  {v.value}
                </span>
                {changed && learned && (
                  <span className="text-gray-400 tabular-nums">{(learned.share * 100).toFixed(1)}% →</span>
                )}
                <span className={`tabular-nums w-10 text-right ${changed ? 'text-gray-800 font-semibold' : 'text-gray-500'}`}>
                  {(v.share * 100).toFixed(1)}%
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function ScenarioBuilder() {
  const navigate = useNavigate()
  const { sessionId, modelSummary, modifications, addModification, removeModification, clearModifications, constraints, addConstraint, removeConstraint, baselineResult, setBaselineResult, setScenarioResult, simConfig, setSimConfig, reset } = useSession()

  const config = simConfig
  const setConfig = (updater: ((c: typeof simConfig) => Partial<typeof simConfig>) | Partial<typeof simConfig>) => {
    if (typeof updater === 'function') {
      setSimConfig(updater(simConfig))
    } else {
      setSimConfig(updater)
    }
  }
  const autoAdjust = computeAutoAdjust(config, modifications)
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [learnings, setLearnings] = useState<StatisticalLearnings | null>(null)
  const [loadingRecs, setLoadingRecs] = useState(true)
  const [runningAsIs, setRunningAsIs] = useState(false)
  const [runningToBe, setRunningToBe] = useState(false)
  const [progressAsIs, setProgressAsIs] = useState<{ done: number; total: number } | null>(null)
  const [progressToBe, setProgressToBe] = useState<{ done: number; total: number } | null>(null)
  const [error, setError] = useState('')
  const [modConflictError, setModConflictError] = useState('')
  const [recTypeFilter, setRecTypeFilter] = useState('')
  const [kpiFilter, setKpiFilter] = useState('')
  const [recsPage, setRecsPage] = useState(0)
  const [inputValues, setInputValues] = useState<Record<string, number>>({})
  const [manualTab, setManualTab] = useState<ManualTab>('duration')
  const [configOpen, setConfigOpen] = useState(true)
  const [manualOpen, setManualOpen] = useState(false)

  // User-inserted observations
  const [observationInputText, setObservationInputText] = useState('')
  const [observationParsing, setObservationParsing] = useState(false)
  const [observationSuggestion, setObservationSuggestion] = useState<ParseScenarioResponse | null>(null)

  // Manual form states
  const [mDurAct, setMDurAct] = useState('')
  const [mDurDir, setMDurDir] = useState<'reduce_pct' | 'increase_pct'>('reduce_pct')
  const [mDurVal, setMDurVal] = useState(20)
  const [mArrDir, setMArrDir] = useState<'reduce_pct' | 'increase_pct'>('reduce_pct')
  const [mArrVal, setMArrVal] = useState(20)
  const [mResAct, setMResAct] = useState('')
  const [mResRes, setMResRes] = useState('')
  const [mResVal, setMResVal] = useState(50)
  const [mCapRes, setMCapRes] = useState('')
  const [mCapVal, setMCapVal] = useState(1)
  const [mSeqFrom, setMSeqFrom] = useState('')
  const [mSeqTo, setMSeqTo] = useState('')
  const [mSeqVal, setMSeqVal] = useState(50)
  const [mReworkAct, setMReworkAct] = useState('')
  const [mReworkDir, setMReworkDir] = useState<'reduce_pct' | 'increase_pct'>('reduce_pct')
  const [mReworkVal, setMReworkVal] = useState(30)
  const [mMaxVisitAct, setMMaxVisitAct] = useState('')
  const [mMaxVisitCap, setMMaxVisitCap] = useState(1)
  const [mParActA, setMParActA] = useState('')
  const [mParActB, setMParActB] = useState('')
  const [mParVal, setMParVal] = useState(0)
  const [mGateway, setMGateway] = useState('')
  const [mGatewayBranches, setMGatewayBranches] = useState<Record<string, number>>({})

  useEffect(() => {
    if (!sessionId) { navigate('/upload'); return }

    Promise.all([
      getRecommendations(sessionId).then(setRecommendations).catch(() => { reset(); navigate('/upload') }),
      getProcessBehaviour(sessionId).then(d => {
        setLearnings(d)
        if (d.activities[0]) setMDurAct(d.activities[0])
        if (d.activities[0]) setMResAct(d.activities[0])
        if (d.resources[0]) setMResRes(d.resources[0])
        if (d.resources[0]) setMCapRes(d.resources[0])
        const capBaseline = Object.values(d.resource_capacity_dict)[0] ?? 1
        setMCapVal(capBaseline)
        if (d.from_activities[0]) { setMSeqFrom(d.from_activities[0]); setMSeqTo('') }
        if (d.rework_parameters[0]) setMReworkAct(d.rework_parameters[0].activity)
        if (d.activities[0]) setMMaxVisitAct(d.activities[0])
        if (d.parallel_pairs?.[0]) {
          setMParActA(d.parallel_pairs[0].activity_a)
          setMParActB(d.parallel_pairs[0].activity_b)
        }
        if (d.decision_points?.[0]) {
          const firstGw = d.decision_points[0].gateway_activity
          setMGateway(firstGw)
          const branches: Record<string, number> = {}
          d.decision_points.filter(g => g.gateway_activity === firstGw)
            .forEach(g => { branches[g.branch_activity] = g.probability_pct })
          setMGatewayBranches(branches)
        }
      }),
    ]).finally(() => setLoadingRecs(false))
  }, [sessionId, navigate])

  const seqToOptions = learnings?.routing_probabilities
    .filter(r => r.from_activity === mSeqFrom)
    .map(r => r.to_activity) ?? []

  const handleRunAsIs = async () => {
    if (!sessionId) return
    setRunningAsIs(true); setError(''); setProgressAsIs({ done: 0, total: config.num_runs })
    try {
      const baseReq: SimulationRequest = { ...config, modifications: [] }
      const baseResult = await runSimulationMulti(sessionId, baseReq, (done, total) => setProgressAsIs({ done, total }))
      setBaselineResult(baseResult)
      setScenarioResult(null)
      navigate('/workspace/results')
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setRunningAsIs(false); setProgressAsIs(null) }
  }

  const handleRunToBe = async () => {
    if (!sessionId) return
    setRunningToBe(true); setError('')
    try {
      const { overrides } = computeAutoAdjust(config, modifications)
      const effectiveConfig = Object.keys(overrides).length > 0 ? { ...config, ...overrides } : config
      const scenReq: SimulationRequest = { ...effectiveConfig, modifications, constraints: constraints.length > 0 ? constraints : undefined }

      // When config is auto-adjusted, the existing baseline used a different config —
      // re-run it with the same effective config so the comparison is fair.
      const needsFreshBaseline = !baselineResult || Object.keys(overrides).length > 0

      if (!needsFreshBaseline) {
        setProgressToBe({ done: 0, total: effectiveConfig.num_runs })
        const scenResult = await runSimulationMulti(sessionId, scenReq, (done, total) => {
          setProgressToBe({ done, total })
        })
        setScenarioResult(scenResult)
      } else {
        setProgressToBe({ done: 0, total: effectiveConfig.num_runs * 2 })
        const baseReq: SimulationRequest = { ...effectiveConfig, modifications: [] }
        let doneCount = 0
        const [baseResult, scenResult] = await Promise.all([
          runSimulationMulti(sessionId, baseReq, () => {
            doneCount += 1
            setProgressToBe({ done: doneCount, total: effectiveConfig.num_runs * 2 })
          }),
          runSimulationMulti(sessionId, scenReq, () => {
            doneCount += 1
            setProgressToBe({ done: doneCount, total: effectiveConfig.num_runs * 2 })
          }),
        ])
        setBaselineResult(baseResult)
        setScenarioResult(scenResult)
      }
      navigate('/workspace/results')
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setRunningToBe(false); setProgressToBe(null) }
  }

  const handleAddSuggestion = (rec: Recommendation, inputVal?: number) => {
    if (!rec.suggestion) return
    let sug = rec.suggestion
    if (rec.requires_input && inputVal !== undefined && !Array.isArray(sug)) {
      const desc = sug.type === 'sequence'
        ? `'${sug.from_activity}' → '${sug.to_activity}' set to ${inputVal.toFixed(1)}%`
        : `${sug.description} → ${inputVal}`
      sug = { ...sug, value: inputVal, modified_value: inputVal, description: desc }
    }
    // Conflict check: sequence suggestion on an activity that has a Decision Point mod
    if (!Array.isArray(sug) && sug.type === 'sequence' && sug.from_activity) {
      const dpConflict = modifications.find(
        m => m.type === 'decision_point' && m.activity === sug.from_activity
      )
      if (dpConflict) {
        setModConflictError(`'${sug.from_activity}' is already covered by a Decision Point modification. Edit the Decision Point instead.`)
        return
      }
    }
    setModConflictError('')
    addModification(sug)
  }

  const addManualDuration = () => {
    if (!mDurAct || !learnings) return
    const row = learnings.activity_durations.find(r => r.activity === mDurAct)
    const bv = row ? row.mean_minutes : 0
    const mv = mDurDir === 'reduce_pct' ? bv * (1 - mDurVal / 100) : bv * (1 + mDurVal / 100)
    addModification({ type: 'time', activity: mDurAct, change_type: mDurDir, value: mDurVal,
      description: `${mDurDir === 'reduce_pct' ? '−' : '+'}${mDurVal}% duration for '${mDurAct}'`,
      baseline_value: +bv.toFixed(1), modified_value: +mv.toFixed(1) })
  }

  const addManualArrival = () => {
    if (!learnings) return
    const bv = learnings.arrival_parameters.cases_per_day
    const mv = mArrDir === 'reduce_pct' ? bv * (1 - mArrVal / 100) : bv * (1 + mArrVal / 100)
    addModification({ type: 'arrival', change_type: mArrDir, value: mArrVal,
      description: `Arrival ${mArrDir === 'reduce_pct' ? '−' : '+'}${mArrVal}% (${bv.toFixed(1)} → ${mv.toFixed(1)}/day)`,
      baseline_value: +bv.toFixed(1), modified_value: +mv.toFixed(1) })
  }

  const addManualResource = () => {
    if (!mResAct || !mResRes || !learnings) return
    const row = learnings.resource_assignments.find(r => r.activity === mResAct && r.resource === mResRes)
    const bv = row ? +(row.probability * 100).toFixed(1) : 0
    addModification({ type: 'resource_share', activity: mResAct, resource: mResRes,
      change_type: 'set_value', value: mResVal,
      description: `'${mResRes}' share for '${mResAct}' → ${mResVal}%`,
      baseline_value: bv, modified_value: mResVal })
  }

  const addManualCapacity = () => {
    if (!mCapRes || !learnings) return
    const bv = learnings.resource_capacity_dict[mCapRes] ?? 1
    addModification({ type: 'resource_capacity', resource: mCapRes, change_type: 'set_value', value: mCapVal,
      description: `'${mCapRes}' capacity → ${mCapVal} (baseline: ${bv})`,
      baseline_value: bv, modified_value: mCapVal })
  }

  const addManualSequence = () => {
    if (!mSeqFrom || !mSeqTo || !learnings) return
    // Block if a Decision Point mod already covers this gateway
    const dpConflict = modifications.find(m => m.type === 'decision_point' && m.activity === mSeqFrom)
    if (dpConflict) {
      setModConflictError(`'${mSeqFrom}' is already covered by a Decision Point modification. Edit the Decision Point instead.`)
      return
    }
    setModConflictError('')
    const row = learnings.routing_probabilities.find(r => r.from_activity === mSeqFrom && r.to_activity === mSeqTo)
    const bv = row ? +(row.probability * 100).toFixed(1) : 0
    addModification({ type: 'sequence', from_activity: mSeqFrom, to_activity: mSeqTo,
      change_type: 'set_value', value: mSeqVal,
      description: `'${mSeqFrom}' → '${mSeqTo}' set to ${mSeqVal}%`,
      baseline_value: bv, modified_value: mSeqVal })
  }

  const addManualRework = () => {
    if (!mReworkAct || !learnings) return
    const row = learnings.rework_parameters.find(r => r.activity === mReworkAct)
    const bv = row ? +(row.rework_rate_given_visit_pct ?? row.rework_rate_pct).toFixed(1) : 0
    const mv = mReworkDir === 'reduce_pct' ? bv * (1 - mReworkVal / 100) : bv * (1 + mReworkVal / 100)
    addModification({
      type: 'rework_rate', activity: mReworkAct, change_type: mReworkDir, value: mReworkVal,
      description: `${mReworkDir === 'reduce_pct' ? '−' : '+'}${mReworkVal}% rework rate for '${mReworkAct}'`,
      baseline_value: +bv.toFixed(1), modified_value: +mv.toFixed(1),
    })
  }

  const addManualGateway = () => {
    if (!mGateway || !learnings) return
    const branches = Object.entries(mGatewayBranches)
      .map(([to_activity, probability_pct]) => ({ to_activity, probability_pct }))
    if (branches.length < 2) return
    const total = branches.reduce((s, b) => s + b.probability_pct, 0)
    if (total > 100.5) return
    // Block if any Transition mod already targets a branch of this gateway
    const seqConflict = modifications.find(
      m => m.type === 'sequence' && m.from_activity === mGateway
    )
    if (seqConflict) {
      setModConflictError(`A Transition modification already targets '${mGateway}' → '${seqConflict.to_activity}'. Remove it first, then use Decision Point to modify all branches together.`)
      return
    }
    // Block if a Decision Point mod already exists for this gateway
    const dpConflict = modifications.find(m => m.type === 'decision_point' && m.activity === mGateway)
    if (dpConflict) {
      setModConflictError(`A Decision Point modification already exists for '${mGateway}'. Remove it before adding a new one.`)
      return
    }
    setModConflictError('')
    const others = Math.max(0, 100 - total)
    const branchDesc = branches.map(b => `${b.to_activity}: ${b.probability_pct.toFixed(0)}%`).join(', ')
    const desc = others > 0.5
      ? `Gateway '${mGateway}': ${branchDesc}, others: ${others.toFixed(0)}%`
      : `Gateway '${mGateway}': ${branchDesc}`
    addModification({
      type: 'decision_point', activity: mGateway,
      change_type: 'set_value', value: 0,
      branches,
      description: desc,
      baseline_value: 0, modified_value: 0,
    })
  }

  const addManualParallel = () => {

    if (!mParActA || !mParActB || !learnings) return
    const row = learnings.parallel_pairs?.find(
      r => (r.activity_a === mParActA && r.activity_b === mParActB) ||
           (r.activity_a === mParActB && r.activity_b === mParActA)
    )
    const bv = row ? +(row.a_parallel_with_b_pct).toFixed(1) : 0
    addModification({
      type: 'parallel_rate', activity_a: mParActA, activity_b: mParActB,
      change_type: 'set_value', value: mParVal,
      description: `'${mParActA}' ∥ '${mParActB}' → ${mParVal}% (was ${bv}%)`,
      baseline_value: bv, modified_value: mParVal,
    })
  }

  const recCategoryCounts = recommendations.reduce<Record<string, number>>((acc, r) => {
    const cat = OBSERVATION_TYPE_CATEGORY[r.observation_type] ?? r.observation_type
    acc[cat] = (acc[cat] ?? 0) + 1
    return acc
  }, {})

  // Apply type filter first, then derive KPI counts from that subset
  const typeFilteredRecs = recTypeFilter
    ? recommendations.filter(r => (OBSERVATION_TYPE_CATEGORY[r.observation_type] ?? r.observation_type) === recTypeFilter)
    : recommendations

  const kpiCounts = typeFilteredRecs.reduce<Record<string, number>>((acc, r) => {
    for (const tag of r.kpi_tags ?? []) acc[tag] = (acc[tag] ?? 0) + 1
    return acc
  }, {})

  const filteredRecs = typeFilteredRecs.filter(r => {
    if (kpiFilter && !(r.kpi_tags ?? []).includes(kpiFilter)) return false
    return true
  })

  const visibleRecs = filteredRecs.slice(recsPage * RECS_PAGE, (recsPage + 1) * RECS_PAGE)

  async function handleParseScenario() {
    if (!sessionId || !observationInputText.trim()) return
    setObservationParsing(true)
    setObservationSuggestion(null)
    try {
      const result = await parseScenario(sessionId, observationInputText.trim())
      setObservationSuggestion(result)
    } catch {
      setObservationSuggestion({ understood_as: null, modifications: [], engine_enforced: false, confidence: 'low', reason: 'Server error', guidance: '' })
    } finally {
      setObservationParsing(false)
    }
  }

  function confirmObservationSuggestion() {
    if (!observationSuggestion) return
    observationSuggestion.modifications.forEach(m => addModification(m))
    if (observationSuggestion.constraint) {
      // Use understood_as as the display text (resolved activity names),
      // falling back to the raw input text
      const c = {
        ...observationSuggestion.constraint,
        text: observationSuggestion.understood_as || observationSuggestion.constraint.text,
      } as Constraint
      addConstraint(c)
    }
    setObservationSuggestion(null)
    setObservationInputText('')
  }

  const MANUAL_TABS: { id: ManualTab; label: string }[] = [
    { id: 'duration', label: 'Duration' },
    { id: 'arrival', label: 'Arrival' },
    { id: 'resource', label: 'Resource share' },
    { id: 'capacity', label: 'Capacity' },
    { id: 'sequence', label: 'Transition' },
    { id: 'rework', label: 'Rework rate' },
    { id: 'max_visits', label: 'Max visits' },
    { id: 'parallel', label: 'Parallel rate' },
    { id: 'decision_point', label: 'Decision Point' },
    ...(learnings?.case_attribute_behaviour && Object.keys(learnings.case_attribute_behaviour).length > 0
      ? [{ id: 'case_attributes' as ManualTab, label: '🏷 Case Attributes' }]
      : []),
  ]

  return (
    <div className="flex-1 grid grid-cols-2 gap-0 min-h-0 max-h-[calc(100vh-57px)]">
      {/* Left: recommendations */}
      <div className="border-r border-gray-200 p-6 overflow-y-auto">

        {/* ── User-inserted observations input ── */}
        <div className="mb-5 bg-gray-50 border border-gray-200 rounded-none p-3">
          <div className="flex items-center gap-2 mb-2">
            <Sparkles className="w-4 h-4 text-gray-600" />
            <span className="text-sm font-medium text-gray-900">Inject an Observation / Modification</span>
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={observationInputText}
              onChange={e => { setObservationInputText(e.target.value); setObservationSuggestion(null) }}
              onKeyDown={e => e.key === 'Enter' && handleParseScenario()}
              placeholder="e.g. ensure no more than 2 reviews per case  ·  enforce Submit to Close under 30 days  ·  eliminate 30% of preparation time"
              className="flex-1 text-sm border border-gray-200 rounded-none px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-gray-400 bg-white"
            />
            <button
              onClick={handleParseScenario}
              disabled={observationParsing || !observationInputText.trim()}
              className="px-3 py-1.5 rounded-none bg-gray-800 text-white text-sm font-medium hover:bg-gray-900 disabled:opacity-50 flex items-center gap-1"
            >
              {observationParsing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : '→'}
            </button>
          </div>

          {/* Suggestion card */}
          {observationSuggestion && (
            <div className={`mt-3 rounded-none border p-3 text-sm ${
              observationSuggestion.understood_as ? 'bg-white border-gray-200' : 'bg-gray-50 border-gray-200'
            }`}>
              {observationSuggestion.understood_as ? (
                <>
                  <p className="text-xs text-gray-500 mb-1">We understood this as:</p>
                  <p className="font-medium text-gray-900 mb-2">"{observationSuggestion.understood_as}"</p>

                  {/* Engine modifications */}
                  {observationSuggestion.modifications.length > 0 && (
                    <div className="mb-2 space-y-1">
                      <p className="text-xs text-gray-400">Engine will apply:</p>
                      {observationSuggestion.modifications.map((m, i) => (
                        <div key={i} className="text-xs text-gray-900 bg-gray-50 rounded-none px-2 py-1 flex items-center gap-1.5">
                          <span className="w-1.5 h-1.5 rounded-none bg-gray-400 shrink-0" />
                          {m.description}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Post-run constraint */}
                  {observationSuggestion.constraint && (
                    <div className="mb-2">
                      <p className="text-xs text-gray-400 mb-1">{observationSuggestion.modifications.length > 0 ? 'Also checked post-run:' : 'Checked post-run:'}</p>
                      <div className="text-xs text-gray-900 bg-gray-50 rounded-none px-2 py-1 flex items-center gap-1.5">
                        <Lock className="w-3 h-3 shrink-0" />
                        {observationSuggestion.understood_as}
                      </div>
                    </div>
                  )}

                  {/* Notes / approximations */}
                  {observationSuggestion.notes && observationSuggestion.notes.length > 0 && (
                    <div className="mb-2 space-y-1">
                      {observationSuggestion.notes.map((n, i) => (
                        <p key={i} className="text-xs text-gray-900 bg-gray-50 rounded-none px-2 py-1">⚠ {n}</p>
                      ))}
                    </div>
                  )}

                  <div className="flex gap-2 mt-2">
                    <button onClick={confirmObservationSuggestion} className="text-xs px-3 py-1 rounded-none bg-gray-800 text-white hover:bg-gray-900">
                      Confirm — Add
                    </button>
                    <button onClick={() => setObservationSuggestion(null)} className="text-xs px-3 py-1 rounded-none border border-gray-200 text-gray-500 hover:bg-gray-50">
                      Discard
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-gray-900 font-medium mb-1">Couldn't map this input</p>
                  {observationSuggestion.reason && <p className="text-xs text-gray-800 mb-2">{observationSuggestion.reason}</p>}
                  {observationSuggestion.guidance && (
                    <pre className="text-xs text-gray-600 whitespace-pre-wrap bg-gray-50 rounded-none p-2">{observationSuggestion.guidance}</pre>
                  )}
                  <button onClick={() => setObservationSuggestion(null)} className="mt-2 text-xs text-gray-400 hover:text-gray-600">Dismiss</button>
                </>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Lightbulb className="w-5 h-5 text-gray-600" />
            <h2 className="font-semibold text-gray-900">Observation Recommendations</h2>
            <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-none">{recommendations.length}</span>
          </div>
          <div className="flex items-center gap-2">
            <select value={recTypeFilter} onChange={e => { setRecTypeFilter(e.target.value); setKpiFilter(''); setRecsPage(0) }}
              className="border border-gray-200 rounded-none px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-gray-600">
              <option value="">All types</option>
              {([
                { value: 'time', label: '⏱ Time' },
                { value: 'resource', label: '👤 Resource' },
                { value: 'control_flow', label: '🔀 Control Flow' },
                { value: 'rework', label: '🔄 Rework' },
                { value: 'case_attribute', label: '🏷 Case Attributes' },
              ] as const).map(({ value, label }) => {
                const n = recCategoryCounts[value] ?? 0
                return (
                  <option key={value} value={value} disabled={n === 0}>
                    {label}{n > 0 ? ` (${n})` : ' —'}
                  </option>
                )
              })}
            </select>
          </div>
        </div>

        {/* KPI filter pills */}
        <div className="flex flex-wrap gap-1.5 mb-4">
          {([
            { value: '', label: 'All KPIs' },
            { value: 'cycle_time', label: '⏱ Cycle time' },
            { value: 'wait_time', label: '⏳ Wait time' },
            { value: 'rework_rate', label: '🔄 Rework' },
            { value: 'bottleneck', label: '🚧 Bottleneck' },
            { value: 'resource_utilization', label: '👤 Utilization' },
          ]).map(({ value, label }) => {
            const active = kpiFilter === value
            const count = value === '' ? recommendations.length : (kpiCounts[value] ?? 0)
            if (value !== '' && count === 0) return null
            return (
              <button key={value}
                onClick={() => { setKpiFilter(value); setRecsPage(0) }}
                className={`text-xs px-2.5 py-1 rounded-none border font-medium transition-colors ${
                  active
                    ? 'bg-gray-800 text-white border-gray-800'
                    : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300 hover:text-gray-800'
                }`}>
                {label}{value !== '' && ` (${count})`}
              </button>
            )
          })}
        </div>

        {loadingRecs ? (
          <div className="text-center py-12 text-gray-300"><Loader2 className="w-6 h-6 animate-spin mx-auto" /></div>
        ) : visibleRecs.length === 0 ? (
          <div className="py-8 text-center space-y-1">
            <p className="text-sm text-gray-400">
              {recTypeFilter ? 'No recommendations in this category for your process data.' : 'No recommendations found'}
            </p>
            {recTypeFilter && CATEGORY_EMPTY_REASON[recTypeFilter] && (
              <p className="text-xs text-gray-300 max-w-xs mx-auto">{CATEGORY_EMPTY_REASON[recTypeFilter]}</p>
            )}
          </div>
        ) : (
          <div className="space-y-2.5">
            {visibleRecs.map(rec => {
              const inpVal = inputValues[rec.stable_key] ?? rec.input_config?.default ?? 0
              return (
                <div key={rec.stable_key} className={`rounded-none border p-4 transition-all hover:shadow-sm ${
                  rec.severity === 'red' ? 'bg-gray-50 border-gray-100' :
                  rec.severity === 'orange' ? 'bg-gray-50 border-gray-100' :
                  rec.severity === 'green' ? 'bg-gray-50 border-gray-100' :
                  'bg-gray-50 border-gray-100'
                }`}>
                  <div className="flex items-start gap-3">
                    <div className="flex flex-col gap-1 shrink-0 mt-0.5">
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded-none inline-flex items-center gap-1 ${BADGE_COLORS[rec.severity ?? ''] || 'bg-gray-100 text-gray-600'}`}>
                        {rec.badge} · {rec.impact.toFixed(1)}×
                        {BADGE_TOOLTIPS[rec.badge] && <InfoTooltip text={BADGE_TOOLTIPS[rec.badge]} />}
                      </span>
                      <ConfidenceBadge ruleText={rec.rule_text} />
                    </div>
                    <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 leading-snug">{rec.rule_text}</p>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">{rec.recommendation}</p>
                  {rec.kpi_tags && rec.kpi_tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {rec.kpi_tags.map(tag => {
                        const KPI_LABELS: Record<string, string> = {
                          cycle_time: '⏱ Cycle time',
                          wait_time: '⏳ Wait time',
                          rework_rate: '🔄 Rework',
                          bottleneck: '🚧 Bottleneck',
                          resource_utilization: '👤 Utilization',
                        }
                        return (
                          <span key={tag} className="text-xs px-1.5 py-0.5 rounded-none bg-white/70 border border-gray-200 text-gray-500">
                            {KPI_LABELS[tag] ?? tag}
                          </span>
                        )
                      })}
                    </div>
                  )}
                  {rec.suggestion && (
                    <div className="mt-3 flex items-center gap-2">
                      {rec.requires_input && rec.input_config && (
                        <div className="flex items-center gap-1.5 flex-1">
                          <label className="text-xs text-gray-500 whitespace-nowrap">{rec.input_config.label}</label>
                          <input type="number" min={rec.input_config.min} max={rec.input_config.max} step={rec.input_config.step}
                            value={inpVal} onChange={e => setInputValues(v => ({ ...v, [rec.stable_key]: Number(e.target.value) }))}
                            className="w-20 rounded-none border border-gray-200 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600 bg-white" />
                          <span className="text-xs text-gray-400">%</span>
                        </div>
                      )}
                      <button onClick={() => handleAddSuggestion(rec, rec.requires_input ? inpVal : undefined)}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-none bg-white hover:bg-gray-50 text-gray-900 text-xs font-medium border border-gray-200 transition-colors shadow-sm">
                        <Plus className="w-3.5 h-3.5" /> Add
                      </button>
                    </div>
                  )}
                    </div>{/* flex-1 */}
                  </div>{/* flex items-start gap-3 */}
                </div>
              )
            })}
          </div>
        )}

        <Paginator total={filteredRecs.length} page={recsPage} pageSize={RECS_PAGE} onPage={setRecsPage} />
      </div>

      {/* Right panel */}
      <div className="p-5 overflow-y-auto space-y-4 bg-gray-50/50">
        {/* Run buttons */}
        <div className="space-y-2">
          {(autoAdjust.warnings.length > 0 || autoAdjust.status.length > 0) && modifications.length > 0 && (
            <div className={`flex items-start gap-2 px-3 py-2 rounded-none text-xs border ${
              autoAdjust.warnings.length > 0
                ? 'bg-gray-50 border-gray-200 text-gray-900'
                : 'bg-gray-50 border-gray-200 text-gray-900'
            }`}>
              <Info className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${autoAdjust.warnings.length > 0 ? 'text-gray-600' : 'text-gray-400'}`} />
              <div className="space-y-0.5">
                {autoAdjust.warnings.map((msg, i) => <p key={i}>{msg}</p>)}
                {autoAdjust.status.map((msg, i) => <p key={`s${i}`}>{msg}</p>)}
                {autoAdjust.warnings.length > 0 && baselineResult && (
                  <p className="mt-0.5 opacity-80">Baseline will be re-run for a fair comparison.</p>
                )}
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <button onClick={handleRunAsIs} disabled={runningAsIs || runningToBe}
              className="flex items-center justify-center gap-2 px-4 py-3 rounded-none bg-white border border-gray-300 text-gray-700 text-sm font-semibold hover:bg-gray-50 disabled:opacity-60 shadow-sm transition-colors">
              {runningAsIs ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              Run As-Is
            </button>
            <button onClick={handleRunToBe} disabled={runningAsIs || runningToBe || modifications.length === 0}
              className="flex items-center justify-center gap-2 px-4 py-3 rounded-none bg-gray-800 text-white text-sm font-semibold hover:bg-gray-900 disabled:opacity-50 disabled:cursor-not-allowed shadow-sm transition-colors">
              {runningToBe ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              Run To-Be
            </button>
          </div>
          {(progressAsIs || progressToBe) && (() => {
            const p = progressAsIs ?? progressToBe!
            const pct = p.total > 0 ? Math.round((p.done / p.total) * 100) : 0
            return (
              <div className="space-y-1">
                <div className="flex justify-between text-xs text-gray-500">
                  <span>Run {Math.min(p.done + 1, p.total)} of {p.total}</span>
                  <span>{pct}%</span>
                </div>
                <div className="w-full h-1.5 bg-gray-200 rounded-none overflow-hidden">
                  <div
                    className="h-full bg-gray-600 rounded-none transition-all duration-300"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            )
          })()}
        </div>

        {error && (
          <div className="px-4 py-3 bg-gray-50 border border-gray-200 rounded-none text-sm text-gray-900 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" /> {error}
          </div>
        )}

        {/* Sim config */}
        <Section title="⚙️ Simulation Config" open={configOpen} onToggle={() => setConfigOpen(v => !v)}>
          <div className="p-4 space-y-4">
            {/* Working hours — read-only from learned data */}
            {modelSummary && (
              <div className="flex items-center justify-between px-3 py-2 bg-gray-50 rounded-none text-xs text-gray-500 border border-gray-100">
                <span className="font-medium text-gray-600">Working hours (learned)</span>
                <span>
                  {String((modelSummary.log_metrics as Record<string,unknown>|null)?.global_start_hour ?? 0).padStart(2,'0')}:00
                  {' – '}
                  {String((modelSummary.log_metrics as Record<string,unknown>|null)?.global_end_hour ?? 24).padStart(2,'0')}:00
                </span>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              {([
                { label: 'Sim days', key: 'sim_days', min: 1, max: 365,
                  tooltip: 'Simulation period after warm-up, in calendar days.' },
                { label: 'Warm-up days', key: 'warmup_days', min: 0, max: 60,
                  tooltip: 'Initial cases excluded from results so resource queues can build up before measurement.' },
                { label: 'Runs (avg)', key: 'num_runs', min: 1, max: 20,
                  tooltip: 'Number of runs with different seeds. The displayed KPIs are averaged across runs.' },
                { label: 'Random seed', key: 'random_seed', min: 0, max: 99999,
                  tooltip: 'Starting random seed. Matching settings and seeds reproduce the same run.' },
              ] as { label: string; key: keyof typeof config; min: number; max: number; tooltip: string }[]).map(({ label, key, min, max, tooltip }) => {
                const logSpanDays = (modelSummary?.log_metrics as Record<string, unknown> | null)?.log_span_days as number | undefined
                const hint = key === 'sim_days' && logSpanDays != null
                  ? `log = ${logSpanDays}d`
                  : null
                return (
                <div key={key}>
                  <label className="flex items-center justify-between text-xs font-medium text-gray-600 mb-1">
                    <span className="flex items-center">
                      {label}
                      <InfoTooltip text={tooltip} />
                    </span>
                    {hint && <span className="text-gray-400 font-normal">{hint}</span>}
                  </label>
                  <input type="number" min={min} max={max} value={config[key] as number}
                    onChange={e => setConfig(c => ({ ...c, [key]: Number(e.target.value) }))}
                    className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600" />
                </div>
                )
              })}
            </div>
            <div className="space-y-2.5 pt-1">
              <label className="flex items-center gap-3 cursor-pointer">
                <Toggle value={config.enable_resource_contention} onChange={() => setConfig(c => ({ ...c, enable_resource_contention: !c.enable_resource_contention }))} />
                <span className="flex items-center text-sm text-gray-700">
                  Resource contention
                  <InfoTooltip text="When on, resources queue up — cases wait if all slots are busy. Turn off for a contention-free baseline that ignores resource availability." />
                </span>
              </label>
              {config.enable_resource_contention && (
                <div className="ml-[52px] space-y-1">
                  <p className="text-xs text-gray-500 flex items-center">
                    Resource routing mode
                    <InfoTooltip text="Probabilistic: each activity must be performed by a specific resource drawn from the historical distribution (e.g. if Bob handles 70% of 'Review' in the log, simulated cases are routed to Bob 70% of the time and wait for him even if Alice is free). Availability: routes to whichever resource is free soonest, still weighted softly by historical probability — more realistic scheduling, reduces queue overestimation." />
                  </p>
                  <div className="flex gap-1 bg-gray-100 p-0.5 rounded-none w-fit">
                    {(['probabilistic', 'availability'] as const).map(mode => (
                      <button
                        key={mode}
                        onClick={() => setConfig(c => ({ ...c, resource_routing: mode }))}
                        className={`px-3 py-1 rounded-none text-xs font-medium transition-colors ${
                          config.resource_routing === mode
                            ? 'bg-white shadow-sm text-gray-900'
                            : 'text-gray-500 hover:text-gray-700'
                        }`}
                      >
                        {mode === 'probabilistic' ? 'Probabilistic' : 'Availability'}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </Section>

        {/* Manual modifications */}
        <Section title="🔧 Manual Modifications" open={manualOpen} onToggle={() => setManualOpen(v => !v)}>
          <div className="p-4 space-y-4">
            <div className="flex gap-1 bg-gray-100 p-1 rounded-none">
              {MANUAL_TABS.map(t => (
                <button key={t.id} onClick={() => { setManualTab(t.id); setModConflictError('') }}
                  className={`flex-1 py-1.5 rounded-none text-xs font-medium transition-colors ${manualTab === t.id ? 'bg-white shadow-sm text-gray-900' : 'text-gray-500 hover:text-gray-700'}`}>
                  {t.label}
                </button>
              ))}
            </div>

            {manualTab === 'duration' && (
              <div className="space-y-3">
                <select value={mDurAct} onChange={e => setMDurAct(e.target.value)}
                  className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                  {learnings?.activities.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
                {mDurAct && (() => {
                  const row = learnings?.activity_durations.find(r => r.activity === mDurAct)
                  if (!row) return null
                  const newMean = mDurDir === 'reduce_pct'
                    ? row.mean_minutes * (1 - mDurVal / 100)
                    : row.mean_minutes * (1 + mDurVal / 100)
                  return (
                    <div className="flex items-center gap-2 text-xs text-gray-500 bg-gray-50 rounded-none px-3 py-2">
                      <span>Current mean:</span>
                      <span className="font-semibold text-gray-800">{row.mean_minutes.toFixed(1)} min</span>
                      <span className="text-gray-300">→</span>
                      <span className="font-semibold text-gray-800">{newMean.toFixed(1)} min</span>
                      <span className="ml-auto text-gray-400">±{row.std_minutes.toFixed(1)} std · {row.count.toLocaleString()} obs</span>
                    </div>
                  )
                })()}
                <div className="grid grid-cols-2 gap-2">
                  <select value={mDurDir} onChange={e => setMDurDir(e.target.value as typeof mDurDir)}
                    className="rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                    <option value="reduce_pct">Reduce by %</option>
                    <option value="increase_pct">Increase by %</option>
                  </select>
                  <div className="flex items-center gap-1.5 border border-gray-200 rounded-none px-3">
                    <input type="number" min={1} max={99} value={mDurVal} onChange={e => setMDurVal(+e.target.value)}
                      className="w-full text-sm focus:outline-none" />
                    <span className="text-gray-400 text-sm">%</span>
                  </div>
                </div>
                <button onClick={addManualDuration} className="w-full flex items-center justify-center gap-2 py-2 bg-gray-50 hover:bg-gray-100 text-gray-900 text-sm font-medium rounded-none border border-gray-200 transition-colors">
                  <Plus className="w-4 h-4" /> Add modification
                </button>
              </div>
            )}

            {manualTab === 'arrival' && (
              <div className="space-y-3">
                <p className="text-xs text-gray-500">Current: <span className="font-semibold text-gray-800">{learnings?.arrival_parameters.cases_per_day.toFixed(1)} cases/day</span></p>
                <div className="grid grid-cols-2 gap-2">
                  <select value={mArrDir} onChange={e => setMArrDir(e.target.value as typeof mArrDir)}
                    className="rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                    <option value="reduce_pct">Reduce by %</option>
                    <option value="increase_pct">Increase by %</option>
                  </select>
                  <div className="flex items-center gap-1.5 border border-gray-200 rounded-none px-3">
                    <input type="number" min={1} max={200} value={mArrVal} onChange={e => setMArrVal(+e.target.value)}
                      className="w-full text-sm focus:outline-none" />
                    <span className="text-gray-400 text-sm">%</span>
                  </div>
                </div>
                <button onClick={addManualArrival} className="w-full flex items-center justify-center gap-2 py-2 bg-gray-50 hover:bg-gray-100 text-gray-900 text-sm font-medium rounded-none border border-gray-200 transition-colors">
                  <Plus className="w-4 h-4" /> Add modification
                </button>
              </div>
            )}

            {manualTab === 'resource' && (
              <div className="space-y-3">
                <select value={mResAct} onChange={e => setMResAct(e.target.value)}
                  className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                  {learnings?.activities.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
                <select value={mResRes} onChange={e => setMResRes(e.target.value)}
                  className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                  {learnings?.resources.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
                {mResAct && mResRes && (() => {
                  const row = learnings?.resource_assignments.find(r => r.activity === mResAct && r.resource === mResRes)
                  if (!row) return <p className="text-xs text-gray-800 bg-gray-50 rounded-none px-3 py-2">No data for this activity–resource pair.</p>
                  return (
                    <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-gray-500 bg-gray-50 rounded-none px-3 py-2">
                      <span>Current share: <span className="font-semibold text-gray-800">{(row.probability * 100).toFixed(1)}%</span></span>
                      <span>Avg duration: <span className="font-semibold text-gray-800">{row.mean_duration_min.toFixed(1)} min</span></span>
                    </div>
                  )
                })()}
                <div className="flex items-center gap-1.5 border border-gray-200 rounded-none px-3 py-2">
                  <span className="text-xs text-gray-500 whitespace-nowrap">New share</span>
                  <input type="number" min={0} max={100} value={mResVal} onChange={e => setMResVal(+e.target.value)}
                    className="flex-1 text-sm focus:outline-none" />
                  <span className="text-gray-400 text-sm">%</span>
                </div>
                <button onClick={addManualResource} className="w-full flex items-center justify-center gap-2 py-2 bg-gray-50 hover:bg-gray-100 text-gray-900 text-sm font-medium rounded-none border border-gray-200 transition-colors">
                  <Plus className="w-4 h-4" /> Add modification
                </button>
              </div>
            )}

            {manualTab === 'capacity' && (
              <div className="space-y-3">
                <select value={mCapRes} onChange={e => {
                  setMCapRes(e.target.value)
                  setMCapVal(learnings?.resource_capacity_dict[e.target.value] ?? 1)
                }} className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                  {learnings?.resources.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
                {mCapRes && <p className="text-xs text-gray-500">Baseline: <span className="font-semibold">{learnings?.resource_capacity_dict[mCapRes] ?? 1}</span> slots</p>}
                <div className="flex items-center gap-1.5 border border-gray-200 rounded-none px-3 py-2">
                  <span className="text-xs text-gray-500 whitespace-nowrap">Parallel slots</span>
                  <input type="number" min={1} max={500} value={mCapVal} onChange={e => setMCapVal(+e.target.value)}
                    className="flex-1 text-sm focus:outline-none" />
                </div>
                <button onClick={addManualCapacity} className="w-full flex items-center justify-center gap-2 py-2 bg-gray-50 hover:bg-gray-100 text-gray-900 text-sm font-medium rounded-none border border-gray-200 transition-colors">
                  <Plus className="w-4 h-4" /> Add modification
                </button>
              </div>
            )}

            {manualTab === 'sequence' && (
              <div className="space-y-3">
                <select value={mSeqFrom} onChange={e => { setMSeqFrom(e.target.value); setMSeqTo('') }}
                  className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                  {learnings?.from_activities.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
                <select value={mSeqTo} onChange={e => setMSeqTo(e.target.value)}
                  className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                  <option value="">— select target —</option>
                  {seqToOptions.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
                {mSeqFrom && mSeqTo && (() => {
                  const row = learnings?.routing_probabilities.find(r => r.from_activity === mSeqFrom && r.to_activity === mSeqTo)
                  return row ? <p className="text-xs text-gray-500">Current: <span className="font-semibold">{(row.probability * 100).toFixed(1)}%</span></p> : null
                })()}
                <div className="flex items-center gap-1.5 border border-gray-200 rounded-none px-3 py-2">
                  <span className="text-xs text-gray-500 whitespace-nowrap">New probability</span>
                  <input type="number" min={0} max={100} step={1} value={mSeqVal} onChange={e => setMSeqVal(+e.target.value)}
                    className="flex-1 text-sm focus:outline-none" />
                  <span className="text-gray-400 text-sm">%</span>
                </div>
                {modConflictError && manualTab === 'sequence' && (
                  <p className="text-xs text-gray-800 bg-gray-50 rounded-none px-3 py-2">{modConflictError}</p>
                )}
                <button onClick={addManualSequence} disabled={!mSeqTo} className="w-full flex items-center justify-center gap-2 py-2 bg-gray-50 hover:bg-gray-100 text-gray-900 text-sm font-medium rounded-none border border-gray-200 transition-colors disabled:opacity-50">
                  <Plus className="w-4 h-4" /> Add modification
                </button>
              </div>
            )}

            {manualTab === 'rework' && (
              <div className="space-y-3">
                {!learnings?.rework_parameters.length ? (
                  <p className="text-sm text-gray-400 text-center py-4">No rework activities detected in this log.</p>
                ) : (
                  <>
                    <select value={mReworkAct} onChange={e => setMReworkAct(e.target.value)}
                      className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                      {learnings.rework_parameters.map(r => <option key={r.activity} value={r.activity}>{r.activity}</option>)}
                    </select>
                    {mReworkAct && (() => {
                      const row = learnings.rework_parameters.find(r => r.activity === mReworkAct)
                      return row ? (
                        <p className="text-xs text-gray-500">
                          Current: <span className="font-semibold">{(row.rework_rate_given_visit_pct ?? row.rework_rate_pct).toFixed(1)}%</span> rework rate · max <span className="font-semibold">{row.max_loops}</span> loop{row.max_loops !== 1 ? 's' : ''}
                        </p>
                      ) : null
                    })()}
                    <div className="grid grid-cols-2 gap-2">
                      <select value={mReworkDir} onChange={e => setMReworkDir(e.target.value as typeof mReworkDir)}
                        className="rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                        <option value="reduce_pct">Reduce by %</option>
                        <option value="increase_pct">Increase by %</option>
                      </select>
                      <div className="flex items-center gap-1.5 border border-gray-200 rounded-none px-3">
                        <input type="number" min={1} max={99} value={mReworkVal} onChange={e => setMReworkVal(+e.target.value)}
                          className="w-full text-sm focus:outline-none" />
                        <span className="text-gray-400 text-sm">%</span>
                      </div>
                    </div>
                    <button onClick={addManualRework} className="w-full flex items-center justify-center gap-2 py-2 bg-gray-50 hover:bg-gray-100 text-gray-900 text-sm font-medium rounded-none border border-gray-200 transition-colors">
                      <Plus className="w-4 h-4" /> Add modification
                    </button>
                  </>
                )}
              </div>
            )}

            {manualTab === 'max_visits' && (
              <div className="space-y-3">
                <select value={mMaxVisitAct} onChange={e => setMMaxVisitAct(e.target.value)}
                  className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                  {(learnings?.activities ?? []).map(a => <option key={a} value={a}>{a}</option>)}
                </select>
                {mMaxVisitAct && (() => {
                  const row = learnings?.rework_parameters.find(r => r.activity === mMaxVisitAct)
                  return (
                    <p className="text-xs text-gray-500">
                      {row
                        ? <>Current max: <span className="font-semibold">{row.max_loops + 1}</span> visit{row.max_loops + 1 !== 1 ? 's' : ''} · rework rate <span className="font-semibold">{(row.rework_rate_given_visit_pct ?? row.rework_rate_pct).toFixed(1)}%</span></>
                        : <>No rework observed — engine currently allows <span className="font-semibold">1</span> visit</>
                      }
                    </p>
                  )
                })()}
                <div className="flex items-center gap-2">
                  <label className="text-sm text-gray-600 shrink-0">Max visits per case:</label>
                  <input type="number" min={1} max={20} value={mMaxVisitCap} onChange={e => setMMaxVisitCap(Math.max(1, +e.target.value))}
                    className="w-20 rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600" />
                </div>
                <button
                  onClick={() => {
                    if (!mMaxVisitAct) return
                    const row = learnings?.rework_parameters.find(r => r.activity === mMaxVisitAct)
                    const oldCap = row ? row.max_loops + 1 : 1
                    addModification({
                      type: 'max_visits', activity: mMaxVisitAct,
                      change_type: 'set_value', value: mMaxVisitCap,
                      description: `'${mMaxVisitAct}' at most ${mMaxVisitCap} visit(s) per case`,
                      baseline_value: oldCap, modified_value: mMaxVisitCap,
                    })
                  }}
                  className="w-full flex items-center justify-center gap-2 py-2 bg-gray-50 hover:bg-gray-100 text-gray-900 text-sm font-medium rounded-none border border-gray-200 transition-colors">
                  <Plus className="w-4 h-4" /> Add modification
                </button>
              </div>
            )}

            {manualTab === 'parallel' && (
              <div className="space-y-3">
                {!learnings?.parallel_pairs?.length ? (
                  <p className="text-sm text-gray-400 text-center py-4">No parallel activity pairs detected. Requires both start_time and end_time columns.</p>
                ) : (
                  <>
                    <select value={mParActA} onChange={e => setMParActA(e.target.value)}
                      className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                      {[...new Set(learnings.parallel_pairs.map(p => p.activity_a))].map(a => <option key={a} value={a}>{a}</option>)}
                    </select>
                    <select value={mParActB} onChange={e => setMParActB(e.target.value)}
                      className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                      {learnings.parallel_pairs.filter(p => p.activity_a === mParActA || p.activity_b === mParActA)
                        .map(p => p.activity_a === mParActA ? p.activity_b : p.activity_a)
                        .map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                    {mParActA && mParActB && (() => {
                      const row = learnings.parallel_pairs?.find(
                        p => (p.activity_a === mParActA && p.activity_b === mParActB) ||
                             (p.activity_a === mParActB && p.activity_b === mParActA)
                      )
                      if (!row) return null
                      return (
                        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-gray-500 bg-gray-50 rounded-none px-3 py-2">
                          <span>Co-parallel rate: <span className="font-semibold text-gray-800">{row.a_parallel_with_b_pct.toFixed(0)}%</span></span>
                          <span>Avg overlap: <span className="font-semibold text-gray-800">{row.mean_overlap_min.toFixed(0)} min</span></span>
                        </div>
                      )
                    })()}
                    <div className="flex items-center gap-1.5 border border-gray-200 rounded-none px-3 py-2">
                      <span className="text-xs text-gray-500 whitespace-nowrap">Set co-parallel rate to</span>
                      <input type="number" min={0} max={100} value={mParVal} onChange={e => setMParVal(+e.target.value)}
                        className="flex-1 text-sm focus:outline-none" />
                      <span className="text-gray-400 text-sm">%</span>
                    </div>
                    <button onClick={addManualParallel} disabled={!mParActA || !mParActB}
                      className="w-full flex items-center justify-center gap-2 py-2 bg-gray-50 hover:bg-gray-100 text-gray-900 text-sm font-medium rounded-none border border-gray-200 transition-colors disabled:opacity-50">
                      <Plus className="w-4 h-4" /> Add modification
                    </button>
                  </>
                )}
              </div>
            )}

            {manualTab === 'case_attributes' && (
              <CaseAttributeDistributions
                sessionId={sessionId!}
                cab={learnings?.case_attribute_behaviour ?? {}}
                onUpdated={updated => setLearnings(prev => prev ? { ...prev, case_attribute_behaviour: updated } : prev)}
                onAddModification={addModification}
              />
            )}

            {manualTab === 'decision_point' && (
              <div className="space-y-3">
                {!learnings?.decision_points?.length ? (
                  <p className="text-sm text-gray-400 text-center py-4">No Decision points detected. Requires activities with ≥2 successors each carrying ≥5% probability.</p>
                ) : (
                  <>
                    {/* Gateway selector */}
                    <select value={mGateway} onChange={e => {
                      const gw = e.target.value
                      setMGateway(gw)
                      const branches: Record<string, number> = {}
                      learnings.decision_points.filter(g => g.gateway_activity === gw)
                        .forEach(g => { branches[g.branch_activity] = g.probability_pct })
                      setMGatewayBranches(branches)
                    }} className="w-full rounded-none border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                      {[...new Set(learnings.decision_points.map(g => g.gateway_activity))].map(gw => (
                        <option key={gw} value={gw}>{gw}</option>
                      ))}
                    </select>

                    {/* Branch probability editors */}
                    {mGateway && (() => {
                      const branches = learnings.decision_points.filter(g => g.gateway_activity === mGateway)
                      const total = Object.values(mGatewayBranches).reduce((s, v) => s + v, 0)
                      const others = Math.max(0, 100 - total)
                      const isValid = others >= 0 && total <= 100
                      return (
                        <div className="space-y-2">
                          {branches.map(b => (
                            <div key={b.branch_activity} className="flex items-center gap-2">
                              <span className="text-xs text-gray-600 flex-1 truncate">{b.branch_activity}</span>
                              <span className="text-[10px] text-gray-400 tabular-nums w-12 text-right">
                                was {b.probability_pct.toFixed(1)}%
                              </span>
                              <div className="flex items-center gap-1 border border-gray-200 rounded-none px-2 w-24">
                                <input
                                  type="number" min={0} max={100} step={5}
                                  value={mGatewayBranches[b.branch_activity] ?? b.probability_pct}
                                  onChange={e => setMGatewayBranches(prev => ({
                                    ...prev, [b.branch_activity]: +e.target.value
                                  }))}
                                  className="w-full text-sm focus:outline-none py-1.5"
                                />
                                <span className="text-gray-400 text-xs">%</span>
                              </div>
                            </div>
                          ))}
                          {/* Read-only "others" row — absorbs minor branches (<5%) */}
                          <div className="flex items-center gap-2 opacity-60">
                            <span className="text-xs text-gray-500 flex-1 italic">others (minor branches)</span>
                            <span className="text-[10px] text-gray-400 tabular-nums w-12 text-right" />
                            <div className="flex items-center gap-1 border border-gray-100 bg-gray-50 rounded-none px-2 w-24">
                              <span className="w-full text-sm py-1.5 text-gray-500 tabular-nums">
                                {others.toFixed(1)}
                              </span>
                              <span className="text-gray-400 text-xs">%</span>
                            </div>
                          </div>
                          <div className={`text-xs px-2 py-1 rounded-none ${isValid ? 'text-gray-800 bg-gray-50' : 'text-gray-800 bg-gray-50'}`}>
                            Named branches: {total.toFixed(1)}% · Others: {others.toFixed(1)}%
                            {!isValid && ' — named branches exceed 100%'}
                          </div>
                        </div>
                      )
                    })()}

                    {modConflictError && manualTab === 'decision_point' && (
                      <p className="text-xs text-gray-800 bg-gray-50 rounded-none px-3 py-2">{modConflictError}</p>
                    )}
                    <button onClick={addManualGateway} disabled={!mGateway}
                      className="w-full flex items-center justify-center gap-2 py-2 bg-gray-50 hover:bg-gray-100 text-gray-900 text-sm font-medium rounded-none border border-gray-200 transition-colors disabled:opacity-50">
                      <Plus className="w-4 h-4" /> Add modification
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </Section>

        {/* Active modifications */}
        <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <div className="flex items-center gap-2">
              <Sliders className="w-4 h-4 text-gray-400" />
              <span className="font-medium text-sm text-gray-700">Active Modifications</span>
              <span className="text-xs bg-gray-100 text-gray-900 px-2 py-0.5 rounded-none font-medium">{modifications.length}</span>
            </div>
            {modifications.length > 0 && (
              <button onClick={() => clearModifications()} className="text-xs text-gray-400 hover:text-gray-600 transition-colors">Clear all</button>
            )}
          </div>

          {/* Constraint cards */}
          {constraints.length > 0 && (
            <div className="divide-y divide-gray-50 border-b border-gray-100">
              {constraints.map((con, idx) => (
                <div key={idx} className="flex items-center gap-3 px-4 py-2.5 bg-gray-50">
                  <Lock className="shrink-0 w-3.5 h-3.5 text-gray-600" />
                  <span className="shrink-0 text-xs font-medium px-2 py-0.5 rounded-none bg-gray-100 text-gray-900">Constraint</span>
                  <p className="flex-1 text-xs text-gray-900 truncate">{con.text}</p>
                  <button onClick={() => removeConstraint(idx)} className="shrink-0 text-gray-300 hover:text-gray-600 transition-colors">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
          {modifications.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-gray-400">
              <Plus className="w-5 h-5 mx-auto mb-2 text-gray-300" />
              No modifications yet
            </div>
          ) : (
            <div className="divide-y divide-gray-50 max-h-64 overflow-y-auto">
              {modifications.map((mod, idx) => (
                <div key={idx} className="flex items-center gap-3 px-4 py-2.5">
                  <span className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-none ${MOD_TYPE_COLORS[mod.type] || 'bg-gray-100 text-gray-600'}`}>
                    {mod.type.replace('_', ' ')}
                  </span>
                  <p className="flex-1 text-xs text-gray-700 truncate">{mod.description}</p>
                  <button onClick={() => removeModification(idx)} className="shrink-0 text-gray-300 hover:text-gray-600 transition-colors">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
          {modifications.length > 0 && (autoAdjust.warnings.length > 0 || autoAdjust.status.length > 0) && (
            <div className={`mx-3 mb-3 mt-1 flex items-start gap-2 px-3 py-2 rounded-none text-xs border ${
              autoAdjust.warnings.length > 0
                ? 'bg-gray-50 border-gray-200 text-gray-900'
                : 'bg-gray-50 border-gray-200 text-gray-900'
            }`}>
              <Info className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${autoAdjust.warnings.length > 0 ? 'text-gray-600' : 'text-gray-400'}`} />
              <div className="space-y-0.5">
                {autoAdjust.warnings.map((msg, i) => <p key={i}>{msg}</p>)}
                {autoAdjust.status.map((msg, i) => <p key={`s${i}`}>{msg}</p>)}
                {autoAdjust.warnings.length > 0 && baselineResult && (
                  <p className="mt-0.5 opacity-80">Baseline will be re-run for a fair comparison.</p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
