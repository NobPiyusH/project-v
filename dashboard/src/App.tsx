import { useEffect, useState } from 'react'
import { api, studio } from './api'
import { useHashRoute, usePoll } from './hooks'
import { Home } from './pages/Home'
import { Providers } from './pages/Providers'
import { DreamShorts } from './pages/DreamShorts'
import { VideoDetail } from './pages/VideoDetail'

type Theme = 'dark' | 'light'

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return localStorage.getItem('pv-theme') === 'light' ? 'light' : 'dark'
    } catch {
      return 'dark'
    }
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('pv-theme', theme)
    } catch {
      /* private mode: the choice just isn't remembered */
    }
  }, [theme])
  return [theme, () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))]
}

function Meter({ label, spent, cap, title }: { label: string; spent: number; cap: number; title: string }) {
  const pct = cap > 0 ? Math.min(100, (spent / cap) * 100) : 0
  return (
    <div className={`meter ${pct > 85 ? 'hot' : ''}`} title={title}>
      <span>{label} ₹{spent.toFixed(0)} / ₹{cap.toFixed(0)}</span>
      <div className="bar"><div className="fill" style={{ width: `${pct}%` }} /></div>
    </div>
  )
}

function UsdMeter({ label, spent, cap, title }: { label: string; spent: number; cap: number; title: string }) {
  const pct = cap > 0 ? Math.min(100, (spent / cap) * 100) : 0
  return (
    <div className={`meter ${pct > 85 ? 'hot' : ''}`} title={title}>
      <span>{label} ${spent.toFixed(2)} / ${cap.toFixed(0)} credit</span>
      <div className="bar"><div className="fill" style={{ width: `${pct}%` }} /></div>
    </div>
  )
}

/** Backend power: shows whether the control plane and media worker are up, with a Start or Restart button. */
function BackendPower() {
  // Cheap to poll: the dev server caches the worker check; the control-plane check is one local request.
  const { data: st, refresh } = usePoll(studio.status, 4000)
  const [err, setErr] = useState<string | null>(null)
  if (!st) return null
  const up = st.controlPlane && st.worker
  const act = (a: 'start' | 'restart') => {
    if (a === 'restart' && !window.confirm('Restart the backend? Running jobs pause for a minute or two and then carry on.')) return
    setErr(null)
    void studio.act(a).then(refresh).catch((e: Error) => setErr(e.message))
  }
  const parts = [`Control plane: ${st.controlPlane ? 'running' : 'stopped'}`, `Media worker: ${st.worker ? 'running' : 'stopped'}`]
  return (
    <div className="power" title={parts.join('\n') + (st.lastOutput ? `\n\nLast run:\n${st.lastOutput.slice(-600)}` : '')}>
      <span className={`dot ${st.busy ? 'busy' : up ? 'up' : 'down'}`} aria-hidden="true" />
      {st.busy
        ? <span className="small">Backend {st.busy}… (1-2 min)</span>
        : up
          ? <button className="icon-btn" onClick={() => act('restart')}>Restart backend</button>
          : <button className="primary icon-btn" onClick={() => act('start')}>
              Start backend{st.controlPlane || st.worker ? ` (${st.controlPlane ? 'worker' : 'control plane'} is down)` : ''}
            </button>}
      {err && <span className="small" style={{ color: 'var(--bad)' }}>{err}</span>}
    </div>
  )
}

export default function App() {
  const route = useHashRoute()
  const [theme, toggleTheme] = useTheme()
  const { data: stages, error } = usePoll(api.stages, 60000)
  const { data: budget } = usePoll(api.budget, 10000)
  const { data: videos } = usePoll(api.videos, 5000)
  const videoMatch = route.match(/^#\/video\/([0-9a-f-]{36})/)
  const dreamMatch = route.match(/^#\/dream\/([0-9a-f-]{36})/)
  const needsYou = (videos ?? []).filter((v) => v.status === 'WAITING').length

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
        </nav>
        <span className="spacer" />
        {budget && (
          <div className="meters">
            <Meter label="Gemini" spent={Number(budget.geminiSpentInr)} cap={Number(budget.geminiCapInr)}
              title="Paid Gemini spend this month (the studio's own hard cap)" />
            {budget.modal
              ? <UsdMeter label="Modal" spent={budget.modal.meteredUsd} cap={budget.modal.freeCreditUsd}
                  title={`Modal's own bill this month: $${budget.modal.meteredUsd.toFixed(2)} used, $${budget.modal.creditLeftUsd.toFixed(2)} of the $${budget.modal.freeCreditUsd.toFixed(0)} free credit left, $${budget.modal.billedUsd.toFixed(2)} billed (updated ${new Date(budget.modal.fetchedAt).toLocaleTimeString()}). Studio's own GPU estimate: ₹${Number(budget.gpuSpentInr ?? 0).toFixed(0)}.`} />
              : <Meter label="GPU" spent={Number(budget.gpuSpentInr ?? 0)} cap={Number(budget.gpuCapInr ?? 0)}
                  title="Modal GPU spend this month recorded by the studio (cap = modal.monthlyCapUsd)" />}
          </div>
        )}
        <BackendPower />
        <button className="icon-btn" onClick={toggleTheme} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
          aria-label="Toggle theme">{theme === 'dark' ? '☀' : '☾'}</button>
      </header>
      <main>
        {error && !stages && <p className="error">Can't reach the control plane at /api. Press <strong>Start backend</strong> at the top right (it takes 1-2 minutes). ({error})</p>}
        {stages && (videoMatch
          ? <VideoDetail id={videoMatch[1]} stages={stages} />
          : route.startsWith('#/providers') ? <Providers />
          : dreamMatch ? <DreamShorts id={dreamMatch[1]} />
          : route.startsWith('#/dream') ? <DreamShorts /> : <Home stages={stages} videos={videos} />)}
      </main>
    </>
  )
}
