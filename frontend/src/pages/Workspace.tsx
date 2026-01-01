import { NavLink, Navigate, Outlet, useNavigate } from 'react-router-dom'
import { useSession } from '@/store/sessionStore'
import { BarChart2, Eye, Sliders, LayoutDashboard, Upload } from 'lucide-react'

const NAV = [
  { to: '/workspace/learnings', icon: BarChart2, label: 'Simulation parameters' },
  { to: '/workspace/observations', icon: Eye, label: 'Observations' },
  { to: '/workspace/scenario', icon: Sliders, label: 'Scenario Builder' },
  { to: '/workspace/results', icon: LayoutDashboard, label: 'Results' },
]

export default function Workspace() {
  const navigate = useNavigate()
  const { sessionId, modelSummary } = useSession()
  if (!sessionId) return <Navigate to="/upload" replace />

  return (
    <div className="h-screen bg-gray-50 flex flex-col overflow-hidden">
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-5">
        <span className="font-semibold text-gray-900 text-sm">OBSERVE</span>
        <nav aria-label="Workspace" className="flex items-center gap-1 flex-1">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink key={to} to={to} className={({ isActive }) =>
              'flex items-center gap-2 px-3 py-2 rounded-none text-sm font-medium transition-colors ' +
                (isActive ? 'bg-gray-900 text-white' : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100')}>
              <Icon className="w-4 h-4" />{label}
            </NavLink>
          ))}
        </nav>
        {modelSummary && (
          <span className="text-xs text-gray-500">
            {modelSummary.activities.length} activities · {modelSummary.resources.length} resources
          </span>
        )}
        <button onClick={() => navigate('/upload')}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-none hover:bg-gray-100 transition-colors">
          <Upload className="w-3.5 h-3.5" />New upload
        </button>
      </header>
      <main className="flex-1 min-h-0 overflow-y-auto"><Outlet /></main>
    </div>
  )
}
