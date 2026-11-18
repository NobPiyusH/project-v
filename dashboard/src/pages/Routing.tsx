import { Fragment, useState } from 'react'
import { api, type RoutingDecision } from '../api'
import { usePoll } from '../hooks'

/** M3b: the router's providers (health, budget bucket, owner switch) and how much of each budget is left. */
export function RouterCard() {
  const { data: providers, error, refresh } = usePoll(api.routerProviders, 10000)
  const { data: budgets } = usePoll(api.routerBudgets, 30000)
  const [busy, setBusy] = useState<string | null>(null)

  async function act(id: string, fn: () => Promise<void>) {
    setBusy(id)
    try {
      await fn()
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Router</h2>
        <span className="pill">Quality floor first, then quality minus cost · cheaper as a budget runs low</span>
      </div>
      {error && <p className="error">{error}</p>}
      {budgets && (
        <div className="row small" style={{ gap: 16, flexWrap: 'wrap', marginBottom: 12 }}>
          {budgets.map((b) => (
            <span key={b.bucket} title={`Cost weight λ = ${b.lambda} quality points per ₹`}>
              <strong>{b.bucket}</strong> budget left: {(b.shareLeft * 100).toFixed(0)}%
            </span>
          ))}
        </div>
      )}
      {providers && (
        <table>
          <thead><tr><th>Provider</th><th>Quality</th><th className="hide-sm">Budget</th><th>Health</th><th className="hide-sm">Chosen this month</th><th>Use</th></tr></thead>
          <tbody>
            {providers.map((p) => {
              const cooling = p.cooldown_until && new Date(p.cooldown_until) > new Date()
              const on = p.enabled && !p.owner_disabled
              return (
                <tr key={p.id}>
                  <td><strong>{p.name}</strong><div className="muted small"><code>{p.id}</code> · {(JSON.parse(p.capabilities) as string[]).join(', ')}</div></td>
                  <td>{p.quality}/10</td>
                  <td className="hide-sm">{p.budget}</td>
                  <td>
                    {cooling
                      ? <span className="badge b-warn" title={p.last_error ?? ''}>Cooling until {new Date(p.cooldown_until!).toLocaleTimeString()}</span>
                      : p.fails ? <span className="badge b-idle" title={p.last_error ?? ''}>{p.fails} recent failure(s)</span>
                        : <span className="badge b-ok">Healthy</span>}
                    {(cooling || p.fails) ? (
                      <button className="small" style={{ marginLeft: 6 }} disabled={busy === p.id} onClick={() => void act(p.id, () => api.resetProvider(p.id))}>reset</button>
                    ) : null}
                  </td>
                  <td className="hide-sm">{p.chosen_this_month}</td>
                  <td>
                    {!p.enabled ? <span className="badge b-idle" title="Disabled in config/providers.yaml">Off (config)</span> : (
                      <button disabled={busy === p.id} onClick={() => void act(p.id, () => api.setProviderOn(p.id, !on))}
                        title={on ? 'Stop routing jobs to this provider' : 'Let the router use this provider again'}>
                        {on ? 'On · switch off' : 'Off · switch on'}
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}

/** M3b: "why this provider?" for one video, newest first. */
export function RoutingDecisionsCard({ videoId }: { videoId: string }) {
  const { data: decisions } = usePoll(() => api.routing(videoId), 15000, [videoId])
  const [open, setOpen] = useState<string | null>(null)
  return (
    <section className="card">
      <div className="card-head"><h2>Routing decisions</h2><span className="pill">Which provider ran each job, and why</span></div>
      {!decisions?.length ? <p className="muted small">No routing decisions yet for this video.</p> : (
        <table>
          <thead><tr><th>When</th><th>Stage</th><th>Chosen</th><th className="hide-sm">Why</th></tr></thead>
          <tbody>
            {decisions.map((d: RoutingDecision) => {
              const cands = JSON.parse(d.candidates || '[]') as Record<string, unknown>[]
              return (
                <Fragment key={d.id}>
                  <tr onClick={() => setOpen(open === d.id ? null : d.id)} style={{ cursor: cands.length ? 'pointer' : undefined }}>
                    <td className="small">{new Date(d.created_at).toLocaleString()}</td>
                    <td className="small">{d.stage}<div className="muted">{d.capability}</div></td>
                    <td>{d.chosen_provider_id ? <strong>{d.chosen_provider_id}</strong> : <span className="badge b-warn">Deferred</span>}</td>
                    <td className="hide-sm small">{d.reason_text}</td>
                  </tr>
                  {open === d.id && cands.length > 0 && (
                    <tr>
                      <td colSpan={4} className="small muted">
                        {cands.map((c, i) => (
                          <div key={i}>
                            <code>{String(c.provider)}</code>
                            {c.estimateInr !== undefined && <> · ≈₹{Number(c.estimateInr).toFixed(2)}</>}
                            {c.quality !== undefined && <> · quality {String(c.quality)}</>}
                            {c.score !== undefined && <> · score {String(c.score)}</>}
                            {c.why ? <> · <em>{String(c.why)}</em></> : null}
                            {c.belowFloor !== undefined && <> · below floor {String(c.belowFloor)}</>}
                          </div>
                        ))}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
