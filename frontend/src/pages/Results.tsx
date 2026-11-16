import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { useSession } from '@/store/sessionStore'
import { ArrowLeft, CheckCircle2, XCircle, ChevronDown, ChevronRight, Lock, Download } from 'lucide-react'
import { useSortable, SortTh } from '@/components/SortTh'
import Paginator, { usePagination } from '@/components/Paginator'
import type { ModificationAuditRecord, ConstraintResult } from '@/api/client'
import { downloadSimulationLog } from '@/api/client'

// ─── formatters ──────────────────────────────────────────────────────────────

function fmtHours(h: number | undefined | null): string {
  if (h == null) return '—'
  if (h < 1) return `${(h * 60).toFixed(0)} min`
  if (h < 48) return `${h.toFixed(1)} h`
  return `${(h / 24).toFixed(1)} d`
}
function fmtPct(v: number | undefined | null): string {
  return v == null ? '—' : `${Number(v).toFixed(1)}%`
}

// ─── delta ───────────────────────────────────────────────────────────────────

function delta(from: number | null | undefined, to: number | null | undefined, lowerIsBetter = true) {
  if (from == null || to == null || from === 0) return null
  const pct = ((to - from) / from) * 100
  const good = lowerIsBetter ? pct < -0.5 : pct > 0.5
  const neutral = Math.abs(pct) < 0.5
  return { pct, label: `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`, good, neutral }
}

function DeltaChip({ from, to, lowerIsBetter = true }: { from?: number | null; to?: number | null; lowerIsBetter?: boolean }) {
  const d = delta(from, to, lowerIsBetter)
  if (!d) return null
  return (
    <span className={`text-[11px] font-bold tabular-nums px-1.5 py-0.5 rounded-none ${
      d.neutral ? 'text-gray-400 bg-gray-100'
      : d.good ? 'text-gray-900 bg-gray-50'
      : 'text-gray-800 bg-gray-50'
    }`}>
      {d.label}
    </span>
  )
}

// ─── MetricTable: compact two-col or three-col table card ────────────────────

interface MRow {
  label: string
  logVal?: number | null
  baseVal: number | null | undefined
  tobeVal?: number | null
  fmt: (v: number | null | undefined) => string
  lowerIsBetter?: boolean
}

