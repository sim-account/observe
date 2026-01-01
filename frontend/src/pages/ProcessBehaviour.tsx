import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSession } from '@/store/sessionStore'
import { getProcessBehaviour, type StatisticalLearnings, type CaseAttributeBehaviour } from '@/api/client'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Loader2 } from 'lucide-react'
import { useSortable, SortTh } from '@/components/SortTh'
import Paginator, { usePagination } from '@/components/Paginator'

const PAGE = 7

type TabId = 'time' | 'waiting' | 'sequence' | 'arrival' | 'resource' | 'rework' | 'parallel' | 'case_attributes'

const TABS: { id: TabId; label: string }[] = [
  { id: 'time', label: '⏱ Time' },
  { id: 'waiting', label: '⏳ Residual Wait' },
  { id: 'sequence', label: '🔀 Sequence' },
  { id: 'arrival', label: '📥 Arrival' },
  { id: 'resource', label: '👤 Resource' },
  { id: 'rework', label: '🔄 Rework' },
  { id: 'parallel', label: '⇌ Parallel' },
  { id: 'case_attributes', label: '🏷 Case Attributes' },
]

function CaseAttributeBehaviourTab({ cab }: { cab: CaseAttributeBehaviour }) {
  const attrs = Object.entries(cab)

  if (!attrs.length) {
    return (
      <div className="text-center py-16 text-gray-400 text-sm">
        No case attribute data available. Upload a case log or ensure your event log has extra attribute columns.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {attrs.map(([attr, attrData]) => (
        <div key={attr} className="bg-white rounded-none border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-100 bg-gray-50">
            <span className="font-semibold text-gray-800 text-sm">{attr}</span>
            <span className="ml-2 text-xs text-gray-400">{attrData.values.length} value{attrData.values.length !== 1 ? 's' : ''} · {attrData.n_total.toLocaleString()} cases</span>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-gray-400 bg-gray-50 border-b border-gray-100">
                <th className="text-left px-5 py-2 font-medium">Value</th>
                <th className="text-right px-4 py-2 font-medium">Cases</th>
                <th className="text-right px-4 py-2 font-medium w-48">Share</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {attrData.values.map(v => (
                <tr key={v.value} className="hover:bg-gray-50">
                  <td className="px-5 py-2 text-gray-800">{v.value}</td>
                  <td className="px-4 py-2 text-right text-gray-500 tabular-nums">{v.n_cases.toLocaleString()}</td>
                  <td className="px-4 py-2">
                    <div className="flex items-center justify-end gap-2">
                      <div className="w-24 bg-gray-100 rounded-none h-1.5">
                        <div className="bg-gray-400 h-1.5 rounded-none" style={{ width: `${Math.min(v.share * 100, 100)}%` }} />
                      </div>
                      <span className="text-gray-600 tabular-nums w-10 text-right">{(v.share * 100).toFixed(1)}%</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

function fmtHours(h: number) {
  if (h < 1) return `${(h * 60).toFixed(0)} min`
  if (h < 48) return `${h.toFixed(1)} hrs`
  return `${(h / 24).toFixed(1)} days`
}

export default function ProcessBehaviour() {
  const { sessionId, reset } = useSession()
  const navigate = useNavigate()
  const [data, setData] = useState<StatisticalLearnings | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<TabId>('time')
  const [seqFilter, setSeqFilter] = useState('')
  const [resActivity, setResActivity] = useState('')

  // per-table page state
  const [timePage, setTimePage] = useState(0)
  const [waitPage, setWaitPage] = useState(0)
  const [seqPage, setSeqPage] = useState(0)
  const [reworkPage, setReworkPage] = useState(0)
  const [capPage, setCapPage] = useState(0)
  const [calPage, setCalPage] = useState(0)
  const [parActPage, setParActPage] = useState(0)
  const [parPairPage, setParPairPage] = useState(0)
  const [dpFilter, setDpFilter] = useState('')
  const [dpPage, setDpPage] = useState(0)

  // reset pages when tab or filter changes
  useEffect(() => { setTimePage(0) }, [activeTab])
  useEffect(() => { setSeqPage(0) }, [seqFilter, activeTab])
  useEffect(() => { setReworkPage(0); setParActPage(0); setParPairPage(0) }, [activeTab])
  useEffect(() => { setDpPage(0) }, [dpFilter, activeTab])

  useEffect(() => {
    if (!sessionId) { navigate('/upload'); return }
    getProcessBehaviour(sessionId)
      .then(d => { setData(d); setLoading(false) })
      .catch(() => { reset(); navigate('/upload') })
  }, [sessionId])

  const filteredSeq = useMemo(() => {
    if (!data?.routing_probabilities) return []
    return seqFilter ? data.routing_probabilities.filter(r => r.from_activity === seqFilter) : data.routing_probabilities
  }, [data?.routing_probabilities, seqFilter])

  const resActivityRows = useMemo(() => {
    if (!data?.resource_assignments || !resActivity) return []
    return data.resource_assignments.filter(r => r.activity === resActivity)
      .sort((a, b) => b.probability - a.probability).slice(0, 10)
  }, [data?.resource_assignments, resActivity])

  const capacityRows = useMemo(() => {
    if (!data) return []
    return Object.entries(data.resource_capacity_dict).map(([res, cap]) => ({
      res, cap, peak: data.peak_concurrent_dict[res] ?? 0,
    }))
  }, [data])

  const calendarRows = useMemo(() => {
    if (!data) return []
    return Object.entries(data.calendars?.resource_calendars ?? {}).map(([res, [s, e]]) => ({
      res, start: s, end: e, hours: e - s,
    }))
  }, [data])

  const timeObservationsWithFreq = useMemo(() => {
    const observations = data?.activity_durations ?? []
    const totalCount = observations.reduce((s, r) => s + r.count, 0) || 1
    return observations.map(r => ({ ...r, freq_pct: (r.count / totalCount) * 100 }))
  }, [data?.activity_durations])

  const { sorted: sortedTime, col: tCol, dir: tDir, toggle: tToggle } = useSortable(timeObservationsWithFreq, 'mean_minutes')
  const { sorted: sortedWait, col: wCol, dir: wDir, toggle: wToggle } = useSortable(data?.residual_wait_parameters ?? [], 'mean_positive_minutes')
  const { sorted: sortedSeq, col: sCol, dir: sDir, toggle: sToggle } = useSortable(filteredSeq, 'probability')
  const { sorted: sortedRework, col: rwCol, dir: rwDir, toggle: rwToggle } = useSortable(data?.rework_parameters ?? [], 'rework_rate_given_visit_pct')
  const { sorted: sortedCapacity, col: capCol, dir: capDir, toggle: capToggle } = useSortable(capacityRows, 'cap')
  const { sorted: sortedCalendars, col: calCol, dir: calDir, toggle: calToggle } = useSortable(calendarRows, 'hours')
  const { sorted: sortedResRows, col: resCol, dir: resDir, toggle: resToggle } = useSortable(resActivityRows, 'probability')
  const { sorted: sortedParActs, col: paCol, dir: paDir, toggle: paToggle } = useSortable(data?.parallel_parameters ?? [], 'parallel_rate_pct')
  const { sorted: sortedParPairs, col: ppCol, dir: ppDir, toggle: ppToggle } = useSortable(data?.parallel_pairs ?? [], 'co_parallel_count')

  const { slice: timeSlice } = usePagination(sortedTime, timePage, PAGE)
  const { slice: waitSlice } = usePagination(sortedWait, waitPage, PAGE)
  const { slice: seqSlice } = usePagination(sortedSeq, seqPage, PAGE)
  const { slice: reworkSlice } = usePagination(sortedRework, reworkPage, PAGE)
  const { slice: capSlice } = usePagination(sortedCapacity, capPage, PAGE)
  const { slice: calSlice } = usePagination(sortedCalendars, calPage, PAGE)
  const { slice: parActSlice } = usePagination(sortedParActs, parActPage, PAGE)
  const { slice: parPairSlice } = usePagination(sortedParPairs, parPairPage, PAGE)

  if (loading) return (
    <div className="flex items-center justify-center h-96 text-gray-400">
      <Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading process behaviour…
    </div>
  )
  if (!data) return <div className="p-8 text-gray-400">No data available.</div>

  return (
    <div className="p-6">
      {/* Log metrics bar */}
      {data.log_metrics?.cases_count && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 mb-6">
          {[
            { label: 'Cases', val: data.log_metrics.cases_count?.toLocaleString() },
            { label: 'Avg cycle time', val: data.log_metrics.avg_cycle_time_hours != null ? fmtHours(data.log_metrics.avg_cycle_time_hours) : '—' },
            { label: 'Median cycle', val: data.log_metrics.median_cycle_time_hours != null ? fmtHours(data.log_metrics.median_cycle_time_hours) : '—' },
            { label: 'Resources', val: data.resources.length },
            { label: 'Parallel service', val: data.log_metrics.parallel_service_pct != null ? `${data.log_metrics.parallel_service_pct.toFixed(1)}%` : '—' },
            { label: 'Rework rate', val: data.log_metrics.rework_rate_pct != null ? `${data.log_metrics.rework_rate_pct}%` : '—' },
            { label: 'Activities', val: data.activities.length },
          ].map(({ label, val }) => (
            <div key={label} className="bg-white rounded-none border border-gray-200 px-4 py-3">
              <p className="text-xs text-gray-400 font-medium mb-0.5">{label}</p>
              <p className="text-lg font-bold text-gray-900">{val}</p>
            </div>
          ))}
        </div>
      )}

      {/* Body: vertical tab column on the left + content on the right */}
      <div className="flex gap-6">
        {/* Sub-tab nav (vertical) */}
        <div className="shrink-0 w-44 self-start sticky top-4">
          <div className="flex flex-col gap-1 bg-white p-1 rounded-none border border-gray-200">
            {TABS.filter(t => t.id !== 'case_attributes' || Object.keys(data.case_attribute_behaviour ?? {}).length > 0).map(t => (              <button key={t.id} onClick={() => setActiveTab(t.id)}
                className={`text-left px-4 py-2 rounded-none text-sm font-medium transition-colors whitespace-nowrap ${activeTab === t.id ? 'bg-gray-800 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-100'}`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* Content column */}
        <div className="flex-1 min-w-0 space-y-6">

      {/* ── TIME ── */}
      {activeTab === 'time' && (
        <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-100">
            <h2 className="font-semibold text-gray-900">Activity Duration Distributions</h2>
            <p className="text-xs text-gray-400 mt-0.5">Mean and standard deviation of activity processing time in minutes</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 bg-gray-50 border-b border-gray-100">
                  <th className="text-left px-5 py-3 font-medium">Activity</th>
                  <SortTh col="mean_minutes" activeCol={tCol} dir={tDir} onToggle={tToggle}>Mean (min)</SortTh>
                  <SortTh col="std_minutes" activeCol={tCol} dir={tDir} onToggle={tToggle}>Std dev (min)</SortTh>
                  <SortTh col="count" activeCol={tCol} dir={tDir} onToggle={tToggle}>Observations</SortTh>
                  <SortTh col="freq_pct" activeCol={tCol} dir={tDir} onToggle={tToggle}>Frequency</SortTh>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {(() => {
                  const maxFreq = Math.max(...sortedTime.map(r => r.freq_pct), 1)
                  return timeSlice.map(row => (
                    <tr key={row.activity} className="hover:bg-gray-50 transition-colors">
                      <td className="px-5 py-3 font-medium text-gray-800">{row.activity}</td>
                      <td className="px-4 py-3 text-right text-gray-700">{row.mean_minutes.toFixed(1)}</td>
                      <td className="px-4 py-3 text-right text-gray-500">{row.std_minutes.toFixed(1)}</td>
                      <td className="px-4 py-3 text-right text-gray-500">{row.count.toLocaleString()}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-2">
                          <div className="w-20 bg-gray-100 rounded-none h-1.5 overflow-hidden" title={`${row.freq_pct.toFixed(1)}% of all activity executions`}>
                            <div className="h-full bg-gray-400 rounded-none" style={{ width: `${(row.freq_pct / maxFreq) * 100}%` }} />
                          </div>
                          <span className="text-xs text-gray-500 tabular-nums w-10 text-right">{row.freq_pct.toFixed(1)}%</span>
                        </div>
                      </td>
                    </tr>
                  ))
                })()}
              </tbody>
            </table>
          </div>
          <Paginator total={sortedTime.length} page={timePage} pageSize={PAGE} onPage={p => { setTimePage(p) }} />
        </div>
      )}

      {/* ── RESIDUAL WAIT ── */}
      {activeTab === 'waiting' && (
        <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-100">
            <h2 className="font-semibold text-gray-900">Residual Waiting-Time Distributions</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              Enablement-to-release delays remaining after observed calendars and resource capacity are replayed
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 bg-gray-50 border-b border-gray-100">
                  <th className="text-left px-5 py-3 font-medium">From</th>
                  <th className="text-left px-4 py-3 font-medium">Activity</th>
                  <SortTh col="positive_wait_probability" activeCol={wCol} dir={wDir} onToggle={wToggle}>Wait probability</SortTh>
                  <SortTh col="mean_positive_minutes" activeCol={wCol} dir={wDir} onToggle={wToggle}>Mean positive wait</SortTh>
                  <SortTh col="p90_positive_minutes" activeCol={wCol} dir={wDir} onToggle={wToggle}>P90 positive wait</SortTh>
                  <SortTh col="observations" activeCol={wCol} dir={wDir} onToggle={wToggle}>Observations</SortTh>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {waitSlice.map((row, i) => (
                  <tr key={`${row.from_activity}-${row.activity}-${i}`} className="hover:bg-gray-50">
                    <td className="px-5 py-3 text-gray-700">{row.from_activity}</td>
                    <td className="px-4 py-3 font-medium text-gray-800">{row.activity}</td>
                    <td className="px-4 py-3 text-right text-gray-700">{(row.positive_wait_probability * 100).toFixed(1)}%</td>
                    <td className="px-4 py-3 text-right text-gray-700">{fmtHours(row.mean_positive_minutes / 60)}</td>
                    <td className="px-4 py-3 text-right text-gray-500">{fmtHours(row.p90_positive_minutes / 60)}</td>
                    <td className="px-4 py-3 text-right text-gray-500">
                      {row.observations.toLocaleString()}
                      {!row.context_supported && <span className="ml-1 text-gray-600" title="Sparse context; simulation uses activity-level fallback">*</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {sortedWait.length === 0 && (
            <div className="px-5 py-10 text-center text-sm text-gray-400">No unambiguous sequential waiting observations were found.</div>
          )}
          <Paginator total={sortedWait.length} page={waitPage} pageSize={PAGE} onPage={setWaitPage} />
        </div>
      )}

      {/* ── SEQUENCE ── */}
      {activeTab === 'sequence' && (
        <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
          <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
            <div>
              <h2 className="font-semibold text-gray-900">Direct Transition Probabilities</h2>
              <p className="text-xs text-gray-400 mt-0.5">Probability that activity A is directly followed by B</p>
            </div>
            <select value={seqFilter} onChange={e => setSeqFilter(e.target.value)}
              className="border border-gray-200 rounded-none px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
              <option value="">All activities</option>
              {data.from_activities.map(a => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 bg-gray-50 border-b border-gray-100">
                  <th className="text-left px-5 py-3 font-medium">From</th>
                  <th className="text-left px-4 py-3 font-medium">To</th>
                  <SortTh col="probability" activeCol={sCol} dir={sDir} onToggle={sToggle}>Probability</SortTh>
                  <SortTh col="count" activeCol={sCol} dir={sDir} onToggle={sToggle}>Count</SortTh>
                  <th className="px-4 py-3 font-medium">Bar</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {seqSlice.map((row, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-5 py-2.5 text-gray-700">{row.from_activity}</td>
                    <td className="px-4 py-2.5 text-gray-700">{row.to_activity}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-gray-900">{(row.probability * 100).toFixed(1)}%</td>
                    <td className="px-4 py-2.5 text-right text-gray-400">{row.count?.toLocaleString()}</td>
                    <td className="px-4 py-2.5">
                      <div className="w-24 bg-gray-100 rounded-none h-1.5">
                        <div className="h-full bg-gray-400 rounded-none" style={{ width: `${row.probability * 100}%` }} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Paginator total={sortedSeq.length} page={seqPage} pageSize={PAGE} onPage={setSeqPage} />
        </div>
      )}

      {/* ── DECISION POINTS (shown alongside sequence tab) ── */}
      {activeTab === 'sequence' && data.decision_points && data.decision_points.length > 0 && (() => {
        // All unique gateway activities
        const allGateways = [...new Set(data.decision_points.map(r => r.gateway_activity))].sort()

        // Filter by selected gateway
        const filtered = dpFilter
          ? data.decision_points.filter(r => r.gateway_activity === dpFilter)
          : data.decision_points

        // Group filtered rows by gateway_activity
        const grouped: Record<string, typeof data.decision_points> = {}
        filtered.forEach(r => {
          if (!grouped[r.gateway_activity]) grouped[r.gateway_activity] = []
          grouped[r.gateway_activity].push(r)
        })
        const gatewayList = Object.entries(grouped)

        // Paginate at the gateway level (10 gateways per page)
        const totalGateways = gatewayList.length
        const pageSize = 10
        const pageStart = dpPage * pageSize
        const pageGateways = gatewayList.slice(pageStart, pageStart + pageSize)

        return (
          <div className="bg-white rounded-none border border-gray-200 overflow-hidden mt-4">
            <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
              <div>
                <h2 className="font-semibold text-gray-900">Decision Points</h2>
                <p className="text-xs text-gray-400 mt-0.5">
                  Activities with ≥2 branches each carrying ≥5% probability — mined from observed routing frequencies
                </p>
              </div>
              <select value={dpFilter} onChange={e => setDpFilter(e.target.value)}
                className="border border-gray-200 rounded-none px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                <option value="">All activities ({allGateways.length})</option>
                {allGateways.map(gw => <option key={gw} value={gw}>{gw}</option>)}
              </select>
            </div>
            <div className="divide-y divide-gray-50">
              {pageGateways.map(([gw, branches]) => (
                <div key={gw} className="px-5 py-4">
                  <div className="flex items-center gap-2 mb-3">
                    <span className="text-xs font-semibold text-gray-800 bg-gray-50 px-2.5 py-1 rounded-none border border-gray-100">
                      ⊕ {gw}
                    </span>
                    <span className="text-xs text-gray-400">{branches.length} branches</span>
                  </div>
                  <div className="space-y-1.5">
                    {branches.map(b => (
                      <div key={b.branch_activity} className="flex items-center gap-3">
                        <span className={`text-xs w-4 text-center ${b.is_dominant ? 'text-gray-600 font-bold' : 'text-gray-300'}`}>
                          {b.is_dominant ? '★' : '·'}
                        </span>
                        <span className="text-sm text-gray-700 flex-1 truncate">{b.branch_activity}</span>
                        <span className="text-sm font-semibold tabular-nums text-gray-800 w-12 text-right">
                          {b.probability_pct.toFixed(1)}%
                        </span>
                        <div className="w-24 bg-gray-100 rounded-none h-1.5 overflow-hidden">
                          <div
                            className={`h-full rounded-none ${b.is_dominant ? 'bg-gray-400' : 'bg-gray-300'}`}
                            style={{ width: `${b.probability_pct}%` }}
                          />
                        </div>
                        <span className="text-xs text-gray-400 tabular-nums w-16 text-right">
                          {b.count.toLocaleString()} obs
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <Paginator total={totalGateways} page={dpPage} pageSize={pageSize} onPage={setDpPage} />
          </div>
        )
      })()}

      {/* ── ARRIVAL ── */}
      {activeTab === 'arrival' && (
        <div className="max-w-lg">
          <div className="bg-white rounded-none border border-gray-200 p-6">
            <h2 className="font-semibold text-gray-900 mb-1">Case Arrival Rate</h2>
            <p className="text-xs text-gray-400 mb-6">Mined from the distribution of case start times in the event log</p>
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-gray-50 rounded-none p-4">
                <p className="text-xs font-medium text-gray-800 mb-1">Cases per day</p>
                <p className="text-3xl font-bold text-gray-950">{data.arrival_parameters.cases_per_day?.toFixed(1)}</p>
              </div>
              <div className="bg-gray-50 rounded-none p-4">
                <p className="text-xs font-medium text-gray-500 mb-1">Mean inter-arrival</p>
                <p className="text-3xl font-bold text-gray-800">
                  {data.arrival_parameters.mean_inter_arrival_hours != null
                    ? fmtHours(data.arrival_parameters.mean_inter_arrival_hours)
                    : fmtHours(24 / Math.max(data.arrival_parameters.cases_per_day, 0.001))}
                </p>
              </div>
            </div>
            <div className="mt-4 pt-4 border-t border-gray-100">
              <p className="text-xs text-gray-500">
                The simulation uses an exponential inter-arrival distribution with rate λ = {data.arrival_parameters.cases_per_day?.toFixed(2)} cases/day.
                Modify this in the Scenario Builder to simulate higher or lower demand.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── RESOURCE ── */}
      {activeTab === 'resource' && (
        <div className="space-y-6">
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: 'Unique resources', val: data.resources.length },
              { label: 'Activities covered', val: [...new Set(data.resource_assignments.map(r => r.activity))].length },
              { label: 'Global window', val: `${data.calendars?.global_start_hour ?? 0}:00 – ${data.calendars?.global_end_hour ?? 24}:00` },
            ].map(({ label, val }) => (
              <div key={label} className="bg-white rounded-none border border-gray-200 px-5 py-4">
                <p className="text-xs text-gray-400 font-medium mb-1">{label}</p>
                <p className="text-2xl font-bold text-gray-900">{val}</p>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {Object.keys(data.resource_capacity_dict).length > 0 && (
              <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
                <div className="px-5 py-4 border-b border-gray-100">
                  <h3 className="font-semibold text-gray-900">Resource Capacity (Sim Slots)</h3>
                  <p className="text-xs text-gray-400 mt-0.5">Avg concurrent WIP inferred via Little's Law</p>
                </div>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 bg-gray-50 border-b border-gray-100">
                      <th className="text-left px-5 py-2 font-medium">Resource</th>
                      <SortTh col="cap" activeCol={capCol} dir={capDir} onToggle={capToggle} className="px-4 py-2 font-medium text-right">Sim capacity</SortTh>
                      <SortTh col="peak" activeCol={capCol} dir={capDir} onToggle={capToggle} className="px-4 py-2 font-medium text-right">Peak observed</SortTh>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {capSlice.map(({ res, cap, peak }) => (
                      <tr key={res} className="hover:bg-gray-50">
                        <td className="px-5 py-2 font-medium text-gray-700">{res}</td>
                        <td className="px-4 py-2 text-right text-gray-900 font-medium">{cap}</td>
                        <td className="px-4 py-2 text-right text-gray-400">{peak || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Paginator total={sortedCapacity.length} page={capPage} pageSize={PAGE} onPage={setCapPage} />
              </div>
            )}

            {Object.keys(data.calendars?.resource_calendars ?? {}).length > 0 && (
              <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
                <div className="px-5 py-4 border-b border-gray-100">
                  <h3 className="font-semibold text-gray-900">Resource Calendars</h3>
                  <p className="text-xs text-gray-400 mt-0.5">Inferred active hours from event timestamps</p>
                </div>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 bg-gray-50 border-b border-gray-100">
                      <th className="text-left px-5 py-2 font-medium">Resource</th>
                      <SortTh col="start" activeCol={calCol} dir={calDir} onToggle={calToggle} className="px-4 py-2 font-medium text-right">Active from</SortTh>
                      <SortTh col="end" activeCol={calCol} dir={calDir} onToggle={calToggle} className="px-4 py-2 font-medium text-right">Active until</SortTh>
                      <SortTh col="hours" activeCol={calCol} dir={calDir} onToggle={calToggle} className="px-4 py-2 font-medium text-right">Hours/day</SortTh>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {calSlice.map(({ res, start, end, hours }) => (
                      <tr key={res} className="hover:bg-gray-50">
                        <td className="px-5 py-2 font-medium text-gray-700">{res}</td>
                        <td className="px-4 py-2 text-right text-gray-600">{start}:00</td>
                        <td className="px-4 py-2 text-right text-gray-600">{end}:00</td>
                        <td className="px-4 py-2 text-right text-gray-400">{hours}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Paginator total={sortedCalendars.length} page={calPage} pageSize={PAGE} onPage={setCalPage} />
              </div>
            )}
          </div>

          {data.resource_assignments.length > 0 && (
            <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
              <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                <h3 className="font-semibold text-gray-900">Per-Activity Resource Breakdown</h3>
                <select value={resActivity || data.activities[0] || ''} onChange={e => setResActivity(e.target.value)}
                  className="border border-gray-200 rounded-none px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-600">
                  {data.activities.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>
              <div className="p-5 grid grid-cols-1 lg:grid-cols-2 gap-6">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 border-b border-gray-100">
                      <th className="text-left py-2 font-medium">Resource</th>
                      <SortTh col="probability" activeCol={resCol} dir={resDir} onToggle={resToggle} className="py-2 font-medium text-right">Share</SortTh>
                      <SortTh col="mean_duration_min" activeCol={resCol} dir={resDir} onToggle={resToggle} className="py-2 font-medium text-right">Avg duration</SortTh>
                      <SortTh col="count" activeCol={resCol} dir={resDir} onToggle={resToggle} className="py-2 font-medium text-right">Count</SortTh>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {sortedResRows.map(row => (
                      <tr key={row.resource} className="hover:bg-gray-50">
                        <td className="py-2 font-medium text-gray-700">{row.resource}</td>
                        <td className="py-2 text-right text-gray-600">{(row.probability * 100).toFixed(1)}%</td>
                        <td className="py-2 text-right text-gray-600">{row.mean_duration_min.toFixed(0)} min</td>
                        <td className="py-2 text-right text-gray-400">{row.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {resActivityRows.length > 0 && (
                  <ResponsiveContainer width="100%" height={Math.max(200, resActivityRows.length * 36)}>
                    <BarChart data={resActivityRows.map(r => ({ name: r.resource, duration: r.mean_duration_min, share: +(r.probability * 100).toFixed(1) }))}
                      layout="vertical" margin={{ left: 8, right: 60, top: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f0f0f0" />
                      <XAxis type="number" unit=" min" tick={{ fontSize: 11 }} stroke="#d1d5db" />
                      <YAxis type="category" dataKey="name" width={120} tick={{ fontSize: 11, fill: '#6b7280' }} stroke="none" />
                      <Tooltip formatter={(v) => [`${Number(v).toFixed(0)} min`, 'Avg duration']} />
                      <Bar dataKey="duration" radius={[0, 4, 4, 0]} fill="#525252" label={{ position: 'right', fontSize: 10, fill: '#6b7280', formatter: (v) => `${Number(v).toFixed(0)}m` }} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── REWORK ── */}
      {activeTab === 'rework' && (
        <div className="space-y-6">
          {data.rework_parameters.length === 0 ? (
            <div className="bg-gray-50 border border-gray-200 rounded-none p-8 text-center text-gray-900">No rework detected in the event log.</div>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-4">
                {[
                  { label: 'Activities with rework', val: data.rework_parameters.length },
                  { label: 'Highest rework rate', val: `${Math.max(...data.rework_parameters.map(r => r.rework_rate_pct)).toFixed(1)}%` },
                  { label: 'Max loops observed', val: `${Math.max(...data.rework_parameters.map(r => r.max_loops))}×` },
                ].map(({ label, val }) => (
                  <div key={label} className="bg-white rounded-none border border-gray-200 px-5 py-4">
                    <p className="text-xs text-gray-400 font-medium mb-1">{label}</p>
                    <p className="text-2xl font-bold text-gray-900">{val}</p>
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
                  <div className="px-5 py-4 border-b border-gray-100">
                    <h3 className="font-semibold text-gray-900">Rework by Activity</h3>
                    <p className="text-xs text-gray-400 mt-0.5">Rate given visit = probability used by rework injection</p>
                  </div>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-xs text-gray-500 bg-gray-50 border-b border-gray-100">
                        <th className="text-left px-5 py-3 font-medium">Activity</th>
                        <SortTh col="rework_rate_pct" activeCol={rwCol} dir={rwDir} onToggle={rwToggle}>Rework %</SortTh>
                        <SortTh col="rework_rate_given_visit_pct" activeCol={rwCol} dir={rwDir} onToggle={rwToggle}>Rate/visit</SortTh>
                        <SortTh col="mean_extra_loops" activeCol={rwCol} dir={rwDir} onToggle={rwToggle}>Avg loops</SortTh>
                        <SortTh col="max_loops" activeCol={rwCol} dir={rwDir} onToggle={rwToggle}>Max</SortTh>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                      {reworkSlice.map(row => (
                        <tr key={row.activity} className="hover:bg-gray-50">
                          <td className="px-5 py-2.5 font-medium text-gray-800 max-w-[200px] truncate">{row.activity}</td>
                          <td className="px-4 py-2.5 text-right text-gray-600">{row.rework_rate_pct.toFixed(1)}%</td>
                          <td className="px-4 py-2.5 text-right font-medium text-gray-800">{row.rework_rate_given_visit_pct.toFixed(1)}%</td>
                          <td className="px-4 py-2.5 text-right text-gray-500">{row.mean_extra_loops.toFixed(2)}</td>
                          <td className="px-4 py-2.5 text-right text-gray-400">{row.max_loops}×</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <Paginator total={sortedRework.length} page={reworkPage} pageSize={PAGE} onPage={setReworkPage} />
                </div>
                <div className="bg-white rounded-none border border-gray-200 p-5">
                  <h3 className="font-semibold text-gray-900 mb-4">Rework Rate by Activity (rate given visit)</h3>
                  <ResponsiveContainer width="100%" height={Math.max(240, data.rework_parameters.slice(0, 12).length * 32)}>
                    <BarChart data={data.rework_parameters.slice(0, 12).map(r => ({ name: r.activity, rate: r.rework_rate_given_visit_pct }))}
                      layout="vertical" margin={{ left: 8, right: 50, top: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f0f0f0" />
                      <XAxis type="number" unit="%" tick={{ fontSize: 11 }} stroke="#d1d5db" />
                      <YAxis type="category" dataKey="name" width={160} tick={{ fontSize: 10, fill: '#6b7280' }} stroke="none" />
                      <Tooltip formatter={(v) => [`${Number(v).toFixed(1)}%`, 'Rework rate given visit']} />
                      <Bar dataKey="rate" fill="#737373" radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── PARALLEL ── */}
      {activeTab === 'parallel' && (
        <div className="space-y-6">
          {(!data.parallel_parameters || data.parallel_parameters.length === 0) ? (
            <div className="bg-gray-50 border border-gray-100 rounded-none p-10 text-center">
              <p className="text-sm font-semibold text-gray-900 mb-1">No parallel activities detected</p>
              <p className="text-xs text-gray-400 max-w-sm mx-auto">
                Requires both start_time and end_time columns. Re-upload with both timestamps to detect concurrent execution.
              </p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-4">
                {[
                  { label: 'Activities with parallelism', val: data.parallel_parameters.length },
                  { label: 'Parallel service time', val: data.log_metrics?.parallel_service_pct != null ? `${data.log_metrics.parallel_service_pct.toFixed(1)}%` : '—', tooltip: 'Sum of service time for parallel occurrences / total service time across all activities' },
                  { label: 'Parallel pairs', val: (data.parallel_pairs ?? []).length },
                ].map(({ label, val, tooltip }) => (
                  <div key={label} className="bg-white rounded-none border border-gray-200 px-5 py-4">
                    <p className="text-xs text-gray-400 font-medium mb-1">{label}{tooltip && <span className="ml-1 text-gray-300" title={tooltip}>ⓘ</span>}</p>
                    <p className="text-2xl font-bold text-gray-900">{val}</p>
                  </div>
                ))}
              </div>

              <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
                <div className="px-5 py-4 border-b border-gray-100">
                  <h3 className="font-semibold text-gray-900">Parallel Rate by Activity</h3>
                  <p className="text-xs text-gray-400 mt-0.5">Percentage of occurrences that overlapped with at least one other activity</p>
                </div>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 bg-gray-50 border-b border-gray-100">
                      <th className="text-left px-5 py-3 font-medium">Activity</th>
                      <SortTh col="parallel_rate_pct" activeCol={paCol} dir={paDir} onToggle={paToggle}>Parallel rate</SortTh>
                      <SortTh col="parallel_count" activeCol={paCol} dir={paDir} onToggle={paToggle}>Parallel occ.</SortTh>
                      <SortTh col="total_count" activeCol={paCol} dir={paDir} onToggle={paToggle}>Total occ.</SortTh>
                      <th className="text-left px-4 py-3 font-medium">Top partner</th>
                      <SortTh col="top_partner_pct" activeCol={paCol} dir={paDir} onToggle={paToggle}>Co-occ. %</SortTh>
                      <SortTh col="top_partner_mean_overlap_min" activeCol={paCol} dir={paDir} onToggle={paToggle}>Avg overlap</SortTh>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {parActSlice.map(row => (
                      <tr key={row.activity} className="hover:bg-gray-50">
                        <td className="px-5 py-2.5 font-medium text-gray-800 max-w-[200px] truncate">{row.activity}</td>
                        <td className="px-4 py-2.5 text-right">
                          <span className={`font-medium ${row.parallel_rate_pct >= 50 ? 'text-gray-800' : row.parallel_rate_pct >= 20 ? 'text-gray-800' : 'text-gray-500'}`}>
                            {row.parallel_rate_pct.toFixed(0)}%
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-right text-gray-600">{row.parallel_count.toLocaleString()}</td>
                        <td className="px-4 py-2.5 text-right text-gray-400">{row.total_count.toLocaleString()}</td>
                        <td className="px-4 py-2.5 text-gray-600 max-w-[160px] truncate">{row.top_partner ?? '—'}</td>
                        <td className="px-4 py-2.5 text-right text-gray-500">{row.top_partner ? `${row.top_partner_pct.toFixed(0)}%` : '—'}</td>
                        <td className="px-4 py-2.5 text-right text-gray-400">{row.top_partner ? `${row.top_partner_mean_overlap_min.toFixed(0)} min` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Paginator total={sortedParActs.length} page={parActPage} pageSize={PAGE} onPage={setParActPage} />
              </div>

              {(data.parallel_pairs ?? []).length > 0 && (
                <div className="bg-white rounded-none border border-gray-200 overflow-hidden">
                  <div className="px-5 py-4 border-b border-gray-100">
                    <h3 className="font-semibold text-gray-900">Parallel Pairs</h3>
                    <p className="text-xs text-gray-400 mt-0.5">Co-occurrence rates and mean overlap duration</p>
                  </div>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-xs text-gray-500 bg-gray-50 border-b border-gray-100">
                        <th className="text-left px-5 py-3 font-medium">Activity A</th>
                        <th className="text-left px-4 py-3 font-medium">Activity B</th>
                        <SortTh col="co_parallel_count" activeCol={ppCol} dir={ppDir} onToggle={ppToggle}>Co-occ.</SortTh>
                        <SortTh col="a_parallel_with_b_pct" activeCol={ppCol} dir={ppDir} onToggle={ppToggle}>A ∥ B %</SortTh>
                        <SortTh col="b_parallel_with_a_pct" activeCol={ppCol} dir={ppDir} onToggle={ppToggle}>B ∥ A %</SortTh>
                        <SortTh col="mean_overlap_min" activeCol={ppCol} dir={ppDir} onToggle={ppToggle}>Avg overlap</SortTh>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                      {parPairSlice.map((row, i) => (
                        <tr key={i} className="hover:bg-gray-50">
                          <td className="px-5 py-2.5 text-gray-700 max-w-[180px] truncate">{row.activity_a}</td>
                          <td className="px-4 py-2.5 text-gray-700 max-w-[180px] truncate">{row.activity_b}</td>
                          <td className="px-4 py-2.5 text-right text-gray-600">{row.co_parallel_count.toLocaleString()}</td>
                          <td className="px-4 py-2.5 text-right font-medium text-gray-800">{row.a_parallel_with_b_pct.toFixed(0)}%</td>
                          <td className="px-4 py-2.5 text-right font-medium text-gray-800">{row.b_parallel_with_a_pct.toFixed(0)}%</td>
                          <td className="px-4 py-2.5 text-right text-gray-400">{row.mean_overlap_min.toFixed(0)} min</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <Paginator total={sortedParPairs.length} page={parPairPage} pageSize={PAGE} onPage={setParPairPage} />
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Case Attributes tab ──────────────────────────────────── */}
      {activeTab === 'case_attributes' && (
      <CaseAttributeBehaviourTab cab={data.case_attribute_behaviour ?? {}} />
      )}

        </div>
      </div>
    </div>
  )
}
