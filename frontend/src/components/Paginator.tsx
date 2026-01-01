import { ChevronLeft, ChevronRight } from 'lucide-react'

interface Props {
  total: number
  page: number
  pageSize: number
  onPage: (p: number) => void
}

export function usePagination<T>(items: T[], page: number, pageSize = 10) {
  const pageCount = Math.ceil(items.length / pageSize)
  const slice = items.slice(page * pageSize, (page + 1) * pageSize)
  return { slice, pageCount }
}

export default function Paginator({ total, page, pageSize, onPage }: Props) {
  const pageCount = Math.ceil(total / pageSize)
  if (pageCount <= 1) return null

  const start = page * pageSize + 1
  const end = Math.min((page + 1) * pageSize, total)

  // Show at most 5 page buttons, centered around current page
  const range: number[] = []
  const half = 2
  let lo = Math.max(0, page - half)
  let hi = Math.min(pageCount - 1, page + half)
  if (hi - lo < 4) {
    if (lo === 0) hi = Math.min(pageCount - 1, 4)
    else lo = Math.max(0, hi - 4)
  }
  for (let i = lo; i <= hi; i++) range.push(i)

  return (
    <div className="flex items-center justify-between px-4 py-2 border-t border-gray-50">
      <span className="text-[11px] text-gray-400 tabular-nums">
        {start}–{end} of {total}
      </span>
      <div className="flex items-center gap-0.5">
        <button
          onClick={() => onPage(Math.max(0, page - 1))}
          disabled={page === 0}
          className="p-1 rounded-none hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronLeft className="w-3.5 h-3.5 text-gray-500" />
        </button>
        {lo > 0 && (
          <>
            <button onClick={() => onPage(0)} className="w-6 h-6 rounded-none text-[11px] text-gray-500 hover:bg-gray-100 transition-colors">1</button>
            {lo > 1 && <span className="text-[11px] text-gray-300 px-0.5">…</span>}
          </>
        )}
        {range.map(i => (
          <button key={i} onClick={() => onPage(i)}
            className={`w-6 h-6 rounded-none text-[11px] font-medium transition-colors ${i === page ? 'bg-gray-800 text-white' : 'text-gray-500 hover:bg-gray-100'}`}>
            {i + 1}
          </button>
        ))}
        {hi < pageCount - 1 && (
          <>
            {hi < pageCount - 2 && <span className="text-[11px] text-gray-300 px-0.5">…</span>}
            <button onClick={() => onPage(pageCount - 1)} className="w-6 h-6 rounded-none text-[11px] text-gray-500 hover:bg-gray-100 transition-colors">{pageCount}</button>
          </>
        )}
        <button
          onClick={() => onPage(Math.min(pageCount - 1, page + 1))}
          disabled={page === pageCount - 1}
          className="p-1 rounded-none hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronRight className="w-3.5 h-3.5 text-gray-500" />
        </button>
      </div>
    </div>
  )
}
