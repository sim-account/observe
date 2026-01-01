import { useState, useMemo } from 'react'
import { ChevronUp, ChevronDown } from 'lucide-react'

export function useSortable<T>(data: T[], defaultCol: string, defaultDir: 'asc' | 'desc' = 'desc') {
  const [col, setCol] = useState(defaultCol)
  const [dir, setDir] = useState<'asc' | 'desc'>(defaultDir)

  const sorted = useMemo(() => {
    return [...data].sort((a, b) => {
      const av = Number((a as Record<string, unknown>)[col] ?? 0)
      const bv = Number((b as Record<string, unknown>)[col] ?? 0)
      return dir === 'desc' ? bv - av : av - bv
    })
  }, [data, col, dir])

  const toggle = (newCol: string) => {
    if (newCol === col) setDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setCol(newCol); setDir('desc') }
  }

  return { sorted, col, dir, toggle }
}

export function SortTh({ col, activeCol, dir, onToggle, children, className = 'px-4 py-3 font-medium text-right' }: {
  col: string
  activeCol: string
  dir: 'asc' | 'desc'
  onToggle: (col: string) => void
  children: React.ReactNode
  className?: string
}) {
  const active = col === activeCol
  return (
    <th className={className}>
      <button className="flex items-center gap-1 group ml-auto" onClick={() => onToggle(col)}>
        {children}
        {active
          ? (dir === 'desc' ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />)
          : <ChevronDown className="w-3.5 h-3.5 opacity-0 group-hover:opacity-40" />}
      </button>
    </th>
  )
}
