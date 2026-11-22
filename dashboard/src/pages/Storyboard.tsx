import { useEffect, useState } from 'react'
import { api, assetUrl, type ClipKind, type ImagePolicy, type Production, type ShotRow } from '../api'
import { usePoll } from '../hooks'
import { STATE_LABEL, Takes } from './ClipsCard'
import { PolicySelect, ShotEditor } from './ProductionCards'

type View = 'image' | 'draft' | 'final' | 'footage'

const VIEW_LABEL: Record<View, string> = { image: 'Image', draft: 'Draft', final: 'Final', footage: 'NASA' }

function best(s: ShotRow): { view: View; id: string } | null {
  if (s.footage_asset_id) return { view: 'footage', id: s.footage_asset_id }
  if (s.final_clip_asset_id) return { view: 'final', id: s.final_clip_asset_id }
  if (s.draft_clip_asset_id) return { view: 'draft', id: s.draft_clip_asset_id }
  if (s.keyframe_asset_id) return { view: 'image', id: s.keyframe_asset_id }
  return null
}

function views(s: ShotRow): { view: View; id: string }[] {
  const out: { view: View; id: string }[] = []
  if (s.keyframe_asset_id) out.push({ view: 'image', id: s.keyframe_asset_id })
  if (s.draft_clip_asset_id) out.push({ view: 'draft', id: s.draft_clip_asset_id })
  if (s.final_clip_asset_id) out.push({ view: 'final', id: s.final_clip_asset_id })
  if (s.footage_asset_id) out.push({ view: 'footage', id: s.footage_asset_id })
  return out
}

/**
 * The storyboard: every shot as a tile showing its best media (real footage, final, draft, else the image), playing on
 * hover. Click a tile for the shot panel; tick several for batch redos. Live clip progress and Stop on top.
 */
