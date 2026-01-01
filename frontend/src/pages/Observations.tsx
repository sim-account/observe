import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSession } from '@/store/sessionStore'
import { getObservationsRules, getCaseAttributeObservations, type ObservationsRules, type RuleItem, type CaseAttributeObservation, type ConditionalParallelRule } from '@/api/client'
import { Loader2, Info } from 'lucide-react'
import Paginator, { usePagination } from '@/components/Paginator'

const PAGE = 7

type TabId = 'time' | 'resource' | 'control_flow' | 'rework' | 'case_attributes'

const TABS: { id: TabId; label: string; tooltip: string }[] = [
  { id: 'time', label: '⏱ Time', tooltip: 'Conditions that cause an activity\'s duration to deviate significantly from its average — rework history, resource speed gaps, or predecessor effects.' },
  { id: 'resource', label: '👤 Resource', tooltip: 'Patterns where a specific predecessor activity systematically determines which resource handles the next step, and resource capacity constraints that create queues.' },
  { id: 'control_flow', label: '🔀 Control Flow', tooltip: 'Structural patterns extracted from case flows (gates, implications, exclusions) and concurrently-executing activities detected when timestamps overlap.' },
  { id: 'rework', label: '🔄 Rework', tooltip: 'Conditions that raise or lower the probability that an activity is repeated — triggered by a predecessor or by which resource performed it.' },
  { id: 'case_attributes', label: '🏷 Case Attributes', tooltip: 'How case-level attributes (e.g. customer tier, region, type) influence cycle time, rework rate, or the likelihood that specific activities occur.' },
]

const CONFIDENCE_TOOLTIP = `Statistical confidence based on p-value:
p ≤ 0.0001 → Exceptional confidence (1-in-10,000 chance this pattern is random)
p ≤ 0.001  → Very high confidence (1-in-1,000)
p ≤ 0.01   → High confidence (1-in-100)
p ≤ 0.05   → Moderate confidence (1-in-20 — the standard threshold for statistical significance)`

export function confidenceFromPValue(pVal: number): { label: string; color: string } {
  if (pVal <= 0.0001) return { label: 'Exceptional confidence', color: 'text-gray-900 bg-gray-50' }
  if (pVal <= 0.001)  return { label: 'Very high confidence',   color: 'text-gray-900 bg-gray-50' }
  if (pVal <= 0.01)   return { label: 'High confidence',        color: 'text-gray-900 bg-gray-50' }
  return                     { label: 'Moderate confidence',    color: 'text-gray-600 bg-gray-100' }
}

export function extractPValue(text: string | undefined): number | null {
  if (!text) return null
  const m = text.match(/p=([0-9.e+-]+)/)
  return m ? parseFloat(m[1]) : null
}

export function ConfidenceBadge({ ruleText }: { ruleText?: string }) {
  const pVal = extractPValue(ruleText)
  if (pVal === null) return null
  const { label, color } = confidenceFromPValue(pVal)
  return (
    <span className={`inline-flex items-center gap-0.5 shrink-0 text-xs font-medium px-2 py-0.5 rounded-none ${color}`}>
      {label}
      <span className="relative group inline-flex items-center ml-0.5">
        <Info className="w-3 h-3 opacity-60 cursor-help" />
        <span className="absolute top-full mt-1.5 left-1/2 -translate-x-1/2 hidden group-hover:block w-64 bg-gray-900 text-white text-xs rounded-none px-3 py-2 leading-relaxed shadow-lg z-50 pointer-events-none whitespace-pre-line">
          {CONFIDENCE_TOOLTIP}
        </span>
      </span>
    </span>
  )
}

const SEVERITY_STYLES: Record<string, { bg: string; badge: string; text: string }> = {
  red: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900', text: 'text-gray-800' },
  orange: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900', text: 'text-gray-800' },
  green: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900', text: 'text-gray-800' },
  blue: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900', text: 'text-gray-800' },
}

const SEQ_STYLES: Record<string, { bg: string; badge: string }> = {
  gate: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900' },
  exclusion: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900' },
}

const SEQ_BADGE: Record<string, string> = {
  gate: 'GATE',
  exclusion: 'EXCLUDES',
}

export function InfoTooltip({ text }: { text: string }) {
  return (
    <span className="relative group inline-flex items-center ml-1">
      <Info className="w-3 h-3 text-gray-400 cursor-help" />
      <span className="absolute top-full mt-1.5 hidden group-hover:block w-64 bg-gray-800 text-white text-xs rounded-none px-3 py-2 leading-relaxed shadow-lg z-50 pointer-events-none whitespace-normal text-left"
        style={{ left: '50%', transform: 'translateX(-50%)', maxWidth: 'min(16rem, calc(100vw - 2rem))' }}>
        {text}
      </span>
    </span>
  )
}

function RuleCard({ rule, badge, badgeClass, bg, meta }: {
  rule: string; badge: string; badgeClass: string; bg: string; meta: string
}) {
  return (
    <div className={`rounded-none border p-4 ${bg}`}>
      <div className="flex items-start gap-3">
        <div className="flex flex-col gap-1 shrink-0 mt-0.5">
          <span className={`text-xs font-semibold px-2.5 py-1 rounded-none ${badgeClass}`}>{badge}</span>
          <ConfidenceBadge ruleText={rule} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 leading-snug">{rule}</p>
          <p className="text-xs text-gray-400 mt-1.5">{meta}</p>
        </div>
      </div>
    </div>
  )
}