function MetricTable({ title, accent, rows, hasLog, hasTobe }: {
  title: string; accent: string; rows: MRow[]
  hasLog: boolean; hasTobe: boolean
}) {
  return (
    <div className="bg-white rounded-none border border-gray-100 shadow-sm overflow-hidden flex flex-col">
      <div className={`px-4 py-2.5 border-b border-gray-100 ${accent}`}>
        <span className="font-semibold text-xs text-gray-700 tracking-wide uppercase">{title}</span>
      </div>
      <table className="w-full text-sm flex-1">
        <thead>
          <tr className="border-b border-gray-50">
            <th className="text-left px-4 py-1.5 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Metric</th>
            {hasLog && <th className="text-right px-3 py-1.5 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Log</th>}
            <th className="text-right px-3 py-1.5 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">As-Is</th>
            {hasTobe && <th className="text-right px-4 py-1.5 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">To-Be</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const d = delta(row.baseVal, row.tobeVal, row.lowerIsBetter ?? true)
            const good = d && !d.neutral && d.good
            const bad = d && !d.neutral && !d.good
            return (
              <tr key={i} className="border-b border-gray-50 last:border-0 hover:bg-gray-50/50 transition-colors">
                <td className="px-4 py-2 text-xs text-gray-500">{row.label}</td>
                {hasLog && (
                  <td className="px-3 py-2 text-right text-[11px] text-gray-400 tabular-nums">
                    {row.logVal != null ? row.fmt(row.logVal) : '—'}
                  </td>
                )}
                <td className="px-3 py-2 text-right">
                  <span className="text-sm font-semibold text-gray-800 tabular-nums">{row.fmt(row.baseVal)}</span>
                </td>
                {hasTobe && (
                  <td className="px-4 py-2 text-right">
                    {row.tobeVal != null ? (
                      <div className="flex items-center justify-end gap-1.5">
                        <span className={`text-sm font-bold tabular-nums ${good ? 'text-gray-800' : bad ? 'text-gray-800' : 'text-gray-900'}`}>
                          {row.fmt(row.tobeVal)}
                        </span>
                        <DeltaChip from={row.baseVal} to={row.tobeVal} lowerIsBetter={row.lowerIsBetter ?? true} />
                      </div>
                    ) : <span className="text-xs text-gray-300">—</span>}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ─── Rework: compact horizontal stat card ────────────────────────────────────

function ReworkCard({ b, s, log, hasTobe }: {
  b: { rework_rate_pct: number; avg_rework_acts: number }
  s?: { rework_rate_pct: number; avg_rework_acts: number } | null
  log?: { rework_rate_pct?: number } | null
  hasTobe: boolean
}) {
  const stats = [
    {
      label: 'Rework rate',
      base: b.rework_rate_pct,
      tobe: s?.rework_rate_pct,
      log: log?.rework_rate_pct,
      fmt: fmtPct,
    },
    {
      label: 'Avg rework acts / case',
      base: b.avg_rework_acts,
      tobe: s?.avg_rework_acts,
      fmt: (v: number | null | undefined) => v != null ? Number(v).toFixed(2) : '—',
    },
  ]
  return (
    <div className="bg-white rounded-none border border-gray-100 shadow-sm overflow-hidden">
      <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50/40">
        <span className="font-semibold text-xs text-gray-700 tracking-wide uppercase">🔄  Rework</span>
      </div>
      <div className="grid grid-cols-2 divide-x divide-gray-50">
        {stats.map((st, i) => {
          const d = hasTobe ? delta(st.base, st.tobe) : null
          const good = d && !d.neutral && d.good
          const bad = d && !d.neutral && !d.good
          return (
            <div key={i} className="px-5 py-3 flex flex-col gap-0.5">
              <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">{st.label}</span>
              <div className="flex items-baseline gap-2 mt-0.5">
                <span className="text-xl font-bold text-gray-800 tabular-nums">{st.fmt(st.base)}</span>
                {hasTobe && st.tobe != null && (
                  <span className={`text-sm font-bold tabular-nums ${good ? 'text-gray-800' : bad ? 'text-gray-800' : 'text-gray-900'}`}>
                    → {st.fmt(st.tobe)}
                  </span>
                )}
                {hasTobe && st.tobe != null && <DeltaChip from={st.base} to={st.tobe} />}
              </div>
              {'log' in st && st.log != null && (
                <span className="text-[11px] text-gray-400 tabular-nums">log: {st.fmt(st.log)}</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── Summary tiles ───────────────────────────────────────────────────────────

function SummaryTile({ label, base, tobe, baseNum, tobeNum, sub, lowerIsBetter = true }: {
  label: string; base: string; tobe?: string | null; sub?: string
  baseNum?: number | null; tobeNum?: number | null; lowerIsBetter?: boolean
}) {
  const d = baseNum != null && tobeNum != null ? delta(baseNum, tobeNum, lowerIsBetter) : null
  const good = d && !d.neutral && d.good
  const bad = d && !d.neutral && !d.good
  return (
    <div className="bg-white rounded-none border border-gray-100 shadow-sm px-4 py-3.5">
      <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1.5">{label}</p>
      <p className="text-xl font-bold text-gray-900 tabular-nums leading-none">{base}</p>
      {tobe && (
        <div className="flex items-center gap-1.5 mt-1 flex-wrap">
          <span className={`text-xs font-bold tabular-nums ${good ? 'text-gray-800' : bad ? 'text-gray-800' : 'text-gray-800'}`}>
            → {tobe}
          </span>
          {d && !d.neutral && (
            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-none ${good ? 'bg-gray-50 text-gray-900' : 'bg-gray-50 text-gray-800'}`}>
              {d.label}
            </span>
          )}
        </div>
      )}
      {sub && !tobe && <p className="text-[11px] text-gray-400 mt-1 truncate">{sub}</p>}
    </div>
  )
}

// ─── Collapsible ─────────────────────────────────────────────────────────────

function Collapsible({ title, children, defaultOpen = false }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="bg-white rounded-none border border-gray-100 shadow-sm overflow-hidden">
      <button onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-5 py-3 hover:bg-gray-50/60 transition-colors">
        <span className="font-semibold text-xs text-gray-700 tracking-wide uppercase">{title}</span>
        {open ? <ChevronDown className="w-3.5 h-3.5 text-gray-400" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-400" />}
      </button>
      {open && <div className="border-t border-gray-100">{children}</div>}
    </div>
  )
}

// ─── BnDual ──────────────────────────────────────────────────────────────────

function BnDual({ asis, tobe, fmt, lowerIsBetter = true }: { asis: number; tobe: number; fmt: (v: number) => string; lowerIsBetter?: boolean }) {
  const pct = asis === 0 ? 0 : ((tobe - asis) / asis) * 100
  const good = lowerIsBetter ? pct < -1 : pct > 1
  const bad = lowerIsBetter ? pct > 1 : pct < -1
  return (
    <span className="inline-flex items-center gap-1 tabular-nums">
      <span className="text-gray-400 text-xs">{fmt(asis)}</span>
      <span className="text-gray-300 text-[10px]">→</span>
      <span className={`text-xs font-semibold ${good ? 'text-gray-800' : bad ? 'text-gray-800' : 'text-gray-900'}`}>{fmt(tobe)}</span>
      {Math.abs(pct) >= 1 && (
        <span className={`text-[10px] font-bold px-1 py-0.5 rounded-none ${good ? 'bg-gray-50 text-gray-900' : bad ? 'bg-gray-50 text-gray-800' : 'bg-gray-100 text-gray-400'}`}>
          {pct > 0 ? '+' : ''}{pct.toFixed(0)}%
        </span>
      )}
    </span>
  )
}

// ─── Modification audit ───────────────────────────────────────────────────────

function ModAudit({ audit }: { audit: ModificationAuditRecord[] }) {
  if (!audit.length) return null
  const unmatched = audit.filter(r => !r.matched)
  return (
    <Collapsible title={`Modification diagnostics${unmatched.length > 0 ? ` · ${unmatched.length} unmatched` : ''}`}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">
              <th className="text-left px-5 py-2">Modification</th>
              <th className="text-left px-4 py-2">Status</th>
              <th className="text-right px-4 py-2">Before</th>
              <th className="text-right px-4 py-2">After</th>
              <th className="text-right px-4 py-2">Expected Δ</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {audit.map((r, i) => {
              const label = r.type === 'resource_share' || r.type === 'resource_capacity'
                ? `${r.resource} @ ${r.activity || '—'}`
                : r.activity || '—'
              const before = r.type === 'resource_share'
                ? (r.old_weighted_mean_min != null ? `${r.old_weighted_mean_min} min` : r.old_probability != null ? `${(r.old_probability * 100).toFixed(1)}%` : '—')
                : r.type === 'time' ? (r.old_mean_min != null ? `${r.old_mean_min} min` : '—')
                : r.type === 'rework_rate' ? (r.old_rate != null ? `${r.old_rate}%` : '—')
                : r.type === 'resource_capacity' ? (r.old_capacity != null ? `${r.old_capacity} slot${r.old_capacity !== 1 ? 's' : ''} (peak: ${r.peak_observed ?? '?'})` : '—')
                : r.type === 'sequence' ? (r.old_probability != null ? `${r.old_probability}%` : '—')
                : r.type === 'arrival' ? (r.old_rate != null ? `${r.old_rate} cases/day` : '—')
                : r.type === 'parallel_rate' ? (r.old_rate != null ? `${r.old_rate}%` : '—')
                : r.type === 'decision_point' ? (r.old_probability ?? '—')
                : '—'
              const after = r.type === 'resource_share'
                ? (r.new_weighted_mean_min != null ? `${r.new_weighted_mean_min} min` : r.new_probability != null ? `${(r.new_probability * 100).toFixed(1)}%` : '—')
                : r.type === 'time' ? (r.new_mean_min != null ? `${r.new_mean_min} min` : '—')
                : r.type === 'rework_rate' ? (r.new_rate != null ? `${r.new_rate}%` : '—')
                : r.type === 'resource_capacity' ? (r.new_capacity != null ? `${r.new_capacity} slot${r.new_capacity !== 1 ? 's' : ''}` : '—')
                : r.type === 'sequence' ? (r.new_probability != null ? `${r.new_probability}%` : '—')
                : r.type === 'arrival' ? (r.new_rate != null ? `${r.new_rate} cases/day` : '—')
                : r.type === 'parallel_rate' ? (r.new_rate != null ? `${r.new_rate}%` : '—')
                : r.type === 'decision_point' ? (r.new_probability ?? '—')
                : '—'
              const pct = r.type === 'resource_share' ? r.expected_service_pct_change : r.type === 'time' ? r.pct_change : null
              return (
                <tr key={i} className="hover:bg-gray-50/60">
                  <td className="px-5 py-2">
                    <span className="text-[10px] text-gray-400 capitalize">{r.type.replace('_', ' ')}</span>
                    <p className="text-xs font-medium text-gray-800">{label}</p>
                  </td>
                  <td className="px-4 py-2">
                    {r.matched
                      ? <span className="flex items-center gap-1 text-[11px] text-gray-800"><CheckCircle2 className="w-3 h-3" />matched</span>
                      : <span className="flex items-center gap-1 text-[11px] text-gray-600"><XCircle className="w-3 h-3" />not found</span>}
                  </td>
                  <td className="px-4 py-2 text-right text-[11px] text-gray-500 tabular-nums">{before}</td>
                  <td className="px-4 py-2 text-right text-[11px] text-gray-500 tabular-nums">{after}</td>
                  <td className="px-4 py-2 text-right text-[11px] font-bold tabular-nums">
                    {pct != null ? <span className={Math.abs(pct) < 0.5 ? 'text-gray-400' : pct < 0 ? 'text-gray-800' : 'text-gray-600'}>{pct > 0 ? '+' : ''}{pct.toFixed(1)}%</span> : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="px-5 py-2 text-[10px] text-gray-300 border-t border-gray-50">
          Estimated processing-time changes use activity durations and resource assignments. Capacity changes show slots before and after the change, alongside the observed peak workload.
        </p>
      </div>
    </Collapsible>
  )
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export default function Results() {
  const navigate = useNavigate()
  const { baselineResult, scenarioResult, modifications, constraints, modelSummary, sessionId } = useSession()

  const b = baselineResult
  const s = scenarioResult && (modifications.length > 0 || constraints.length > 0) ? scenarioResult : null

  const { sorted: sortedBn, col: bnCol, dir: bnDir, toggle: bnToggle } = useSortable(
    useMemo(() => b?.bottlenecks ?? [], [b]), 'total_wait_share_pct'
  )
  const topBottleneck = useMemo(
    () => (b?.bottlenecks ?? []).reduce<NonNullable<typeof b>["bottlenecks"][number] | null>(
      (top, r) => (top == null || r.total_wait_share_pct > top.total_wait_share_pct) ? r : top, null
    ),
    [b]
  )
  const sBnMap = useMemo(() => new Map((s?.bottlenecks ?? []).map(r => [r.activity, r])), [s])

  const utilData = useMemo(() => {
    if (!b?.resource_utilization) return []
    return Object.entries(b.resource_utilization)
      .map(([res, util]) => ({ res, util, tobe: s?.resource_utilization?.[res] ?? null }))
      .sort((a, b2) => b2.util - a.util)
      .slice(0, 10)
  }, [b, s])

  const [utilPage, setUtilPage] = useState(0)
  const { slice: utilSlice } = usePagination(utilData, utilPage, 10)

  if (!b) {
    return (
      <div className="flex items-center justify-center h-96 text-gray-400">
        <div className="text-center space-y-3">
          <p className="text-base font-medium text-gray-600">No results yet</p>
          <button onClick={() => navigate('/workspace/scenario')}
            className="px-4 py-2 rounded-none bg-gray-800 text-white text-sm font-medium hover:bg-gray-900 transition-colors">
            Go to Scenario Builder
          </button>
        </div>
      </div>
    )
  }

  const log = modelSummary?.log_metrics as { avg_cycle_time_hours?: number; median_cycle_time_hours?: number; rework_rate_pct?: number } | null | undefined
  const hasLog = log != null
  const hasTobe = s != null

  return (
    <div className="bg-gray-50 min-h-full">

      {/* Banner */}
      {s && (
        <div className="bg-gradient-to-r from-gray-50 to-gray-50 border-b border-gray-100 px-6 py-2 flex items-center gap-3">
          <span className="text-[11px] bg-gray-800 text-white px-2.5 py-0.5 rounded-none font-bold tracking-wide uppercase">
            {modifications.length} mod{modifications.length !== 1 ? 's' : ''} applied
          </span>
          <div className="flex items-center gap-3 text-xs ml-3">
            <span className="flex items-center gap-1.5 text-gray-500">
              <span className="w-2 h-2 rounded-none bg-gray-400 inline-block" />As-Is
            </span>
            <span className="flex items-center gap-1.5 text-gray-800 font-medium">
              <span className="w-2 h-2 rounded-none bg-gray-600 inline-block" />To-Be
            </span>
          </div>
          <button onClick={() => navigate('/workspace/scenario')}
            className="flex items-center gap-1.5 text-xs text-gray-800 hover:text-gray-900 font-medium transition-colors ml-auto">
            <ArrowLeft className="w-3.5 h-3.5" /> Edit scenario
          </button>
        </div>
      )}

      {/* Constraint violations panel */}
      {s?.constraint_results && s.constraint_results.length > 0 && (
        <div className="mx-6 mt-4 bg-white rounded-none border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-100 flex items-center gap-2">
            <Lock className="w-4 h-4 text-gray-600" />
            <span className="font-semibold text-sm text-gray-900">Constraint Evaluation</span>
            <span className="text-xs text-gray-400 ml-1">{s.constraint_results.filter(c => !c.passed).length} violation{s.constraint_results.filter(c => !c.passed).length !== 1 ? 's' : ''}</span>
          </div>
          <div className="divide-y divide-gray-50">
            {s.constraint_results.map((cr, i) => (
              <div key={i} className={`flex items-center gap-3 px-5 py-2.5 ${!cr.passed ? 'bg-gray-50/50' : ''}`}>
                {cr.passed
                  ? <CheckCircle2 className="w-4 h-4 text-gray-600 shrink-0" />
                  : <XCircle className="w-4 h-4 text-gray-600 shrink-0" />
                }
                <span className="flex-1 text-sm text-gray-700">{cr.text}</span>
                <span className={`text-xs font-medium tabular-nums ${cr.passed ? 'text-gray-800' : 'text-gray-800'}`}>
                  {cr.violation_pct != null
                    ? `${cr.violation_pct.toFixed(1)}% violation`
                    : cr.actual_value != null
                    ? `${cr.actual_value.toFixed(1)}${cr.unit === 'pct' ? '%' : ''} (threshold: ${cr.threshold}${cr.unit === 'pct' ? '%' : ''})`
                    : cr.passed ? 'Pass' : 'Fail'
                  }
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Download simulation log */}
      {s && sessionId && (
        <div className="mx-6 mt-3 flex justify-end">
          <a
            href={downloadSimulationLog(sessionId)}
            download="simulation_log.csv"
            className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-800 transition-colors"
          >
            <Download className="w-3.5 h-3.5" /> Download simulation log (CSV)
          </a>
        </div>
      )}

      <div className="px-6 py-5 space-y-3">

        {/* ── Summary tiles ── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <SummaryTile label="Avg cycle time"
            base={fmtHours(b.avg_cycle_time_hours)}
            tobe={s ? fmtHours(s.avg_cycle_time_hours) : null}
            baseNum={b.avg_cycle_time_hours} tobeNum={s?.avg_cycle_time_hours}
            sub={log?.avg_cycle_time_hours ? `log: ${fmtHours(log.avg_cycle_time_hours)}` : undefined} />
          <SummaryTile label="Avg wait time"
            base={fmtHours(b.avg_waiting_time_hours)}
            tobe={s ? fmtHours(s.avg_waiting_time_hours) : null}
            baseNum={b.avg_waiting_time_hours} tobeNum={s?.avg_waiting_time_hours}
            sub={`${fmtPct(b.waiting_pct_of_cycle)} of cycle`} />
          <SummaryTile label="Rework rate"
            base={fmtPct(b.rework_rate_pct)}
            tobe={s ? fmtPct(s.rework_rate_pct) : null}
            baseNum={b.rework_rate_pct} tobeNum={s?.rework_rate_pct}
            sub={log?.rework_rate_pct ? `log: ${fmtPct(log.rework_rate_pct)}` : undefined} />
          <SummaryTile
            label="Top bottleneck"
            base={topBottleneck ? `${topBottleneck.total_wait_share_pct}% wait` : '—'}
            tobe={s && topBottleneck ? (() => { const tb = sBnMap.get(topBottleneck.activity); return tb ? `${tb.total_wait_share_pct}% wait` : null })() : null}
            baseNum={topBottleneck?.total_wait_share_pct}
            tobeNum={s && topBottleneck ? sBnMap.get(topBottleneck.activity)?.total_wait_share_pct : null}
            sub={topBottleneck?.activity} />
        </div>

        {/* ── Cycle Time + Wait Time side by side ── */}
        <div className="grid grid-cols-2 gap-3">
          <MetricTable title="⏱  Cycle Time" accent="bg-gray-50/50" hasLog={hasLog} hasTobe={hasTobe}
            rows={[
              { label: 'Mean', baseVal: b.avg_cycle_time_hours, tobeVal: s?.avg_cycle_time_hours, logVal: log?.avg_cycle_time_hours, fmt: fmtHours },
              { label: 'Median', baseVal: b.median_cycle_time_hours, tobeVal: s?.median_cycle_time_hours, logVal: log?.median_cycle_time_hours, fmt: fmtHours },
            ]} />
          <MetricTable title="⏳  Wait Time" accent="bg-gray-50/50" hasLog={false} hasTobe={hasTobe}
            rows={[
              { label: 'Mean wait', baseVal: b.avg_waiting_time_hours, tobeVal: s?.avg_waiting_time_hours, fmt: fmtHours },
              { label: 'Median wait', baseVal: b.median_waiting_time_hours, tobeVal: s?.median_waiting_time_hours, fmt: fmtHours },
              { label: 'Wait % of cycle', baseVal: b.waiting_pct_of_cycle, tobeVal: s?.waiting_pct_of_cycle, fmt: fmtPct },
            ]} />
        </div>

        {/* ── Rework compact card ── */}
        <ReworkCard b={b} s={s} log={log} hasTobe={hasTobe} />

        {/* ── Bottleneck + Resource Utilization side by side ── */}
        <div className={utilData.length > 0 ? 'grid grid-cols-2 gap-3 items-start' : ''}>

          {/* Bottleneck */}
          <div className="bg-white rounded-none border border-gray-100 shadow-sm overflow-hidden">
            <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50/40 flex items-center gap-2">
              <span className="font-semibold text-xs text-gray-700 tracking-wide uppercase">🚧  Bottlenecks</span>
              <span className="text-[10px] text-gray-400">by wait share</span>
              {s && (
                <div className="ml-auto flex items-center gap-3 text-[10px] text-gray-400">
                  <span className="flex items-center gap-1"><span className="w-2 h-1 rounded-none bg-gray-300 inline-block" />As-Is</span>
                  <span className="flex items-center gap-1"><span className="w-2 h-1 rounded-none bg-gray-600 inline-block" />To-Be</span>
                </div>
              )}
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-50">
                  <th className="text-left px-4 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Activity</th>
                  <SortTh col="mean_duration_min" activeCol={bnCol} dir={bnDir} onToggle={bnToggle} className="px-3 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wide text-right">Duration</SortTh>
                  <SortTh col="mean_wait_min" activeCol={bnCol} dir={bnDir} onToggle={bnToggle} className="px-3 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wide text-right">Wait</SortTh>
                  <SortTh col="total_wait_share_pct" activeCol={bnCol} dir={bnDir} onToggle={bnToggle} className="px-3 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wide text-right">Wait %</SortTh>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {sortedBn.map(row => {
                  const tb = s ? sBnMap.get(row.activity) : null
                  const sev = row.total_wait_share_pct >= 20 ? 'text-gray-800 font-bold' : row.total_wait_share_pct >= 10 ? 'text-gray-800 font-semibold' : 'text-gray-700'
                  const barC = row.total_wait_share_pct >= 20 ? 'bg-gray-400' : row.total_wait_share_pct >= 10 ? 'bg-gray-400' : 'bg-gray-300'
                  return (
                    <tr key={row.activity} className="hover:bg-gray-50/60 transition-colors">
                      <td className="px-4 py-2 font-medium text-gray-800 max-w-[130px] truncate">{row.activity}</td>
                      <td className="px-3 py-2 text-right">
                        {tb ? <BnDual asis={row.mean_duration_min} tobe={tb.mean_duration_min} fmt={v => `${Number(v).toFixed(0)}m`} />
                          : <span className="text-gray-600 tabular-nums">{row.mean_duration_min.toFixed(0)} m</span>}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {tb ? <BnDual asis={row.mean_wait_min} tobe={tb.mean_wait_min} fmt={v => `${Number(v).toFixed(0)}m`} />
                          : <span className="text-gray-600 tabular-nums">{row.mean_wait_min.toFixed(0)} m</span>}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-1.5">
                          {tb ? (
                            <>
                              <div className="w-10 bg-gray-100 rounded-none overflow-hidden" style={{ height: 10 }}>
                                <div className="bg-gray-300" style={{ width: `${Math.min(row.total_wait_share_pct, 100)}%`, height: 5 }} />
                                <div className="bg-gray-600" style={{ width: `${Math.min(tb.total_wait_share_pct, 100)}%`, height: 5 }} />
                              </div>
                              <BnDual asis={row.total_wait_share_pct} tobe={tb.total_wait_share_pct} fmt={v => `${v}%`} />
                            </>
                          ) : (
                            <>
                              <div className="w-10 bg-gray-100 rounded-none h-1.5 overflow-hidden">
                                <div className={`h-full rounded-none ${barC}`} style={{ width: `${Math.min(row.total_wait_share_pct, 100)}%` }} />
                              </div>
                              <span className={`tabular-nums ${sev}`}>{row.total_wait_share_pct}%</span>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Resource Utilization */}
          {utilData.length > 0 && (
            <div className="bg-white rounded-none border border-gray-100 shadow-sm overflow-hidden">
              <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50/40 flex items-center gap-2">
                <span className="font-semibold text-xs text-gray-700 tracking-wide uppercase">👤  Utilization</span>
                <span className="text-[10px] text-gray-400">top {utilData.length}</span>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-gray-50">
                    <th className="text-left px-4 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Resource</th>
                    <th className="text-right px-3 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">As-Is</th>
                    {hasTobe && <th className="text-right px-3 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">To-Be</th>}
                    <th className="px-4 py-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Load</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {utilSlice.map(({ res, util, tobe }) => {
                    const high = util >= 80
                    const mid = util >= 60 && util < 80
                    const barC = high ? 'bg-gray-400' : mid ? 'bg-gray-400' : 'bg-gray-400'
                    const txtC = high ? 'text-gray-800' : mid ? 'text-gray-800' : 'text-gray-700'
                    const d = tobe != null ? delta(util, tobe) : null
                    const good = d && !d.neutral && d.good
                    const bad = d && !d.neutral && !d.good
                    return (
                      <tr key={res} className="hover:bg-gray-50/60 transition-colors">
                        <td className="px-4 py-2 font-medium text-gray-700 max-w-[110px] truncate">{res}</td>
                        <td className="px-3 py-2 text-right">
                          <span className={`font-bold tabular-nums ${txtC}`}>{fmtPct(util)}</span>
                        </td>
                        {hasTobe && (
                          <td className="px-3 py-2 text-right">
                            {tobe != null ? (
                              <div className="flex items-center justify-end gap-1">
                                <span className={`font-bold tabular-nums ${good ? 'text-gray-800' : bad ? 'text-gray-800' : 'text-gray-900'}`}>{fmtPct(tobe)}</span>
                                <DeltaChip from={util} to={tobe} />
                              </div>
                            ) : <span className="text-gray-300">—</span>}
                          </td>
                        )}
                        <td className="px-4 py-2">
                          <div className="flex items-center gap-1.5">
                            <div className="w-16 bg-gray-100 rounded-none overflow-hidden" style={{ height: hasTobe && tobe != null ? 10 : 5 }}>
                              <div className={`${barC} rounded-none`} style={{ width: `${Math.min(util, 100)}%`, height: hasTobe && tobe != null ? 5 : 5 }} />
                              {hasTobe && tobe != null && (
                                <div className={`${good ? 'bg-gray-400' : bad ? 'bg-gray-400' : 'bg-gray-400'} rounded-none`}
                                  style={{ width: `${Math.min(tobe, 100)}%`, height: 5 }} />
                              )}
                            </div>
                            {high && <span className="text-[9px] text-gray-600 font-bold uppercase tracking-wide">overloaded</span>}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <Paginator total={utilData.length} page={utilPage} pageSize={10} onPage={setUtilPage} />
            </div>
          )}
        </div>

        {/* ── Collapsibles ── */}
        {s && s.modification_audit && s.modification_audit.length > 0 && (
          <ModAudit audit={s.modification_audit} />
        )}

        <Collapsible title="Activity Time &amp; Wait Breakdown" defaultOpen={true}>
          <div className="p-5 grid grid-cols-1 lg:grid-cols-2 gap-5">
            {([
              { key: 'activity_time_breakdown' as const, label: 'Service time share', color: '#9ca3af', tobe: '#374151' },
              { key: 'activity_wait_breakdown' as const, label: 'Wait time share', color: '#a3a3a3', tobe: '#404040' },
            ] as const).map(({ key, label, color, tobe }) => {
              const base = b[key]; const cmp = s?.[key]
              const items = useMemo(() => {
                const all = Array.from(new Set([...Object.keys(base), ...Object.keys(cmp ?? {})]))
                return all.map(n => ({ name: n, asis: base[n] ?? 0, tobe: cmp?.[n] ?? 0 })).sort((a, b2) => b2.asis - a.asis).slice(0, 10)
              // eslint-disable-next-line react-hooks/exhaustive-deps
              }, [base, cmp])
              return (
                <div key={key}>
                  <p className="text-xs font-semibold text-gray-600 mb-3 uppercase tracking-wide">{label}</p>
                  <ResponsiveContainer width="100%" height={cmp ? 280 : 200}>
                    <BarChart data={items} layout="vertical" margin={{ left: 8, right: 16, top: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f0f0f0" />
                      <XAxis type="number" unit="%" tick={{ fontSize: 10 }} stroke="#e5e7eb" />
                      <YAxis type="category" dataKey="name" width={130} tick={{ fontSize: 10, fill: '#6b7280' }} stroke="none" />
                      <Tooltip formatter={(v) => [`${Number(v).toFixed(1)}%`, '']} />
                      {cmp && <Legend iconType="square" iconSize={8} wrapperStyle={{ fontSize: 10, paddingTop: 8 }} />}
                      <Bar dataKey="asis" name="As-Is" radius={[0, 3, 3, 0]} fill={color} barSize={cmp ? 6 : 12} />
                      {cmp && <Bar dataKey="tobe" name="To-Be" radius={[0, 3, 3, 0]} fill={tobe} barSize={6} />}
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )
            })}
          </div>
        </Collapsible>

      </div>
    </div>
  )
}
