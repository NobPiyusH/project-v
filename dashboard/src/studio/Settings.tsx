import { api, type GpuBrokerProvider } from '../api'
import { BackendPower } from '../BackendPower'
import { BudgetMeters } from '../BudgetMeters'
import { dreamApi } from '../dreamApi'
import { GpuLogs } from '../GpuPanel'
import { usePoll } from '../hooks'
import { Autopilot } from '../pages/Autopilot'
import { ProducerRules } from '../pages/ProducerCard'
import { Providers } from '../pages/Providers'
import { RouterCard } from '../pages/Routing'
import { Lessons } from '../pages/dream/Lessons'
import { inr } from './format'

const TABS = [['gpu', 'GPU'], ['providers', 'Providers'], ['routing', 'Routing'], ['budget', 'Budget'], ['autopilot', 'Autopilot'], ['producer', 'Producer rules'], ['lessons', 'Lessons'], ['backend', 'Backend']] as const
type Tab = (typeof TABS)[number][0]

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '–')

function GpuSettings() {
  const { data: broker } = usePoll(dreamApi.gpuEnabled, 15000)
  const { data: providers, error } = usePoll(api.gpuBrokerProviders, 30000)
  const { data: options } = usePoll(dreamApi.gpuOptions, 60000)
  const { data: wd } = usePoll(api.gpuWatchdog, 15000)
  const live = (wd?.leases ?? []).filter((l) => !l.released_at)
  const state = (p: GpuBrokerProvider) => !p.configured ? ['not set up', 'b-idle'] : p.proven ? ['proven', 'b-ok'] : p.preflightOk ? ['quote ok, not proven', 'b-info'] : p.preflightOk === false ? ['preflight failed', 'b-bad'] : ['not checked', 'b-idle']
  return (
    <>
      <section className="card">
        <div className="card-head"><h2>GPU today</h2>
          {broker?.today ? <span className="pill">${broker.today.spentUsd.toFixed(2)} of ${broker.today.capUsd.toFixed(2)} cap (UTC day)</span> : <span className="pill">{broker?.enabled === false ? 'GPU broker off' : 'loading'}</span>}</div>
        <p className="small muted" style={{ marginTop: 0 }}>Past the cap nothing new is rented and the Short waits for you. Pods turn off by themselves when idle; a pod still running shows below.</p>
        {wd && <p className="small" style={{ margin: 0 }}>{live.length === 0 ? <span className="st-done">● No pod leased right now. Nothing is billing.</span>
          : <span className="st-needs">● {live.length} pod{live.length === 1 ? '' : 's'} leased: {live.map((l) => `${l.provider} ${l.name} (since ${when(l.created_at)})`).join('; ')}</span>}</p>}
      </section>
      <section className="card">
        <div className="card-head"><h2>Providers</h2><span className="muted small">Preflight and canary runs stay where they are today (a canary rents a real pod)</span></div>
        {error && <p className="error">{error}</p>}
        <div style={{ overflowX: 'auto' }}><table>
          <thead><tr><th>Provider</th><th>State</th><th className="hide-sm">Last preflight</th><th>Month cap</th><th className="hide-sm">Auto search</th></tr></thead>
          <tbody>{(providers ?? []).map((p) => { const [t, c] = state(p); return (
            <tr key={p.provider}><td><strong>{p.provider}</strong>{p.provenNote && <div className="muted small">{p.provenNote}</div>}</td>
              <td><span className={`badge ${c}`}>{t}</span></td><td className="hide-sm small">{when(p.preflightAt)}{p.preflightNote && <div className="muted">{p.preflightNote}</div>}</td>
              <td>${p.monthlyCapUsd.toFixed(2)}</td><td className="hide-sm">{p.inAutoSearch ? 'yes' : 'no'}</td></tr>) })}</tbody>
        </table></div>
      </section>
      <section className="card">
        <div className="card-head"><h2>Prices now</h2><span className="muted small">Live, cheapest first as the broker sees it</span></div>
        {(options ?? []).length === 0 ? <p className="muted small">No quotes yet.</p> : (
          <div style={{ overflowX: 'auto' }}><table className="small"><tbody>{(options ?? []).map((o, i) => (
            <tr key={`${o.provider}-${o.key ?? i}`} style={o.available ? undefined : { opacity: 0.55 }}><td>{o.provider}</td><td>{o.gpu ? o.gpu.replace('|', ' ') : '–'}</td>
              <td>{o.available ? `$${o.hourlyUsd.toFixed(2)}/h` : '–'}</td><td>{o.available ? o.stock : (o.why ?? 'unavailable')}</td></tr>))}</tbody></table></div>)}
      </section>
      <section className="card"><div className="card-head"><h2>Live pod logs</h2><span className="muted small">Only fetched while this tab is open</span></div>
        <div className="gpu-embed"><GpuLogs /></div></section>
    </>
  )
}