export function Storyboard({ id, p, vertical, onChange }: { id: string; p: Production; vertical: boolean; onChange: () => void }) {
  const { data: progress, refresh: refreshProgress } = usePoll(() => api.clipProgress(id), 3000, [id])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [active, setActive] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [policy, setPolicy] = useState<ImagePolicy>('AUTO')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const shots = p.shots
  const idx = shots.findIndex((s) => s.id === active)
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest('input, textarea, select')) return
      if (e.key === 'Escape') setActive(null)
      if (e.key === 'ArrowRight' && idx < shots.length - 1) setActive(shots[idx + 1].id)
      if (e.key === 'ArrowLeft' && idx > 0) setActive(shots[idx - 1].id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, idx, shots])

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
  const clips = (kind: ClipKind, ids: string[]) => act(async () => {
    const est = await api.clipEstimate(id, kind, ids)
    if (est.clips === 0) throw new Error(`None of these shots can get a ${kind} clip yet.`)
    if (!window.confirm(`${est.summary}\n\nStart?`)) return
    await api.redoClips(id, ids, kind)
    setSelected(new Set())
  })
  const toggle = (shotId: string) => setSelected((cur) => {
    const next = new Set(cur)
    if (next.has(shotId)) next.delete(shotId)
    else next.add(shotId)
    return next
  })

  const running = (progress?.clips ?? []).filter((c) => !['done', 'failed', 'stopped'].includes(c.state))
  const rendering = shots.some((s) => s.clip_status === 'RENDERING')
  const count = (f: (s: ShotRow) => unknown) => shots.filter(f).length
  const activeShot = idx >= 0 ? shots[idx] : null

  return (
    <div className={activeShot ? 'split' : undefined}>
      <section className="card">
        <div className="card-head">
          <h2>Storyboard</h2>
          <div className="row small muted">
            <span>{shots.length} shots</span>
            <span>· {count((s) => s.priority === 'hero')} hero</span>
            <span>· {count((s) => s.draft_clip_asset_id)} drafts</span>
            <span>· {count((s) => s.final_clip_asset_id)} finals</span>
            {count((s) => s.footage_asset_id) > 0 && <span>· {count((s) => s.footage_asset_id)} NASA</span>}
          </div>
          <span className="grow" />
          {(running.length > 0 || rendering) && (
            <button className="danger" disabled={busy}
              onClick={() => { if (window.confirm('Stop making clips? Finished clips are kept; the rest are cancelled and no GPU keeps running.')) void act(() => api.stopClips(id)) }}>
              Stop clips
            </button>
          )}
        </div>

        {running.length > 0 && (
          <div className="notice" style={{ display: 'grid', gap: 4 }}>
            <strong>Making {running.length} clip{running.length === 1 ? '' : 's'} on the GPU</strong>
            {running.slice(0, 8).map((c, i) => (
              <div key={c.shotId + c.kind + i} className="row small">
                <span>Shot {c.shotNumber ?? '?'} ({c.kind})</span>
                <span className="muted">{STATE_LABEL[c.state] ?? c.state}</span>
                {c.steps ? <progress max={c.steps} value={c.step ?? 0} style={{ width: 120 }} /> : null}
                {c.etaSec != null && c.etaSec > 0 && <span className="muted">~{Math.ceil(c.etaSec)}s left</span>}
              </div>
            ))}
            {running.length > 8 && <span className="muted small">and {running.length - 8} more in the queue</span>}
          </div>
        )}

        {selected.size > 0 && (
          <div className="notice" style={{ display: 'grid', gap: 8 }}>
            <div className="row">
              <strong className="grow">{selected.size} shot{selected.size === 1 ? '' : 's'} selected</strong>
              <button onClick={() => setSelected(new Set())}>Clear</button>
            </div>
            <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
              placeholder="Notes for an image redo, e.g. 'more photoreal, darker, no lens flares'" />
            <div className="row">
              <PolicySelect value={policy} onChange={setPolicy} />
              <button disabled={busy} onClick={() => act(async () => {
                await api.redoShots(id, [...selected], notes, policy)
                setSelected(new Set())
                setNotes('')
              })}>Redo images</button>
              <button disabled={busy} onClick={() => clips('draft', [...selected])}>Redo drafts</button>
              <button className="primary" disabled={busy} onClick={() => clips('final', [...selected])}>Make finals</button>
            </div>
          </div>
        )}
        {error && <p className="error">{error}</p>}

        <div className={`storyboard ${vertical ? 'vertical' : ''}`}>
          {shots.map((s) => {
            const b = best(s)
            const isClip = b && b.view !== 'image'
            return (
              <div key={s.id} role="button" tabIndex={0}
                className={`tile ${vertical ? 'vertical' : ''} ${s.priority === 'hero' ? 'hero' : ''} ${active === s.id ? 'active' : ''}`}
                onClick={() => setActive(active === s.id ? null : s.id)}
                onKeyDown={(e) => { if (e.key === 'Enter') setActive(s.id) }}>
                <div className="media">
                  {b && (isClip
                    ? <video src={assetUrl(b.id)} muted loop playsInline preload="metadata"
                      poster={s.keyframe_asset_id ? assetUrl(s.keyframe_asset_id) : undefined}
                      onMouseEnter={(e) => void e.currentTarget.play()} onMouseLeave={(e) => e.currentTarget.pause()} />
                    : <img src={assetUrl(b.id)} alt={s.description ?? `Shot ${s.ord + 1}`} loading="lazy" />)}
                  <span className="n">{s.ord + 1}</span>
                  <input className="sel" type="checkbox" checked={selected.has(s.id)} aria-label={`Select shot ${s.ord + 1}`}
                    onClick={(e) => e.stopPropagation()} onChange={() => toggle(s.id)} />
                  <div className="chips">
                    {s.priority === 'hero' && <span className="badge b-warn">hero</span>}
                    {b && <span className={`badge ${b.view === 'image' ? 'b-idle' : 'b-ok'}`}>{VIEW_LABEL[b.view]}</span>}
                    {(s.status === 'FAILED' || s.clip_status === 'FAILED') && <span className="badge b-bad">failed</span>}
                  </div>
                  {(s.status === 'REDOING' || s.clip_status === 'RENDERING') && (
                    <div className="busy">{s.clip_status === 'RENDERING' ? 'Rendering…' : 'New image…'}</div>
                  )}
                </div>
                <div className="cap clamp2">{Number(s.duration_target_sec).toFixed(1)}s · {s.description}</div>
              </div>
            )
          })}
        </div>
      </section>

      {activeShot && (
        <ShotPanel key={activeShot.id} videoId={id} p={p} shot={activeShot} index={idx} total={shots.length} busy={busy}
          onPrev={() => idx > 0 && setActive(shots[idx - 1].id)}
          onNext={() => idx < shots.length - 1 && setActive(shots[idx + 1].id)}
          onClose={() => setActive(null)} onChange={onChange}
          onClips={(kind) => clips(kind, [activeShot.id])}
          onKeepDraft={(keep) => act(() => api.setFinalWanted(activeShot.id, !keep))}
          onSelectTake={(assetId) => act(() => api.selectClip(activeShot.id, assetId))} />
      )}
    </div>
  )
}

