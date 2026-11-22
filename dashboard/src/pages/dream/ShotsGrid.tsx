import { assetUrl } from '../../api'
import type { DreamDetail } from '../../dreamApi'
import { ShotReview } from './ShotReview'

/** The Short's shots (or SVI beats) with their clip or picture; ticks pick which ones a redo touches. */
export function ShotsGrid({ d, selectable, picked, onPick }: { d: DreamDetail; selectable: boolean; picked: string[]; onPick: (ids: string[]) => void }) {
  const progress = new Map((d.clipProgress ?? []).map((p) => [p.shotId, p.state]))
  return (
    <section className="card">
      <div className="card-head"><h2>{d.svi_scenes ? `SVI scenes (${d.svi_scenes} × ${d.svi_scene_seconds} s)` : 'Shots'}</h2>{selectable && <span className="muted small">
        {d.svi_scenes ? 'Tick a beat to redo it: that scene is made again from that beat on, earlier beats are kept' : 'Tick shots to redo only those'}</span>}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 12 }}>
        {d.shots.map((s) => (
          <div key={s.id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
            <div className="row small" style={{ justifyContent: 'space-between' }}>
              <strong>{s.scene_no ? `Scene ${s.scene_no} · beat ${s.beat_no}` : `#${s.ord + 1}`}</strong>
              {selectable && <input type="checkbox" checked={picked.includes(s.id)} aria-label={`Pick shot ${s.ord + 1}`}
                onChange={(e) => onPick(e.target.checked ? [...picked, s.id] : picked.filter((x) => x !== s.id))} />}
            </div>
            {s.clip_asset_id
              ? <video src={assetUrl(s.clip_asset_id)} poster={s.picture_asset_id ? assetUrl(s.picture_asset_id) : undefined}
                  muted loop playsInline controls preload="metadata" style={{ width: '100%', borderRadius: 6 }} />
              : s.picture_asset_id
                ? <img src={assetUrl(s.picture_asset_id)} alt={s.scene} style={{ width: '100%', borderRadius: 6 }} />
                : <div className="muted small" style={{ aspectRatio: '9/16', display: 'grid', placeItems: 'center', background: 'var(--surface-2)', borderRadius: 6 }}>No picture yet</div>}
            {progress.get(s.id) && progress.get(s.id) !== 'done' && <span className="badge b-info">{progress.get(s.id)}</span>}
            <p className="small" style={{ margin: '6px 0 0' }}>{s.scene}</p>
            {s.error && <p className="error small">{s.error}</p>}
            {s.clip_asset_id && <ShotReview dreamId={d.id} shotId={s.id} />}
          </div>))}
      </div>
    </section>
  )
}
