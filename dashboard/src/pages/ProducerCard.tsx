import { Fragment, useState } from 'react'
import { api, type ProducerDecision } from '../api'
import { usePoll } from '../hooks'

const VERDICT: Record<string, string> = { approve: 'Approve', redo: 'Redo', ask: 'Ask you' }

const TRUST: Record<string, string> = {
  auto: 'Auto: advises until it agrees with you often enough, then decides',
  advise: 'Advise only: it recommends, you decide everything',
  decide: 'Decide: it approves, redoes and spends within your limits, and asks you when unsure',
}

/** AI Producer report: what it recommended or did per stage, what you decided, agreement, and its trust switch. */
export function ProducerCard({ videoId }: { videoId: string }) {
  const { data } = usePoll(() => api.producer(videoId), 15000, [videoId])
  const { data: settings, refresh } = usePoll(api.producerSettings, 30000)
  const [open, setOpen] = useState<string | null>(null)
  const a = data?.agreement
  const s = data?.summary
  return (
    <section className="card">
      <div className="card-head">
        <h2>AI Producer</h2>
        <span className="pill" title="All videos: how often its recommendation matched your decision">
          {a && a.decided > 0 ? `Agrees with you ${a.rate}% (${a.agreed}/${a.decided})` : 'No decisions to compare yet'}
        </span>
      </div>
      {settings && (
        <div className="row small" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
          <span>Trust:</span>
          {(['auto', 'advise', 'decide'] as const).map((t) => (
            <button key={t} type="button" className={settings.trust === t ? 'on' : ''} title={TRUST[t]}
              onClick={() => void api.setProducerTrust(t).then(refresh)}>
              {t === 'auto' ? 'Auto' : t === 'advise' ? 'Advise only' : 'Let it decide'}
            </button>
          ))}
          <span className="muted">{settings.deciding ? 'It decides now (within your limits).' : 'It advises now; you decide.'}</span>
        </div>
      )}
      {s && (s.approved + s.redone + s.asked + s.spend_approved) > 0 && (
        <p className="small">
          This video: approved {s.approved} stage(s), redid {s.redone}, approved {s.spend_approved} spend step(s), asked you {s.asked} time(s).
          Spent ₹{Number(s.spentInr).toFixed(0)} of the ₹{Number(s.budgetInr).toFixed(0)} Producer budget (reviews ₹{Number(s.review_cost).toFixed(1)}).
        </p>
      )}
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
      <ProducerRules />
    </section>
  )
}

/** Rules it learned from your notes (and rules you add): you can edit, switch off or delete each. */
function ProducerRules() {
  const { data: rules, refresh } = usePoll(api.producerRules, 30000)
  const [text, setText] = useState('')
  const [stage, setStage] = useState('')
  return (
    <details style={{ marginTop: 12 }}>
      <summary className="small"><strong>Rules it follows</strong> ({rules?.length ?? 0}): learned from your notes, or added by you</summary>
      {!rules?.length && <p className="muted small">No rules yet. Reject a stage with notes (e.g. "no theatrical whispering") and it learns a rule.</p>}
      {rules?.map((r) => (
        <div key={r.id} className="row small" style={{ gap: 8, alignItems: 'center', margin: '4px 0' }}>
          <input type="checkbox" checked={r.enabled} title="Use this rule"
            onChange={(e) => void api.updateProducerRule(r.id, { enabled: e.target.checked }).then(refresh)} />
          <span className="muted">{r.stage ?? 'all'}</span>
          <input defaultValue={r.rule_text} style={{ flex: 1 }}
            onBlur={(e) => e.target.value !== r.rule_text && void api.updateProducerRule(r.id, { text: e.target.value }).then(refresh)} />
          <span className="muted">{r.source === 'owner' ? 'yours' : `learned (${r.evidence})`}</span>
          <button type="button" className="small" onClick={() => void api.deleteProducerRule(r.id).then(refresh)}>delete</button>
        </div>
      ))}
      <div className="row small" style={{ gap: 8, marginTop: 8 }}>
        <select value={stage} onChange={(e) => setStage(e.target.value)}>
          <option value="">all stages</option>
          {['SCRIPT', 'NARRATION', 'KEYFRAMES', 'DRAFT_GEN', 'FINAL_GEN', 'ASSEMBLY'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input placeholder="Add a rule, e.g. calm, warm narration; no whispering" value={text} style={{ flex: 1 }}
          onChange={(e) => setText(e.target.value)} />
        <button type="button" disabled={!text.trim()}
          onClick={() => void api.addProducerRule(stage || null, text).then(() => { setText(''); return refresh() })}>Add</button>
      </div>
    </details>
  )
}
