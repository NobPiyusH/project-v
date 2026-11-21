import { useState } from 'react'
import { api } from './api'
import { BackendPower } from './BackendPower'
import { BudgetMeters } from './BudgetMeters'
import { GpuPanel } from './GpuPanel'
import { autopilotApi } from './autopilotApi'
import { Autopilot } from './pages/Autopilot'
import { useHashRoute, usePoll, useTheme } from './hooks'
import { Home } from './pages/Home'
import { Providers } from './pages/Providers'
import { DreamShorts } from './pages/dream/DreamShorts'
import { VideoDetail } from './pages/VideoDetail'

export default function App() {
  const route = useHashRoute()
  const [theme, toggleTheme] = useTheme()
  const [gpuOpen, setGpuOpen] = useState(false)
  const { data: stages, error } = usePoll(api.stages, 60000)
  const { data: budget } = usePoll(api.budget, 10000)
  const { data: videos } = usePoll(api.videos, 5000)
  const videoMatch = route.match(/^#\/video\/([0-9a-f-]{36})/)
  const dreamMatch = route.match(/^#\/dream\/([0-9a-f-]{36})/)
  const needsYou = (videos ?? []).filter((v) => v.status === 'WAITING').length
  const { data: autoRuns } = usePoll(autopilotApi.runs, 30000)
  const readyToPost = (autoRuns ?? []).filter((r) => r.status === 'READY').length

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/"><span className="logo" aria-hidden="true" />Project V</a>
        <nav className="nav">
          <a href="#/" className={route === '#/' || route === '' || videoMatch ? 'active' : ''}>
            Studio{needsYou > 0 && <span className="needs-dot" title={`${needsYou} video(s) waiting for you`}>{needsYou}</span>}
          </a>
          <a href="#/providers" className={route.startsWith('#/providers') ? 'active' : ''}>Providers</a>
          <a href="#/dream" className={route.startsWith('#/dream') ? 'active' : ''}>Dream Shorts</a>
          <a href="#/autopilot" className={route.startsWith('#/autopilot') ? 'active' : ''}>
            Autopilot{readyToPost > 0 && <span className="needs-dot" title={`${readyToPost} Short(s) ready to post`}>{readyToPost}</span>}
          </a>
        </nav>
        <span className="spacer" />
        {budget && <BudgetMeters budget={budget} />}
        <BackendPower />
        <button className={`icon-btn ${gpuOpen ? 'primary' : ''}`} onClick={() => setGpuOpen((o) => !o)}
          title="Live log of the GPU pods on RunPod (only fetched while open)" aria-expanded={gpuOpen}>GPU logs</button>
        <button className="icon-btn" onClick={toggleTheme} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
          aria-label="Toggle theme">{theme === 'dark' ? '☀' : '☾'}</button>
      </header>
      <main>
        {error && !stages && <p className="error">Can't reach the control plane at /api. Press <strong>Start backend</strong> at the top right (it takes 1-2 minutes). ({error})</p>}
        {stages && (videoMatch
          ? <VideoDetail id={videoMatch[1]} stages={stages} />
          : route.startsWith('#/providers') ? <Providers />
          : route.startsWith('#/autopilot') ? <Autopilot />
          : dreamMatch ? <DreamShorts id={dreamMatch[1]} />
          : route.startsWith('#/dream') ? <DreamShorts /> : <Home stages={stages} videos={videos} />)}
      </main>
      {gpuOpen && <GpuPanel onClose={() => setGpuOpen(false)} />}
    </>
  )
}
