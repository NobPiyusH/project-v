import { useState } from 'react'
import { studio } from './api'
import { usePoll } from './hooks'

/** Backend power: shows whether the control plane and media worker are up, with a Start or Restart button. */
export function BackendPower() {
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