function FilterBar({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: string[]
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium text-gray-500">{label}</span>
      <select value={value} onChange={e => onChange(e.target.value)}
        className="border border-gray-200 rounded-none px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
        <option value="">All</option>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}

function MetricChip({ label, value, color = 'gray', tooltip }: { label: string; value: number; color?: string; tooltip?: string }) {
  const styles: Record<string, string> = { gray: 'bg-gray-100 text-gray-700', blue: 'bg-gray-100 text-gray-900', green: 'bg-gray-100 text-gray-900', red: 'bg-gray-100 text-gray-900', orange: 'bg-gray-100 text-gray-900' }
  return (
    <div className={`flex items-center gap-2 px-4 py-2.5 rounded-none ${styles[color as keyof typeof styles] ?? styles.gray}`}>
      <span className="text-xl font-bold">{value}</span>
      <span className="text-xs font-medium leading-tight flex items-center">
        {label}
        {tooltip && <InfoTooltip text={tooltip} />}
      </span>
    </div>
  )
}

function impactScore(ratio: number | null | undefined): number {
  if (ratio == null || ratio <= 0) return 0
  return ratio >= 1 ? ratio : 1 / ratio
}

/** Extract the multiplier from rule_text when ratio field is 0 or invalid.
 * Matches patterns like "492.8x faster", "84.4x more", "1000.0x lower" */
function extractMultiplierFromText(text: string | undefined): number | null {
  if (!text) return null
  // Matches ASCII x: (492.8x faster, (1000.0x shorter, (84.4x more likely, etc.
  const m = text.match(/\((\d+(?:\.\d+)?)x\s+(?:faster|slower|shorter|longer|more|less|higher|lower)/)
  if (m) return parseFloat(m[1])
  // Matches Unicode ×: 2.1× longer, 3.4× higher (from case_attribute_extractor)
  const m2 = text.match(/(\d+(?:\.\d+)?)×\s+(?:faster|slower|shorter|longer|more|less|higher|lower)/)
  return m2 ? parseFloat(m2[1]) : null
}

/** Always show ratio ≥ 1 — fall back to multiplier embedded in rule_text for ratio=0 cases. */
function fmtRatio(ratio: number | null | undefined, ruleText?: string): string {
  if (ratio != null && ratio > 0) {
    const eff = ratio >= 1 ? ratio : 1 / ratio
    return `${eff.toFixed(1)}×`
  }
  const fromText = extractMultiplierFromText(ruleText)
  if (fromText != null && fromText > 0) return `${fromText.toFixed(1)}×`
  return '—'
}

function withImpactFilter<T extends { ratio?: number | null }>(rules: T[], top = 5): { rules: T[]; isFallback: boolean } {
  const high = rules.filter(r => r.ratio != null && (r.ratio >= 2.0 || r.ratio <= 0.5))
  if (high.length > 0) return { rules: high, isFallback: false }
  const sorted = [...rules]
    .filter(r => r.ratio != null)
    .sort((a, b) => impactScore(b.ratio) - impactScore(a.ratio))
    .slice(0, top)
  return { rules: sorted, isFallback: sorted.length < rules.filter(r => r.ratio != null).length }
}

function withSeqFilter(rules: RuleItem[]): { rules: RuleItem[]; level: 'primary' | 'fallback' | 'top5' } {
  const follows = rules.filter(r => r.rule_type === 'gate')
  const exclusions = rules.filter(r => r.rule_type === 'exclusion')

  const primary = [
    ...follows.filter(r => (r.p_eventually ?? 0) >= 0.95),
    ...exclusions.filter(r => (r.p_eventually ?? 1) <= 0.05),
  ]
  if (primary.length > 0) return { rules: primary, level: 'primary' }

  const fallback = [
    ...follows.filter(r => (r.p_eventually ?? 0) >= 0.90),
    ...exclusions.filter(r => (r.p_eventually ?? 1) <= 0.10),
  ]
  if (fallback.length > 0) return { rules: fallback, level: 'fallback' }

  const top5 = [
    ...[...follows].sort((a, b) => (b.p_eventually ?? 0) - (a.p_eventually ?? 0)).slice(0, 5),
    ...[...exclusions].sort((a, b) => (a.p_eventually ?? 1) - (b.p_eventually ?? 1)).slice(0, 5),
  ]
  return { rules: top5, level: 'top5' }
}


function FallbackBanner() {
  return (
    <p className="text-xs text-gray-800 bg-gray-50 border border-gray-100 rounded-none px-3 py-2">
      No strong patterns found — showing top 5 by impact magnitude.
    </p>
  )
}

export default function Observations() {
  const { sessionId, reset } = useSession()
  const navigate = useNavigate()
  const [data, setData] = useState<ObservationsRules | null>(null)
  const [caObservations, setCaObservations] = useState<CaseAttributeObservation[]>([])
  const [caAttributes, setCaAttributes] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<TabId>('time')
  const [cfSub, setCfSub] = useState<'sequence' | 'parallel' | 'conditional_routing'>('sequence')

  const [timePage, setTimePage] = useState(0)
  const [reworkPage, setReworkPage] = useState(0)
  const [routingPage, setRoutingPage] = useState(0)
  const [condRoutingPage, setCondRoutingPage] = useState(0)
  const [condRoutingGw, setCondRoutingGw] = useState('')
  const [condRoutingType, setCondRoutingType] = useState('')
  const [seqPage, setSeqPage] = useState(0)
  const [parPage, setParPage] = useState(0)
  const [caPage, setCaPage] = useState(0)

  // Case attribute filters
  const [caAttrFilter, setCaAttrFilter] = useState('')
  const [caSubTypeFilter, setCaSubTypeFilter] = useState('')

  const [seqType, setSeqType] = useState('')
  const [seqAct, setSeqAct] = useState('')
  const [timeType, setTimeType] = useState('')
  const [timeAct, setTimeAct] = useState('')
  const [reworkType, setReworkType] = useState('')
  const [reworkAct, setReworkAct] = useState('')
  const [routingAct, setRoutingAct] = useState('')
  const [routingPred, setRoutingPred] = useState('')
  const [parallelAct, setParallelAct] = useState('')
  const [parallelType, setParallelType] = useState('')

  useEffect(() => {
    if (!sessionId) return
    getObservationsRules(sessionId)
      .then(rules => { setData(rules); setLoading(false) })
      .catch((err) => {
        const detail = err?.response?.data?.detail ?? err?.message ?? 'Unknown error'
        setError(detail)
        setLoading(false)
      })
    getCaseAttributeObservations(sessionId)
      .then(ca => { setCaObservations(ca.observations); setCaAttributes(ca.attributes) })
      .catch(() => {})
  }, [sessionId])

  useEffect(() => { setTimePage(0) }, [timeType, timeAct, activeTab])
  useEffect(() => { setReworkPage(0) }, [reworkType, reworkAct, activeTab])
  useEffect(() => { setRoutingPage(0) }, [routingAct, routingPred, activeTab])
  useEffect(() => { setCondRoutingPage(0) }, [condRoutingGw, condRoutingType, activeTab])
  useEffect(() => { setSeqPage(0) }, [seqType, seqAct, cfSub, activeTab])
  useEffect(() => { setParPage(0) }, [parallelAct, parallelType, cfSub, activeTab])
  useEffect(() => { setCaPage(0) }, [caAttrFilter, caSubTypeFilter, activeTab])

  const impactSeq = useMemo(() => withSeqFilter(data?.sequence_rules ?? []), [data])
  const impactTime = useMemo(() => withImpactFilter(data?.time_rules ?? []), [data])
  const impactRework = useMemo(() => withImpactFilter(data?.rework_rules ?? []), [data])
  const impactRouting = useMemo(() => withImpactFilter(data?.routing_rules ?? []), [data])
  const impactParallel = useMemo(() => {
    const always = (data?.parallel_rules ?? []).filter(r => r.rule_type === 'always_parallel')
    const cond = (data?.parallel_rules ?? []).filter(r => r.rule_type !== 'always_parallel' && r.rule_type !== 'case_attr_conditional')
    const { rules: filtCond, isFallback } = withImpactFilter(cond)
    return { rules: [...always, ...filtCond], isFallback }
  }, [data])
  const impactCa = useMemo(() => withImpactFilter(caObservations), [caObservations])
  const caParallelRules = useMemo(
    () => (data?.parallel_rules ?? []).filter(r => r.rule_type === 'case_attr_conditional'),
    [data]
  )
  const impactCaParallel = useMemo(() => withImpactFilter(caParallelRules), [caParallelRules])
  const caAllAttributes = useMemo(() => {
    const parAttrs = caParallelRules
      .map(r => r.condition_type?.replace(/^case_attr:/, '') ?? null)
      .filter(Boolean) as string[]
    return [...new Set([...caAttributes, ...parAttrs])].sort()
  }, [caAttributes, caParallelRules])

  const filteredSeq = useMemo(() => {
    return impactSeq.rules.filter(r =>
      (!seqType || r.rule_type === seqType) && (!seqAct || r.activity_a === seqAct)
    )
  }, [impactSeq, seqType, seqAct])

  const filteredTime = useMemo(() => {
    return impactTime.rules.filter(r =>
      (!timeType || r.condition_type === timeType) && (!timeAct || r.activity === timeAct)
    )
  }, [impactTime, timeType, timeAct])

  const filteredRework = useMemo(() => {
    return impactRework.rules.filter(r =>
      (!reworkType || r.condition_type === reworkType) && (!reworkAct || r.activity === reworkAct)
    )
  }, [impactRework, reworkType, reworkAct])

  const filteredRouting = useMemo(() => {
    return impactRouting.rules.filter(r =>
      (!routingAct || r.activity === routingAct) && (!routingPred || r.predecessor === routingPred)
    )
  }, [impactRouting, routingAct, routingPred])

  const filteredParallel = useMemo(() => {
    return impactParallel.rules.filter(r =>
      (!parallelAct || r.activity === parallelAct) && (!parallelType || r.rule_type === parallelType)
    )
  }, [impactParallel, parallelAct, parallelType])

  if (loading) return (
    <div className="flex items-center justify-center h-96 text-gray-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading process behavior observations…
    </div>
  )
  if (error || !data) return (
    <div className="p-8 text-center text-gray-400">
      <p className="font-medium text-gray-500 mb-1">{error && error.includes('Session') ? 'Session not found' : 'Failed to load process behavior observations'}</p>
      <p className="text-sm mb-2">{error ? error : 'The backend session has expired or was reset.'}</p>
      <button
        onClick={() => { reset(); navigate('/upload') }}
        className="px-4 py-2 bg-gray-800 text-white text-sm font-medium rounded-none hover:bg-gray-900 transition-colors">
        Re-upload event log
      </button>
    </div>
  )

  const { slice: timeSlice } = usePagination(filteredTime, timePage, PAGE)
  const { slice: reworkSlice } = usePagination(filteredRework, reworkPage, PAGE)
  const { slice: routingSlice } = usePagination(filteredRouting, routingPage, PAGE)
  const { slice: seqSlice } = usePagination(filteredSeq, seqPage, PAGE)
  const { slice: parSlice } = usePagination(filteredParallel, parPage, PAGE)

  return (
    <div className="p-6">
      {/* Body: vertical tab column on the left + content on the right */}
      <div className="flex gap-6">
        {/* Sub-tab nav (vertical) */}
        <div className="shrink-0 w-56 self-start sticky top-4">
          <div className="flex flex-col gap-1 bg-white p-1 rounded-none border border-gray-200">
            {TABS.map(t => (
              <button key={t.id} onClick={() => setActiveTab(t.id)}
                className={`flex items-center justify-between gap-2 text-left px-3 py-2 rounded-none text-sm font-medium transition-colors whitespace-nowrap ${activeTab === t.id ? 'bg-gray-800 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-100'}`}>
                <span className="inline-flex items-center gap-1 whitespace-nowrap">
                  {t.label}
                  <InfoTooltip text={t.tooltip} />
                </span>
                <span className={`shrink-0 text-xs px-1.5 py-0.5 rounded-none ${activeTab === t.id ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-500'}`}>
                  {t.id === 'case_attributes'
                    ? caObservations.length + caParallelRules.length
                    : t.id === 'control_flow'
                      ? (data?.counts.sequence ?? 0) + (data?.counts.parallel ?? 0) + (data?.counts.conditional_routing ?? 0)
                      : t.id === 'resource'
                        ? data?.counts.routing ?? 0
                        : data?.counts[t.id as 'time' | 'rework'] ?? 0
                  }
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Content column */}
        <div className="flex-1 min-w-0 space-y-6">

      {/* ── CONTROL FLOW ── */}
      {activeTab === 'control_flow' && (
        <div className="space-y-4">
          {/* Sub-category selector */}
          <div className="flex items-center gap-2">
            <select value={cfSub} onChange={e => { setCfSub(e.target.value as typeof cfSub) }}
              className="border border-gray-200 rounded-none px-3 py-1.5 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-gray-600 bg-white">
              <option value="sequence">🔒 Sequence — gates &amp; exclusions ({data.sequence_rules.length})</option>
              <option value="parallel">⇌ Parallel — concurrent execution ({data.parallel_rules?.length ?? 0})</option>
              <option value="conditional_routing">🔀 Conditional Routing — how predecessor/resource shifts branches ({data.conditional_routing_rules?.length ?? 0})</option>
            </select>
          </div>

          {/* ── Sequence ── */}
          {cfSub === 'sequence' && (
            <div className="space-y-4">
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex gap-2">
                  <MetricChip label="Gate rules" value={data.sequence_rules.filter(r => r.rule_type === 'gate').length} color="green"
                    tooltip="A gate is a conditional branch: this activity only occurs in a subset of cases. Not a loop — cases either contain it or skip it entirely." />
                  <MetricChip label="Exclusion" value={data.sequence_rules.filter(r => r.rule_type === 'exclusion').length} color="red"
                    tooltip="Activities A and B rarely appear together in the same case — they represent mutually exclusive process paths." />
                </div>
                <div className="ml-auto flex items-center gap-3">
                  <FilterBar label="Type" value={seqType} onChange={setSeqType} options={data.filters.sequence_types} />
                  <FilterBar label="Activity A" value={seqAct} onChange={setSeqAct} options={data.filters.sequence_activities} />
                </div>
              </div>
              {filteredSeq.length === 0 ? (
                <div className="bg-gray-50 rounded-none p-8 text-center text-gray-400">No sequence rules match the current filter.</div>
              ) : (
                <>
                  {impactSeq.level === 'fallback' && (
                    <p className="text-xs text-gray-800 bg-gray-50 border border-gray-100 rounded-none px-3 py-2">
                      No rules exceed 95% / 5% threshold — showing rules ≥90% follow rate or ≤10% exclusion rate.
                    </p>
                  )}
                  {impactSeq.level === 'top5' && (
                    <p className="text-xs text-gray-800 bg-gray-50 border border-gray-100 rounded-none px-3 py-2">
                      No strong patterns detected — showing top 5 by probability for each category.
                    </p>
                  )}
                  <div className="space-y-2">
                    {seqSlice.map((r, i) => {
                      const style = SEQ_STYLES[r.rule_type ?? ''] ?? SEVERITY_STYLES.blue
                      return (
                        <RuleCard key={i} rule={r.rule_text}
                          badge={SEQ_BADGE[r.rule_type ?? ''] ?? r.rule_type?.toUpperCase() ?? ''}
                          badgeClass={style.badge} bg={`${style.bg} border`}
                          meta={`Observed in ${r.n_cases_b_follows?.toLocaleString()} of ${r.n_cases_a?.toLocaleString()} cases containing A · p_eventually=${((r.p_eventually ?? 0) * 100).toFixed(0)}%`}
                        />
                      )
                    })}
                  </div>
                  <Paginator total={filteredSeq.length} page={seqPage} pageSize={PAGE} onPage={setSeqPage} />
                </>
              )}
            </div>
          )}

          {/* ── Parallel ── */}
          {cfSub === 'parallel' && (
            <div className="space-y-4">
              {filteredParallel.length === 0 && (!data?.parallel_rules || data.parallel_rules.length === 0) ? (
                <div className="bg-gray-50 border border-gray-100 rounded-none p-10 text-center">
                  <p className="text-sm font-semibold text-gray-900 mb-1">No conditional parallel patterns detected</p>
                  <p className="text-xs text-gray-400 max-w-sm mx-auto">
                    Requires start_time and end_time columns and a minimum of 10 occurrences per condition group.
                  </p>
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-3 flex-wrap">
                    <div className="flex gap-2 flex-wrap">
                      {([
                        { type: 'always_parallel', label: 'Always together', color: 'orange' as const, tooltip: 'Activity pairs where both sides have ≥80% parallel rate — they are structurally co-scheduled in nearly every case.' },
                        { type: 'resource_conditional', label: 'Resource-driven', color: 'blue' as const, tooltip: 'The parallel rate of an activity changes significantly depending on which resource handles it.' },
                        { type: 'predecessor_conditional', label: 'Predecessor-driven', color: 'blue' as const, tooltip: 'The parallel rate of an activity changes significantly based on which activity preceded it.' },
                      ] as const).map(({ type, label, color, tooltip }) => {
                        const n = data?.parallel_rules?.filter(r => r.rule_type === type).length ?? 0
                        if (!n) return null
                        return <MetricChip key={type} label={label} value={n} color={color} tooltip={tooltip} />
                      })}
                    </div>
                    <div className="ml-auto flex items-center gap-3">
                      <FilterBar label="Activity" value={parallelAct} onChange={setParallelAct} options={data?.filters?.parallel_activities ?? []} />
                      <FilterBar label="Type" value={parallelType} onChange={setParallelType}
                        options={['always_parallel', 'resource_conditional', 'predecessor_conditional']} />
                    </div>
                  </div>
                  {filteredParallel.length === 0 ? (
                    <div className="bg-gray-50 rounded-none p-8 text-center text-gray-400">No parallel rules match the current filter.</div>
                  ) : (
                    <>
                      {impactParallel.isFallback && <FallbackBanner />}
                      <div className="space-y-2">
                        {parSlice.map((r, i) => {
                          const sev = r.severity ?? 'blue'
                          const s = SEVERITY_STYLES[sev] ?? SEVERITY_STYLES.blue
                          const meta = r.baseline_rate_pct != null
                            ? `n=${r.count.toLocaleString()} · Baseline: ${r.baseline_rate_pct.toFixed(0)}% parallel → Conditional: ${r.parallel_rate_pct.toFixed(0)}%`
                            : `n=${r.count.toLocaleString()} · ${r.parallel_rate_pct.toFixed(0)}% co-occurrence rate`
                          return (
                            <RuleCard key={i} rule={r.rule_text}
                              badge={r.ratio != null ? `${r.badge} · ${fmtRatio(r.ratio, r.rule_text)}` : r.badge}
                              badgeClass={s.badge} bg={`${s.bg} border`}
                              meta={meta} />
                          )
                        })}
                      </div>
                      <Paginator total={filteredParallel.length} page={parPage} pageSize={PAGE} onPage={setParPage} />
                    </>
                  )}
                </>
              )}
            </div>
          )}

          {/* ── Conditional Routing sub-view ── */}
          {cfSub === 'conditional_routing' && (
            <div className="space-y-4">
              {!data.conditional_routing_rules?.length ? (
                <div className="bg-gray-50 rounded-none p-8 text-center text-gray-400">No conditional routing patterns found. Requires a statistically significant difference (p &lt; 0.05) in resource assignment and ≥5 cases per condition group.</div>
              ) : (
                <>
                  <div className="flex gap-2 flex-wrap">
                    <MetricChip label="Predecessor-triggered" value={data.conditional_routing_rules.filter(r => r.condition_type === 'predecessor').length} color="blue"
                      tooltip="A preceding activity systematically shifts branch probabilities at this decision point above baseline." />
                    <MetricChip label="Resource-triggered" value={data.conditional_routing_rules.filter(r => r.condition_type === 'resource').length} color="orange"
                      tooltip="Which resource performs this activity significantly shifts which downstream branch is taken." />
                  </div>
                  <div className="space-y-2">
                    {data.conditional_routing_rules.map((r, i) => {
                      const sev = r.severity ?? 'orange'
                      const s = SEVERITY_STYLES[sev] ?? SEVERITY_STYLES.orange
                      const eff = r.ratio >= 1 ? r.ratio : 1 / r.ratio
                      const condLabel = r.condition_type === 'predecessor' ? 'PREDECESSOR' : 'RESOURCE'
                      return (
                        <RuleCard key={i} rule={r.rule_text}
                          badge={`${condLabel} · ${eff.toFixed(1)}×`}
                          badgeClass={s.badge} bg={`${s.bg} border`}
                          meta={`n=${r.count?.toLocaleString()} · Baseline: ${r.baseline_pct?.toFixed(0)}% → Conditional: ${r.conditional_pct?.toFixed(0)}%`}
                        />
                      )
                    })}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── TIME ── */}
      {activeTab === 'time' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex gap-2">
              <MetricChip label="🔴 Slower conditions" value={data.time_rules.filter(r => (r.ratio ?? 1) > 1).length} color="red"
                tooltip="Conditions that make this activity take significantly longer than baseline — rework history, slow resources, or specific predecessors." />
              <MetricChip label="🟢 Faster conditions" value={data.time_rules.filter(r => (r.ratio ?? 1) < 1).length} color="green"
                tooltip="Conditions that make this activity run faster than baseline — fast paths worth promoting or protecting." />
            </div>
            <div className="ml-auto flex items-center gap-3">
              <FilterBar label="Condition" value={timeType} onChange={setTimeType} options={data.filters.time_condition_types} />
              <FilterBar label="Activity" value={timeAct} onChange={setTimeAct} options={data.filters.time_activities} />
            </div>
          </div>
          {filteredTime.length === 0 ? (
            <div className="bg-gray-50 rounded-none p-8 text-center text-gray-400">No time rules match the current filter.</div>
          ) : (
            <>
              {impactTime.isFallback && <FallbackBanner />}
              <div className="space-y-2">
                {timeSlice.map((r, i) => {
                  const isPos = r.direction === 'positive' || (r.ratio != null && r.ratio < 1)
                  const sev = isPos ? 'green' : (r.ratio != null ? (r.ratio >= 3 ? 'red' : r.ratio >= 1.5 ? 'orange' : 'blue') : 'blue')
                  const s = SEVERITY_STYLES[sev]
                  const badgeLabel = r.badge ?? (isPos
                    ? { rework_in_case: 'LOW REWORK CASE', resource_speed_gap: 'SPEED ADVANTAGE', preceded_by: 'FAST PATH' }[r.condition_type ?? ''] ?? 'FAST'
                    : { rework_in_case: 'REWORK PENALTY', resource_speed_gap: 'RESOURCE GAP', preceded_by: 'SLOW PATH' }[r.condition_type ?? ''] ?? 'SLOW')
                  return (
                    <RuleCard key={i} rule={r.rule_text}
                      badge={`${badgeLabel} · ${fmtRatio(r.ratio, r.rule_text)}`}
                      badgeClass={s.badge} bg={`${s.bg} border`}
                      meta={`n=${r.count?.toLocaleString()} · Baseline: ${r.baseline_mean_min?.toFixed(0)} min → Conditional: ${r.conditional_mean_min?.toFixed(0)} min`}
                    />
                  )
                })}
              </div>
              <Paginator total={filteredTime.length} page={timePage} pageSize={PAGE} onPage={setTimePage} />
            </>
          )}
        </div>
      )}

      {/* ── REWORK ── */}
      {activeTab === 'rework' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex gap-2">
              <MetricChip label="🔴 More rework" value={data.rework_rules.filter(r => (r.ratio ?? 1) > 1).length} color="red"
                tooltip="Conditions that significantly raise the rework rate above baseline — problematic predecessors or high-error resources." />
              <MetricChip label="🟢 Less rework" value={data.rework_rules.filter(r => (r.ratio ?? 1) < 1).length} color="green"
                tooltip="Conditions that significantly lower the rework rate — best-practice paths or high-quality resources to learn from." />
            </div>
            <div className="ml-auto flex items-center gap-3">
              <FilterBar label="Condition" value={reworkType} onChange={setReworkType} options={data.filters.rework_condition_types} />
              <FilterBar label="Activity" value={reworkAct} onChange={setReworkAct} options={data.filters.rework_activities} />
            </div>
          </div>
          {filteredRework.length === 0 ? (
            <div className="bg-gray-50 rounded-none p-8 text-center text-gray-400">No rework rules match the current filter.</div>
          ) : (
            <>
              {impactRework.isFallback && <FallbackBanner />}
              <div className="space-y-2">
                {reworkSlice.map((r, i) => {
                  const isPos = r.direction === 'positive' || (r.ratio != null && r.ratio < 1)
                  const sev = isPos ? 'green' : (r.ratio != null ? (r.ratio >= 3 ? 'red' : r.ratio >= 1.5 ? 'orange' : 'blue') : 'blue')
                  const s = SEVERITY_STYLES[sev]
                  const badgeLabel = r.badge ?? (isPos
                    ? { preceded_by: 'LOW REWORK PATH', resource: 'LOW REWORK' }[r.condition_type ?? ''] ?? 'LOWER REWORK'
                    : { preceded_by: 'HIGH REWORK PATH', resource: 'HIGH REWORK' }[r.condition_type ?? ''] ?? 'HIGHER REWORK')
                  return (
                    <RuleCard key={i} rule={r.rule_text}
                      badge={`${badgeLabel} · ${fmtRatio(r.ratio, r.rule_text)}`}
                      badgeClass={s.badge} bg={`${s.bg} border`}
                      meta={`n=${r.count?.toLocaleString()} · Baseline: ${r.baseline_rework_rate_pct?.toFixed(0)}% → Conditional: ${r.conditional_rework_rate_pct?.toFixed(0)}%`}
                    />
                  )
                })}
              </div>
              <Paginator total={filteredRework.length} page={reworkPage} pageSize={PAGE} onPage={setReworkPage} />
            </>
          )}
        </div>
      )}

      {/* ── RESOURCE ── */}
      {activeTab === 'resource' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex gap-2">
              <MetricChip label="Strong routing (≥3×)" value={data.routing_rules.filter(r => (r.ratio ?? 0) >= 3).length} color="red"
                tooltip="After a specific predecessor, this resource handles the next activity at least 3× more than their baseline share — a strong hidden routing rule." />
              <MetricChip label="Moderate (1.5–3×)" value={data.routing_rules.filter(r => (r.ratio ?? 0) >= 1.5 && (r.ratio ?? 0) < 3).length} color="orange"
                tooltip="After a specific predecessor, this resource handles the next activity 1.5–3× more than their baseline share." />
            </div>
            <div className="ml-auto flex items-center gap-3">
              <FilterBar label="Predecessor" value={routingPred} onChange={setRoutingPred} options={data.filters.routing_predecessors} />
              <FilterBar label="Activity" value={routingAct} onChange={setRoutingAct} options={data.filters.routing_activities} />
            </div>
          </div>
          {filteredRouting.length === 0 ? (
            <div className="bg-gray-50 rounded-none p-8 text-center text-gray-400">No resource routing rules match the current filter.</div>
          ) : (
            <>
              {impactRouting.isFallback && <FallbackBanner />}
              <div className="space-y-2">
                {routingSlice.map((r, i) => {
                  const sev = r.ratio != null ? (r.ratio >= 3 ? 'red' : r.ratio >= 1.5 ? 'orange' : 'blue') : 'blue'
                  const s = SEVERITY_STYLES[sev]
                  return (
                    <RuleCard key={i} rule={r.rule_text}
                      badge={`RESOURCE ROUTING · ${fmtRatio(r.ratio, r.rule_text)}`}
                      badgeClass={s.badge} bg={`${s.bg} border`}
                      meta={`n=${r.count?.toLocaleString()} · Baseline for resource: ${r.baseline_pct?.toFixed(0)}% → When preceded: ${r.conditional_pct?.toFixed(0)}%`}
                    />
                  )
                })}
              </div>
              <Paginator total={filteredRouting.length} page={routingPage} pageSize={PAGE} onPage={setRoutingPage} />
            </>
          )}
        </div>
      )}

      {/* ── CASE ATTRIBUTES ── */}
      {activeTab === 'case_attributes' && (
        <div className="space-y-4">
          {caObservations.length === 0 && caParallelRules.length === 0 ? (
            <div className="bg-gray-50 border border-gray-100 rounded-none p-10 text-center">
              <p className="text-sm font-semibold text-gray-900 mb-1">No case attribute observations available</p>
              <p className="text-xs text-gray-400 max-w-sm mx-auto">
                Upload a case log alongside your event log to discover how case attributes (region, tier, type…) influence process behaviour.
              </p>
            </div>
          ) : (
            <>
              {(impactCa.isFallback || impactCaParallel.isFallback) && <FallbackBanner />}
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex gap-2 flex-wrap">
                  {(['cycle_time_effect','rework_effect','activity_presence','activity_absence','parallel_effect'] as const).map(st => {
                    const n = st === 'parallel_effect' ? caParallelRules.length : caObservations.filter(a => a.sub_type === st).length
                    if (!n) return null
                    const labels: Record<string, string> = { cycle_time_effect: 'Cycle Time', rework_effect: 'Rework', activity_presence: 'Activity Presence', activity_absence: 'Activity Absence', parallel_effect: 'Parallel' }
                    const colorMap: Record<string, string> = { cycle_time_effect: 'red', rework_effect: 'orange', activity_presence: 'blue', activity_absence: 'gray', parallel_effect: 'blue' }
                    const tooltipMap: Record<string, string> = {
                    cycle_time_effect: 'Cases with this attribute value take significantly more or less time than the average case.',
                    rework_effect: 'Cases with this attribute value experience disproportionately more or less rework.',
                    activity_presence: 'Cases with this attribute value are significantly more likely to include a given activity.',
                    activity_absence: 'Cases with this attribute value are significantly more likely to skip a given activity.',
                    parallel_effect: 'Cases with this attribute value significantly raise or lower the probability of an activity executing in parallel.',
                  }
                  return <MetricChip key={st} label={labels[st]} value={n} color={colorMap[st]} tooltip={tooltipMap[st]} />
                  })}
                </div>
                <div className="ml-auto flex items-center gap-3">
                  <FilterBar label="Attribute" value={caAttrFilter} onChange={v => { setCaAttrFilter(v); setCaPage(0) }} options={caAllAttributes} />
                  <FilterBar label="Type" value={caSubTypeFilter} onChange={v => { setCaSubTypeFilter(v); setCaPage(0) }}
                    options={['cycle_time_effect','rework_effect','activity_presence','activity_absence','parallel_effect']} />
                </div>
              </div>

              {(() => {
                const filteredCa = impactCa.rules.filter(a =>
                  (!caAttrFilter || a.attribute === caAttrFilter) &&
                  (!caSubTypeFilter || caSubTypeFilter === a.sub_type)
                )
                const filteredPar = (!caSubTypeFilter || caSubTypeFilter === 'parallel_effect')
                  ? impactCaParallel.rules.filter(r => {
                      const attr = r.condition_type?.replace(/^case_attr:/, '') ?? ''
                      return !caAttrFilter || attr === caAttrFilter
                    })
                  : []
                const caSlice = filteredCa.slice(caPage * PAGE, (caPage + 1) * PAGE)
                const shownCa = caSlice

                const groups: Record<string, { ca: CaseAttributeObservation[]; par: ConditionalParallelRule[] }> = {}
                shownCa.forEach(a => {
                  if (!groups[a.attribute]) groups[a.attribute] = { ca: [], par: [] }
                  groups[a.attribute].ca.push(a)
                })
                filteredPar.forEach(r => {
                  const attr = r.condition_type?.replace(/^case_attr:/, '') ?? 'unknown'
                  if (!groups[attr]) groups[attr] = { ca: [], par: [] }
                  groups[attr].par.push(r)
                })

                const SUB_BADGE: Record<string, string> = { cycle_time_effect: 'CYCLE TIME', rework_effect: 'REWORK', activity_presence: 'ACTIVITY', activity_absence: 'ABSENCE' }
                const SUB_COLORS: Record<string, { bg: string; badge: string }> = {
                  cycle_time_effect: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900' },
                  rework_effect: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900' },
                  activity_presence: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900' },
                  activity_absence: { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-600' },
                }
                const PAR_STYLE = { bg: 'bg-gray-50 border-gray-100', badge: 'bg-gray-100 text-gray-900' }

                return (
                  <div className="space-y-6">
                    {Object.entries(groups).map(([attr, { ca: observations, par: pars }]) => (
                      <div key={attr}>
                        <div className="flex items-center gap-2 mb-2">
                          <span className="text-xs font-semibold text-gray-800 bg-gray-50 px-2.5 py-1 rounded-none border border-gray-100">{attr}</span>
                          <span className="text-xs text-gray-400">{observations.length + pars.length} observation{observations.length + pars.length !== 1 ? 's' : ''}</span>
                        </div>
                        <div className="space-y-2">
                          {observations.map((a, i) => {
                            const style = SUB_COLORS[a.sub_type] ?? SUB_COLORS.activity_presence
                            const badge = `${SUB_BADGE[a.sub_type] ?? a.sub_type.toUpperCase()} · ${fmtRatio(a.ratio, a.rule_text)}`
                            const meta = a.sub_type === 'cycle_time_effect'
                              ? `Baseline: ${a.baseline_value.toFixed(1)}h → Conditional: ${a.conditional_value.toFixed(1)}h · n=${a.n_cases.toLocaleString()}`
                              : `Baseline: ${a.baseline_value.toFixed(0)}% → Conditional: ${a.conditional_value.toFixed(0)}% · n=${a.n_cases.toLocaleString()}`
                            return <RuleCard key={`ca-${i}`} rule={a.rule_text} badge={badge} badgeClass={style.badge} bg={`${style.bg} border`} meta={meta} />
                          })}
                          {pars.map((r, i) => {
                            const badge = r.ratio != null ? `PARALLEL · ${fmtRatio(r.ratio, r.rule_text)}` : `PARALLEL · ${r.parallel_rate_pct.toFixed(0)}%`
                            const meta = r.baseline_rate_pct != null
                              ? `Baseline: ${r.baseline_rate_pct.toFixed(0)}% → Conditional: ${r.parallel_rate_pct.toFixed(0)}% · n=${r.count.toLocaleString()}`
                              : `${r.parallel_rate_pct.toFixed(0)}% co-occurrence rate · n=${r.count.toLocaleString()}`
                            return <RuleCard key={`par-${i}`} rule={r.rule_text} badge={badge} badgeClass={PAR_STYLE.badge} bg={`${PAR_STYLE.bg} border`} meta={meta} />
                          })}
                        </div>
                      </div>
                    ))}
                    <Paginator total={filteredCa.length} page={caPage} pageSize={PAGE} onPage={setCaPage} />
                  </div>
                )
              })()}
            </>
          )}
        </div>
      )}
        </div>
      </div>
    </div>
  )
}
