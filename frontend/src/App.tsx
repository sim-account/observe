import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import UploadPage from '@/pages/Upload'
import Workspace from '@/pages/Workspace'
import ProcessBehaviour from '@/pages/ProcessBehaviour'
import Observations from '@/pages/Observations'
import ScenarioBuilder from '@/pages/ScenarioBuilder'
import Results from '@/pages/Results'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/upload" replace />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/workspace" element={<Workspace />}>
          <Route path="learnings" element={<ProcessBehaviour />} />
          <Route path="observations" element={<Observations />} />
          <Route path="scenario" element={<ScenarioBuilder />} />
          <Route path="results" element={<Results />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
