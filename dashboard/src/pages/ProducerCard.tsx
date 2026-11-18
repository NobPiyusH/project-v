import { Fragment, useState } from 'react'
import { api, type ProducerDecision } from '../api'
import { usePoll } from '../hooks'

const VERDICT: Record<string, string> = { approve: 'Approve', redo: 'Redo', ask: 'Ask you' }

/** AI Producer report (P1): what it recommended per stage, what you decided, and how often you agree. */
export function ProducerCard({ videoId }: { videoId: string }) {
  const { data } = usePoll(() => api.producer(videoId), 15000, [videoId])
  const [open, setOpen] = useState<string | null>(null)
  const a = data?.agreement
  return (
    <section className="card">
      <div className="card-head">
        <h2>AI Producer</h2>
        <span className="pill" title="All videos: how often its recommendation matched your decision">
          {a && a.decided > 0 ? `Agrees with you ${a.rate}% (${a.agreed}/${a.decided})` : 'No decisions to compare yet'}
        </span>
      </div>
      {!data?.decisions.length ? (
        <p className="muted small">No reviews yet. Set a stage to <strong>AI Producer</strong> mode: it reviews the script,
          listens to the voice, looks at keyframes and watches the clips, then recommends; you decide.</p>
      ) : (
        <table>
          <thead><tr><th>Stage</th><th>Recommends</th><th>You</th><th className="hide-sm">Why</th></tr></thead>
          <tbody>
            {data.decisions.map((d: ProducerDecision) => {
              const items = JSON.parse(d.items || '[]') as { id: string; score: number; ok: boolean; problem: string; fix: string }[]
              return (
                <Fragment key={d.id}>
                  <tr onClick={() => setOpen(open === d.id ? null : d.id)} style={{ cursor: items.length ? 'pointer' : undefined }}>
                    <td className="small">{d.stage}<div className="muted">try {d.attempt}</div></td>
                    <td><span className={`badge ${d.verdict === 'approve' ? 'b-ok' : 'b-warn'}`}>{VERDICT[d.verdict] ?? d.verdict}</span>
                      {d.confidence != null && <div className="muted small">{Math.round(Number(d.confidence) * 100)}% sure</div>}</td>
                    <td className="small">{d.owner_decision ?? '—'}
                      {d.agreed != null && <div className="muted">{d.agreed ? 'agreed' : 'disagreed'}</div>}</td>
                    <td className="hide-sm small">{d.summary}{d.redo_notes ? <div className="muted">Redo: {d.redo_notes}</div> : null}</td>
                  </tr>
                  {open === d.id && items.length > 0 && (
                    <tr>
                      <td colSpan={4} className="small muted">
                        {items.map((i) => (
                          <div key={i.id}><code>{i.id}</code> · {i.score}/10 {i.ok ? '✓' : '✗'}
                            {i.problem ? <> · {i.problem}</> : null}{i.fix ? <> · <em>fix: {i.fix}</em></> : null}</div>
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