function BudgetSettings() {
  const { data: b } = usePoll(api.budget, 10000)
  if (!b) return <p className="muted">Loading…</p>
  const rows: [string, string][] = [
    ['Gemini this month', `${inr(Number(b.geminiSpentInr))} of ${inr(Number(b.geminiCapInr))} (${inr(Number(b.geminiRemainingInr))} left)`],
    ['Claude this month', b.claudeCapInr != null ? `${inr(Number(b.claudeSpentInr ?? 0))} of ${inr(Number(b.claudeCapInr))}` : 'no cap set'],
    ['GPU this month (studio estimate)', Number(b.gpuCapInr) > 0 ? `${inr(Number(b.gpuSpentInr))} of ${inr(Number(b.gpuCapInr))}` : `${inr(Number(b.gpuSpentInr))} (no monthly cap; the daily GPU cap is under GPU)`],
  ]
  if (b.modal) rows.push(['Modal (own bill)', `$${b.modal.meteredUsd.toFixed(2)} used, $${b.modal.creditLeftUsd.toFixed(2)} credit left`])
  return (
    <section className="card">
      <div className="card-head"><h2>Budget</h2><span className="muted small">Caps live in config/providers.yaml and .env; they are shown here, not edited</span></div>
      <div style={{ marginBottom: 12 }}><BudgetMeters budget={b} /></div>
      <div className="sv-kv">{rows.map(([k, v]) => <div key={k} style={{ display: 'contents' }}><span>{k}</span><span className="num">{v}</span></div>)}</div>
    </section>
  )
}

/** Admin moved out of the way: every tab is today's panel, unchanged, or a small read-out of what the API gives. */
export function Settings({ route }: { route: string }) {
  const part = route.replace(/^#\/settings\/?/, '').split(/[/?]/)[0]
  const tab: Tab = (TABS.find(([k]) => k === part)?.[0]) ?? 'gpu'
  return (
    <>
      <h1>Settings</h1>
      <div className="sv-chips" role="tablist" aria-label="Settings">
        {TABS.map(([k, label]) => <a key={k} role="tab" aria-selected={tab === k} className={`sv-chip button ${tab === k ? 'on' : ''}`} href={`#/settings/${k}`}>{label}</a>)}
      </div>
      {tab === 'gpu' && <GpuSettings />}
      {tab === 'providers' && <Providers withRouter={false} />}
      {tab === 'routing' && <RouterCard />}
      {tab === 'budget' && <BudgetSettings />}
      {tab === 'autopilot' && <Autopilot />}
      {tab === 'producer' && <section className="card"><div className="card-head"><h2>Producer rules</h2><span className="muted small">Apply to every Video</span></div><ProducerRules /></section>}
      {tab === 'lessons' && <><Lessons /><p className="muted small">Lessons appear after you review a finished Short.</p></>}
      {tab === 'backend' && (
        <section className="card"><div className="card-head"><h2>Backend power</h2></div>
          <p className="small muted" style={{ marginTop: 0 }}>The control plane and media worker. If the backend is down, the Start button also shows in the top bar.</p><BackendPower /></section>
      )}
    </>
  )
}
