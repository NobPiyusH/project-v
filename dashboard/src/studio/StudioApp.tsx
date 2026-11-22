import { useState } from 'react'
import { api } from '../api'
import { BackendPower } from '../BackendPower'
import { BudgetMeters } from '../BudgetMeters'
import { usePoll, type Theme } from '../hooks'
import { studio } from '../api'
import { Create } from './Create'
import { ProjectPage } from './ProjectPage'
import { Settings } from './Settings'
import { StudioList } from './StudioList'
import { useProjects } from './useProjects'
import { inr } from './format'
import './studio.css'

/** The new three-area shell (Create / Studio / Settings). Old pages stay reachable through the Classic view link. */
export function StudioApp({ route, theme, toggleTheme }: { route: string; theme: Theme; toggleTheme: () => void }) {
  const { rows, error, stages } = useProjects()
  const { data: budget } = usePoll(api.budget, 10000)
  const { data: power } = usePoll(studio.status, 5000)
  const [meterOpen, setMeterOpen] = useState(false)
  const needs = (rows ?? []).filter((r) => r.status === 'needs' || r.status === 'failed').length
  const project = route.match(/^#\/p\/(short|video)\/([0-9a-f-]{36})/)
  const area = route.startsWith('#/create') ? 'create' : route.startsWith('#/settings') ? 'settings' : 'studio'
  const month = budget ? Number(budget.geminiSpentInr) + Number(budget.gpuSpentInr ?? 0) + Number(budget.claudeSpentInr ?? 0) : null
  const down = power && !(power.controlPlane && power.worker)

  return (
    <>
      <header className="sv-top">
        <a className="brand" href="#/studio"><span className="logo" aria-hidden="true" />Project V</a>
        <nav className="sv-nav" aria-label="Main">
          <a href="#/create" className={area === 'create' ? 'on' : ''}>Create</a>
          <a href="#/studio" className={area === 'studio' ? 'on' : ''}>Studio{needs > 0 && <span className="needs-dot" title={`${needs} need you`}>{needs}</span>}</a>
          <a href="#/settings" className={area === 'settings' ? 'on' : ''}>Settings</a>
        </nav>
        <span className="spacer" />
        {month != null && <button type="button" className="pill" onClick={() => setMeterOpen((o) => !o)} aria-expanded={meterOpen}
          title="Spend this month (Gemini + GPU + Claude). Tap for the caps.">Month {inr(month)}</button>}
        {down ? <BackendPower /> : power && <a className="pill" href="#/settings/backend" title="Backend power is under Settings">Backend ● on</a>}
        <a className="pill" href="#/" title="The old dashboard, unchanged">Classic view</a>
        <button className="icon-btn" onClick={toggleTheme} aria-label="Toggle theme" title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>{theme === 'dark' ? '☀' : '☾'}</button>
      </header>
      {meterOpen && budget && <div className="sv-main" style={{ paddingBottom: 0 }}><section className="card"><BudgetMeters budget={budget} /></section></div>}
      <main className="sv-main">
        {error && !rows && <p className="error">Can't reach the control plane at /api. Start the backend (Settings → Backend). ({error})</p>}
        {area === 'create' && <Create rows={rows ?? []} />}
        {area === 'settings' && <Settings route={route} />}
        {area === 'studio' && (project && stages
          ? <ProjectPage kind={project[1] as 'short' | 'video'} id={project[2]} stages={stages} />
          : project ? <p className="muted">Loading…</p> : <StudioList rows={rows} />)}
      </main>
    </>
  )
}