function ShotPanel(props: {
  videoId: string; p: Production; shot: ShotRow; index: number; total: number; busy: boolean
  onPrev: () => void; onNext: () => void; onClose: () => void; onChange: () => void
  onClips: (kind: ClipKind) => void; onKeepDraft: (keep: boolean) => void; onSelectTake: (assetId: string) => void
}) {
  const { shot: s, p } = props
  const all = views(s)
  const [view, setView] = useState<View>(best(s)?.view ?? 'image')
  const shown = all.find((v) => v.view === view) ?? all[0]
  const line = s.prompt_json.lineIndex != null ? p.script?.lines.find((l) => l.index === s.prompt_json.lineIndex) : undefined
  const [showTakes, setShowTakes] = useState(s.priority === 'hero')

  return (
    <aside className="drawer" aria-label={`Shot ${s.ord + 1}`}>
      <div className="row">
        <strong>Shot {s.ord + 1}</strong>
        <span className="muted small">of {props.total}</span>
        {s.priority === 'hero' && <span className="badge b-warn">hero</span>}
        <span className="grow" />
        <button className="icon-btn" onClick={props.onPrev} disabled={props.index === 0} title="Previous shot (←)">←</button>
        <button className="icon-btn" onClick={props.onNext} disabled={props.index === props.total - 1} title="Next shot (→)">→</button>
        <button className="icon-btn close" onClick={props.onClose}>Close</button>
      </div>

      {all.length > 1 && (
        <div className="media-tabs">
          {all.map((v) => (
            <button key={v.view} className={v.view === shown?.view ? 'on' : ''} onClick={() => setView(v.view)}>{VIEW_LABEL[v.view]}</button>
          ))}
        </div>
      )}
      <div className="viewer">
        {shown ? (shown.view === 'image'
          ? <img src={assetUrl(shown.id)} alt={s.description ?? ''} />
          : <video key={shown.id} src={assetUrl(shown.id)} controls autoPlay muted loop playsInline />)
          : <div className="muted" style={{ padding: 40 }}>No image yet</div>}
      </div>

      <div className="small">
        <div className="muted">{Number(s.duration_target_sec).toFixed(1)}s · {s.shot_type} · {s.camera_move}</div>
        <div>{s.description}</div>
        {line && <div style={{ marginTop: 4 }}><span className="muted">Heard:</span> “{line.text}”</div>}
        {s.overlay_text && <div className="muted">On screen: “{s.overlay_text}”</div>}
        {s.clip_status === 'FAILED' && <div className="error" style={{ marginTop: 6 }}>Clip failed: {s.clip_error}</div>}
      </div>

      {!s.footage_asset_id && s.keyframe_asset_id && (
        <div style={{ display: 'grid', gap: 6 }}>
          <strong className="small">Moving video</strong>
          <div className="row">
            <button disabled={props.busy} onClick={() => props.onClips('draft')}>{s.draft_clip_asset_id ? 'Redo draft' : 'Make draft'}</button>
            {s.draft_clip_asset_id && (
              <button className="primary" disabled={props.busy} onClick={() => props.onClips('final')}>
                {s.final_clip_asset_id ? 'Redo final' : 'Make final'}
              </button>
            )}
            {(s.draft_clip_asset_id || s.final_clip_asset_id) && (
              <button onClick={() => setShowTakes((x) => !x)}>{showTakes ? 'Hide takes' : 'Compare takes'}</button>
            )}
          </div>
          {s.draft_clip_asset_id && !s.final_clip_asset_id && (
            <label className="row small muted">
              <input type="checkbox" checked={!s.final_wanted} disabled={props.busy} onChange={(e) => props.onKeepDraft(e.target.checked)} />
              Keep the draft (no final render)
            </label>
          )}
          {showTakes && (s.draft_clip_asset_id || s.final_clip_asset_id) && <Takes shotId={s.id} onSelect={props.onSelectTake} />}
        </div>
      )}

      <ShotEditor videoId={props.videoId} shot={s} onChange={props.onChange} />
      <div className="muted small"><span className="kbd">←</span> <span className="kbd">→</span> move between shots · <span className="kbd">Esc</span> closes</div>
    </aside>
  )
}
