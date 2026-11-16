import { useState } from 'react'
import { api, assetUrl, type ClipKind, type ClipVersion, type Production, type ShotRow } from '../api'
import { usePoll } from '../hooks'

export const STATE_LABEL: Record<string, string> = {
  queued: 'Waiting for a GPU', starting: 'Starting', rendering: 'Rendering', encoding: 'Saving',
  done: 'Done', failed: 'Failed', stopped: 'Stopped',
}

/**
 * M2b: moving video per shot. Drafts (fast, 480p) for every shot, finals (hybrid, 720p) for the drafts you keep
 * going. Shows live progress while a batch runs, with a Stop that cancels the remaining GPU work.
 */
export function ClipsCard({ id, p, onChange }: { id: string; p: Production; onChange: () => void }) {
  const { data: progress, refresh: refreshProgress } = usePoll(() => api.clipProgress(id), 3000, [id])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [takesOf, setTakesOf] = useState<string | null>(null)

  const drafts = p.shots.filter((s) => s.draft_clip_asset_id).length
  const finals = p.shots.filter((s) => s.final_clip_asset_id).length
  const running = (progress?.clips ?? []).filter((c) => !['done', 'failed', 'stopped'].includes(c.state))
  const rendering = p.shots.some((s) => s.clip_status === 'RENDERING')
  if (drafts === 0 && finals === 0 && running.length === 0 && !rendering) return null

  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    setBusy(true)
    try {
      await fn()
      onChange()
      await refreshProgress()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  const toggle = (shotId: string) => setSelected((cur) => {
    const next = new Set(cur)
    if (next.has(shotId)) next.delete(shotId)
    else next.add(shotId)
    return next
  })
  const redo = (kind: ClipKind) => act(async () => {
    const est = await api.clipEstimate(id, kind, [...selected])
    if (est.clips === 0) throw new Error('None of the selected shots can get a ' + kind + ' clip yet.')
    if (!window.confirm(`${est.summary}\n\nStart?`)) return
    await api.redoClips(id, [...selected], kind)
    setSelected(new Set())
  })

  return (
    <section className="card">
      <div className="card-head">
        <h2>Video clips</h2>
        <span className="pill">{drafts} draft{drafts === 1 ? '' : 's'} · {finals} final{finals === 1 ? '' : 's'}</span>
        <span className="grow" />
        {(running.length > 0 || rendering) && (
          <button className="danger" disabled={busy}
            onClick={() => { if (window.confirm('Stop making clips? Finished clips are kept; the rest are cancelled and no GPU keeps running.')) void act(() => api.stopClips(id)) }}>
            Stop
          </button>
        )}
      </div>
      <p className="muted small" style={{ marginTop: 0 }}>
        Drafts: Wan fast mode at 480p, to judge motion cheaply. Finals: Wan hybrid mode at 720p, for every draft not marked
        “keep draft”. 4K and smooth 24 fps come in finishing.
      </p>

      {running.length > 0 && (
        <div className="notice" style={{ display: 'grid', gap: 4 }}>
          <strong>Making {running.length} clip{running.length === 1 ? '' : 's'} on Modal</strong>
          {running.map((c) => (
            <div key={c.shotId + c.kind} className="row small">
              <span>Shot {c.shotNumber ?? '?'} ({c.kind})</span>
              <span className="muted">{STATE_LABEL[c.state] ?? c.state}</span>
              {c.steps ? <progress max={c.steps} value={c.step ?? 0} style={{ width: 120 }} /> : null}
              {c.etaSec != null && c.etaSec > 0 && <span className="muted">~{Math.ceil(c.etaSec)}s left</span>}
            </div>
          ))}
        </div>
      )}

      {selected.size > 0 && (
        <div className="notice row">
          <strong className="grow">{selected.size} shot{selected.size === 1 ? '' : 's'} selected</strong>
          <button disabled={busy} onClick={() => redo('draft')}>Redo draft</button>
          <button className="primary" disabled={busy} onClick={() => redo('final')}>Make final</button>
          <button onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}
      {error && <p className="error">{error}</p>}

      <div className="shot-grid">
        {p.shots.map((s) => (
          <ClipTile key={s.id} shot={s} selected={selected.has(s.id)} onToggle={() => toggle(s.id)} busy={busy}
            onKeepDraft={(keep) => act(() => api.setFinalWanted(s.id, !keep))}
            showTakes={takesOf === s.id} onTakes={() => setTakesOf(takesOf === s.id ? null : s.id)}
            onSelectTake={(assetId) => act(() => api.selectClip(s.id, assetId))} />
        ))}
      </div>
    </section>
  )
}

function ClipTile({ shot: s, selected, onToggle, busy, onKeepDraft, showTakes, onTakes, onSelectTake }: {
  shot: ShotRow; selected: boolean; onToggle: () => void; busy: boolean; onKeepDraft: (keep: boolean) => void
  showTakes: boolean; onTakes: () => void; onSelectTake: (assetId: string) => void
}) {
  const clip = s.footage_asset_id ?? s.final_clip_asset_id ?? s.draft_clip_asset_id
  return (
    <figure className={`shot ${s.priority === 'hero' ? 'hero' : ''} ${selected ? 'selected' : ''}`}>
      <div className="shot-media">
        {clip
          ? <video key={clip} src={assetUrl(clip)} muted loop playsInline controls preload="metadata"
            poster={s.keyframe_asset_id ? assetUrl(s.keyframe_asset_id) : undefined} />
          : <div className="shot-empty">No clip yet</div>}
        {s.clip_status === 'RENDERING' && <div className="shot-overlay">Rendering…</div>}
      </div>
      <figcaption>
        <div className="row small">
          <input type="checkbox" checked={selected} onChange={onToggle} aria-label={`Select shot ${s.ord + 1}`} />
          <strong>#{s.ord + 1}</strong>
          {s.footage_asset_id ? <span className="badge b-ok">NASA footage</span>
            : s.final_clip_asset_id ? <span className="badge b-ok">final</span> : s.draft_clip_asset_id ? <span className="badge b-info">draft</span> : null}
          {s.priority === 'hero' && <span className="badge b-warn">hero</span>}
          {(s.draft_clip_asset_id || s.final_clip_asset_id) && (
            <button className="small" style={{ padding: '1px 6px' }} onClick={onTakes}>{showTakes ? 'Hide takes' : s.priority === 'hero' ? 'Compare takes' : 'Takes'}</button>
          )}
        </div>
        {s.clip_status === 'FAILED' && <div className="small error">Clip failed: {s.clip_error}</div>}
        {s.clip_status === 'STOPPED' && <div className="small muted">Stopped before it finished.</div>}
        {s.draft_clip_asset_id && !s.final_clip_asset_id && (
          <label className="row small muted">
            <input type="checkbox" checked={!s.final_wanted} disabled={busy} onChange={(e) => onKeepDraft(e.target.checked)} />
            Keep the draft (no final render)
          </label>
        )}
        {showTakes && <Takes shotId={s.id} onSelect={onSelectTake} />}
      </figcaption>
    </figure>
  )
}

/**
 * Every take of a shot side by side, all playing together, so motion can be compared at a glance (hero shots get
 * two draft takes by default). Picking one is free: nothing is regenerated.
 */
export function Takes({ shotId, onSelect }: { shotId: string; onSelect: (assetId: string) => void }) {
  const { data } = usePoll<ClipVersion[]>(() => api.clipVersions(shotId), 10000, [shotId])
  if (!data) return <div className="muted small">Loading takes…</div>
  const playAll = () => document.querySelectorAll<HTMLVideoElement>(`[data-takes="${shotId}"] video`).forEach((v) => {
    v.currentTime = 0
    void v.play()
  })
  return (
    <div style={{ marginTop: 6 }}>
      {data.length > 1 && (
        <button className="small" style={{ padding: '1px 6px', marginBottom: 6 }} onClick={playAll}>Play all together</button>
      )}
      <div data-takes={shotId} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8 }}>
        {data.map((v, i) => (
          <div key={v.id} className="small" style={{ display: 'grid', gap: 4 }}>
            <video src={assetUrl(v.id)} muted loop playsInline preload="metadata" controls style={{ width: '100%' }} />
            <div className="row">
              <span>{v.kind} {data.length - i}</span>
              {v.active ? <span className="badge b-ok">in use</span>
                : <button className="small" style={{ padding: '1px 6px' }} onClick={() => onSelect(v.id)}>Use this</button>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
