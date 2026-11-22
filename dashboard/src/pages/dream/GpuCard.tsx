import { useState } from 'react'
import { dreamApi } from '../../dreamApi'
import { usePoll } from '../../hooks'
import { errorText } from './shared'

/** Auto (the studio picks the cheapest, learning from your picks) or Manual (you pick from the live, price-sorted list). */
export function GpuCard({ id }: { id: string }) {
  const { data: on } = usePoll(() => dreamApi.gpuEnabled(), 15000)
  const { data: pick, refresh } = usePoll(() => dreamApi.gpuPick(id), 5000, [id])
  const { data: opts, refresh: refreshOpts } = usePoll(() => dreamApi.gpuOptions(), 60000)
  const [err, setErr] = useState<string | null>(null)
  if (!on?.enabled || !pick) return null
  const run = async (change: () => Promise<unknown>) => {
    setErr(null)
    try { await change() } catch (e) { setErr(errorText(e)) }
    void refresh()
  }
  const set = (mode: 'auto' | 'manual', choice?: string) => run(() => dreamApi.setGpuPick(id, mode, choice))
  const power = (hold: 'auto' | 'manual', idle?: number) => run(() => dreamApi.setGpuHold(id, hold, idle))
  const turnOff = async () => {
    if (!window.confirm('Turn the shared GPU off now? It is one pod for all Shorts: any step using it, in any Short, will stop and wait for Retry.')) return
    await run(() => dreamApi.gpuOff(id))
  }
  return (
    <section className="card">
      <div className="card-head"><h2>GPU</h2>
        <span className="muted small">{pick.rented ? 'A GPU is rented for this Short; a change applies to the next rental' : 'Chosen when the Short needs a GPU'}</span></div>
      {on.today && <p className="small" style={{ margin: '0 0 8px' }}
        title={`Booked $${on.today.bookedUsd.toFixed(2)} + the pod that is on now $${on.today.runningUsd.toFixed(2)}. Past the cap nothing new is rented; the Short waits for you.`}>
        <strong>Today (UTC):</strong> ${on.today.spentUsd.toFixed(2)} of ${on.today.capUsd.toFixed(2)} GPU cap
        {on.today.capUsd > 0 && on.today.spentUsd >= on.today.capUsd && <span className="error"> · cap reached, no new GPU today</span>}
        {pick.estimate && <span className="muted"> · this Short needs about {Math.round(pick.estimate.hours * 60)} min ≈ ${pick.estimate.usd.toFixed(2)} at ${pick.estimate.hourlyUsd.toFixed(2)}/h</span>}</p>}
      <div className="row" style={{ marginBottom: 8 }}>
        <strong className="small">GPU power</strong>
        <label><input type="radio" checked={pick.hold === 'auto'} onChange={() => void power('auto')} /> Auto (the studio turns it off when done)</label>
        <label><input type="radio" checked={pick.hold === 'manual'} onChange={() => void power('manual')} /> Manual (stays on until I turn it off)</label>
        {pick.hold === 'manual' && <label className="small">idle auto-off <select value={pick.idleMinutes}
          onChange={(e) => void power('manual', Number(e.target.value))}>{[5, 10, 15, 30].map((n) => <option key={n} value={n}>{n} min</option>)}</select></label>}
        {pick.rented && <button className="danger" onClick={() => void turnOff()}>Turn off GPU now</button>}
        {pick.rented && <span className="muted small">one shared pod · {pick.usersOnPod} Short{pick.usersOnPod === 1 ? '' : 's'} using it</span>}
        {pick.rented && pick.hold === 'manual' && pick.offInSeconds != null && <span className="muted small">turns off by itself in {Math.floor(pick.offInSeconds / 60)}:{String(pick.offInSeconds % 60).padStart(2, '0')} if unused</span>}
        {!pick.rented && <span className="muted small">GPU is off</span>}
      </div>
      <div className="row">
        <label><input type="radio" checked={pick.mode === 'auto'} onChange={() => void set('auto')} /> Auto (cheapest; learns from your picks)</label>
        <label><input type="radio" checked={pick.mode === 'manual'} onChange={() => void set('manual', pick.choice || opts?.find((o) => o.available)?.key || undefined)} /> Manual (I pick)</label>
        <button onClick={() => void refreshOpts()}>Refresh prices</button>
      </div>
      {pick.mode === 'manual' && (
        <table className="small" style={{ width: '100%' }}><tbody>
          {(opts ?? []).map((o, i) => (
            <tr key={`${o.provider}-${o.key ?? i}`} style={o.available ? undefined : { opacity: 0.55 }}>
              <td><input type="radio" disabled={!o.available} checked={!!o.key && pick.choice === o.key} aria-label={`Pick ${o.provider} ${o.gpu ?? ''}`}
                onChange={() => o.key && void set('manual', o.key)} /></td>
              <td>{o.provider}</td><td>{o.gpu ? o.gpu.replace('|', ' ') : '–'}</td>
              <td>{o.available ? `$${o.hourlyUsd.toFixed(2)}/h` : '–'}</td><td>{o.available ? o.stock : (o.why ?? 'unavailable')}</td>
            </tr>))}
        </tbody></table>)}
      {err && <p className="error small">{err}</p>}
    </section>
  )
}
