import { useEffect, useState } from 'react'
import { api } from './api'
import { useHashRoute, usePoll } from './hooks'
import { Home } from './pages/Home'
import { Providers } from './pages/Providers'
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

export default function App() {
  const route = useHashRoute()
  const [theme, toggleTheme] = useTheme()
  const { data: stages, error } = usePoll(api.stages, 60000)
  const { data: budget } = usePoll(api.budget, 10000)
  const { data: videos } = usePoll(api.videos, 5000)
  const videoMatch = route.match(/^#\/video\/([0-9a-f-]{36})/)
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
        <button className="icon-btn" onClick={toggleTheme} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
          aria-label="Toggle theme">{theme === 'dark' ? '☀' : '☾'}</button>
      </header>
      <main>
        {error && !stages && <p className="error">Can't reach the control plane at /api. Is it running? ({error})</p>}
        {stages && (videoMatch
          ? <VideoDetail id={videoMatch[1]} stages={stages} />
          : route.startsWith('#/providers') ? <Providers /> : <Home stages={stages} videos={videos} />)}
      </main>
    </>
  )
}
