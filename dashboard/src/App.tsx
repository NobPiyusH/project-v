import { api } from './api'
import { useHashRoute, usePoll } from './hooks'
import { Providers } from './pages/Providers'
import { VideoDetail } from './pages/VideoDetail'
import { VideoList } from './pages/VideoList'

export default function App() {
  const route = useHashRoute()
  const { data: stages, error } = usePoll(api.stages, 60000)
  const { data: system } = usePoll(api.system, 15000)
  const videoMatch = route.match(/^#\/video\/([0-9a-f-]{36})/)

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/">Project V Studio</a>
        <nav className="nav">
          <a href="#/" className={route === '#/' || videoMatch ? 'active' : ''}>Videos</a>
          <a href="#/providers" className={route.startsWith('#/providers') ? 'active' : ''}>Providers</a>
        </nav>
        <span className="spacer" />
        {system && <span className="pill">Planner: {system.planner}</span>}
      </header>
      <main>
        {error && !stages && <p className="error">Can't reach the control plane at /api. Is it running? ({error})</p>}
        {stages && (videoMatch
          ? <VideoDetail id={videoMatch[1]} stages={stages} />
          : route.startsWith('#/providers') ? <Providers /> : <VideoList stages={stages} />)}
      </main>
    </>
  )
}
