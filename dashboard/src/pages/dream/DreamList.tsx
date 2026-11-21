import { assetUrl } from '../../api'
import { dreamApi, type DreamStep, type DreamSummary } from '../../dreamApi'
import { DreamStatusBadge } from './DreamStatusBadge'
import { STEP_LABEL } from './shared'

/** The owner's Dream Shorts, newest first; a Short waiting on its clip cost can be approved from the row. */
export function DreamList({ shorts, error, refresh }: { shorts: DreamSummary[] | null; error: string | null; refresh: () => void }) {
  return (
    <section className="card">
      <div className="card-head"><h2>Your Dream Shorts</h2></div>
      {error && <p className="error">{error}</p>}
      {shorts && shorts.length === 0 && <p className="muted">Start your first Dream Short above.</p>}
      {shorts && shorts.length > 0 && (
        <table>
          <thead><tr><th></th><th>Short</th><th>Status</th><th className="hide-sm">Step</th><th>Spent</th></tr></thead>
          <tbody>{shorts.map((d: DreamSummary) => (
            <tr key={d.id} style={{ cursor: 'pointer' }} onClick={() => { window.location.hash = `#/dream/${d.id}` }}>
              <td style={{ width: 40 }}>{d.thumb_asset_id && <img src={assetUrl(d.thumb_asset_id)} alt="" style={{ width: 32, borderRadius: 4 }} />}</td>
              <td><strong>{d.title ?? d.idea}</strong>{d.episode > 1 && <span className="pill" style={{ marginLeft: 6 }}>Ep {d.episode}</span>}{d.title && <div className="muted small">{d.idea}</div>}</td>
              <td><DreamStatusBadge status={d.status} />
                {d.status === 'WAITING_COST' && (
                  <div><button className="primary small" style={{ marginTop: 6 }}
                    onClick={(e) => { e.stopPropagation(); void dreamApi.approve(d.id, 'CLIPS').then(refresh) }}>
                    Approve cost</button></div>)}</td>
              <td className="hide-sm">{d.step ? STEP_LABEL[d.step as DreamStep] ?? d.step : ''}</td>
              <td>₹{Number(d.spent_inr).toFixed(0)}</td>
            </tr>))}
          </tbody>
        </table>
      )}
    </section>
  )
}
