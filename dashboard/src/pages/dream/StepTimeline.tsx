import { STEP_LABEL, type DreamDetail } from '../../dreamApi'

const IST: Intl.DateTimeFormatOptions = { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit' }
const ist = (t: string | null) => (t ? new Date(t).toLocaleTimeString('en-IN', IST) : '–')
function dur(sec: number | null) {
  if (sec == null) return '–'
  const m = Math.floor(sec / 60), s = Math.round(sec % 60)
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : m ? `${m} min ${s} s` : `${s} s`
}

/** Per step: when it started and finished (IST), how long it worked, what it cost; the running step against its usual time. */
export function StepTimeline({ d }: { d: DreamDetail }) {
  if (!d.timeline) return null
  return (
    <section className="card">
      <div className="card-head"><h2>Steps</h2><span className="muted small">Times in IST · work time excludes waiting for you</span></div>
      <table>
        <thead><tr><th>Step</th><th>Status</th><th>Started</th><th>Finished</th><th>Took</th><th>₹</th></tr></thead>
        <tbody>{d.timeline.map((t) => (
          <tr key={t.step}>
            <td>{STEP_LABEL[t.step]}</td>
            <td className="small">{t.status === 'PENDING' ? <span className="muted">not started</span> : t.status.toLowerCase().replace('_', ' ')}</td>
            <td className="small">{ist(t.startedAt)}</td>
            <td className="small">{ist(t.finishedAt)}</td>
            <td className="small">{t.startedAt ? dur(t.workSeconds) : '–'}
              {t.startedAt && !t.finishedAt && <span className="muted">{' '}/ {t.typicalSeconds != null ? `usually ${dur(t.typicalSeconds)}` : 'no estimate yet'}</span>}</td>
            <td className="small">{t.costInr ? t.costInr.toFixed(2) : '–'}</td>
          </tr>))}</tbody>
      </table>
      <h3 style={{ margin: '14px 0 6px' }}>Log</h3>
      {d.log.length === 0 && <p className="muted small">Nothing yet.</p>}
      <div className="small" style={{ maxHeight: 280, overflowY: 'auto' }}>
        {d.log.map((l, i) => <div key={i} style={{ marginBottom: 3 }}><span className="muted">{ist(l.at)}</span> {l.text}</div>)}
      </div>
    </section>
  )
}
