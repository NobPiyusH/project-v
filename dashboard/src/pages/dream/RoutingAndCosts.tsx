import type { DreamDetail } from '../../dreamApi'

/** Every routing decision for this Short and every cost row it recorded. */
export function RoutingAndCosts({ d }: { d: DreamDetail }) {
  return (
    <section className="card">
      <div className="card-head"><h2>Why these providers, and what it cost</h2></div>
      {d.decisions.length === 0 && <p className="muted small">No routing decisions yet.</p>}
      {d.decisions.map((r, i) => <div key={i} className="small" style={{ marginBottom: 6 }}>
        <strong>{r.stage}</strong> → <code>{r.chosen_provider_id}</code> <span className="muted">{r.reason_text}</span></div>)}
      {d.costs.length > 0 && <table style={{ marginTop: 10 }}>
        <thead><tr><th>Provider</th><th>What</th><th>₹</th></tr></thead>
        <tbody>{d.costs.map((c, i) => <tr key={i}><td><code>{c.provider_id}</code></td><td className="small">{c.note}</td>
          <td>{Number(c.amount_inr).toFixed(2)}{c.estimated ? ' (est.)' : ''}</td></tr>)}</tbody>
      </table>}
    </section>
  )
}
