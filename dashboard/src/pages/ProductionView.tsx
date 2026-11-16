import { useState, type ReactNode } from 'react'
import { api, assetUrl, type Fact, type ImagePolicy, type Production, type ShotRow, type ShotVersion } from '../api'
import { usePoll } from '../hooks'
import { ScriptEditor } from './ScriptEditor'
import { ClipsCard } from './ClipsCard'

/** What the stages have actually produced so far: research, concept, script + voice, music, shots, animatic. */
export function ProductionView({ id }: { id: string }) {
  const { data: p, refresh } = usePoll(() => api.production(id), 3000, [id])
  if (!p) return null
  const empty = !p.research && !p.concept && !p.script && !p.music && p.shots.length === 0
  if (empty) return null

  return (
    <>
      {p.animaticAssetId && <AnimaticCard id={id} p={p} onChange={refresh} />}
      {p.shots.length > 0 && <ClipsCard id={id} p={p} onChange={refresh} />}
      {p.shots.length > 0 && <ShotGrid id={id} p={p} onChange={refresh} />}
      {(p.concept || p.script || p.music) && <StoryCard id={id} p={p} onChange={refresh} />}
      {p.research && <ResearchCard facts={JSON.parse(p.research.facts) as Fact[]} queries={p.research.queries} />}
    </>
  )
}

/** Runs an API action with busy/error state, then refreshes. */
function useAction(onChange: () => void) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<unknown>) => {
    setError(null)
    setBusy(true)
    try {
      await fn()
      onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return { error, busy, run }
}

function AnimaticCard({ id, p, onChange }: { id: string; p: Production; onChange: () => void }) {
  const { error, busy, run } = useAction(onChange)
  return (
    <section className="card">
      <div className="card-head">
        <h2>Animatic</h2>
        <span className="pill">Draft: keyframes + voice + music, to check story and pacing before video generation</span>
      </div>
      {p.animaticOutdated && (
        <div className="notice row">
          <span className="grow">You changed shots or voice lines since this animatic was made.</span>
          <button className="primary" disabled={busy || p.redoingShots + p.redoingLines > 0}
            title={p.redoingShots + p.redoingLines > 0 ? 'Wait until redos finish' : 'Free: runs on your laptop'}
            onClick={() => run(() => api.rerun(id, 'ANIMATIC'))}>
            {busy ? 'Starting…' : 'Rebuild animatic (free)'}
          </button>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <video key={p.animaticAssetId} className="player" src={assetUrl(p.animaticAssetId!)} controls preload="metadata" />
    </section>
  )
}

const POLICY_LABEL: Record<ImagePolicy, string> = {
  AUTO: 'Auto (hero: Nano Banana, rest: Z-Image)',
  PREMIUM: 'Premium: Nano Banana (~₹3 each)',
  FREE: 'Free: Z-Image on Modal',
}

function PolicySelect({ value, onChange }: { value: ImagePolicy; onChange: (p: ImagePolicy) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as ImagePolicy)} aria-label="Image model">
      {(Object.keys(POLICY_LABEL) as ImagePolicy[]).map((k) => <option key={k} value={k}>{POLICY_LABEL[k]}</option>)}
    </select>
  )
}

function ShotGrid({ id, p, onChange }: { id: string; p: Production; onChange: () => void }) {
  const [open, setOpen] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [notes, setNotes] = useState('')
  const [policy, setPolicy] = useState<ImagePolicy>('AUTO')
  const { error, busy, run } = useAction(onChange)
  const heroes = p.shots.filter((s) => s.priority === 'hero').length
  const toggle = (shotId: string) => setSelected((cur) => {
    const next = new Set(cur)
    if (next.has(shotId)) next.delete(shotId)
    else next.add(shotId)
    return next
  })
  const redoSelected = () => run(async () => {
    await api.redoShots(id, [...selected], notes, policy)
    setSelected(new Set())
    setNotes('')
  })

  return (
    <section className="card">
      <div className="card-head">
        <h2>Shots</h2>
        <span className="pill">{p.shots.length} shots · {heroes} hero{p.redoingShots > 0 ? ` · ${p.redoingShots} redoing` : ''}</span>
      </div>
      {selected.size > 0 && (
        <div className="notice" style={{ display: 'grid', gap: 8 }}>
          <strong>Redo {selected.size} selected shot{selected.size > 1 ? 's' : ''}</strong>
          <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder="What should change? e.g. 'more photoreal, darker, no lens flares'" />
          <div className="row">
            <PolicySelect value={policy} onChange={setPolicy} />
            <button className="primary" disabled={busy} onClick={redoSelected}>{busy ? 'Starting…' : 'Redo selected'}</button>
            <button onClick={() => setSelected(new Set())}>Clear selection</button>
          </div>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <p className="muted small" style={{ marginTop: 0 }}>
        Tick shots to redo several at once, or click an image to edit its prompt, redo it, or switch versions.
      </p>
      <div className="shot-grid">
        {p.shots.map((s) => (
          <figure key={s.id} className={`shot ${s.priority === 'hero' ? 'hero' : ''} ${selected.has(s.id) ? 'selected' : ''}`}>
            <div className="shot-media" onClick={() => setOpen(open === s.id ? null : s.id)}>
              {s.keyframe_asset_id
                ? <img key={s.keyframe_asset_id} src={assetUrl(s.keyframe_asset_id)} alt={s.description ?? `Shot ${s.ord + 1}`} loading="lazy" />
                : <div className="shot-empty">No keyframe yet</div>}
              {s.status === 'REDOING' && <div className="shot-overlay">Redoing…</div>}
            </div>
            <figcaption>
              <div className="row small">
                <input type="checkbox" checked={selected.has(s.id)} onChange={() => toggle(s.id)} aria-label={`Select shot ${s.ord + 1}`} />
                <strong>#{s.ord + 1}</strong>
                <span className="muted">{Number(s.duration_target_sec).toFixed(1)}s · {s.camera_move ?? ''}</span>
                {s.priority === 'hero' && <span className="badge b-warn">hero</span>}
                {s.keyframe_provider && <span className="muted small">{s.keyframe_provider === 'gemini_api' ? 'Nano Banana' : 'Z-Image'}</span>}
              </div>
              {s.status === 'FAILED' && <div className="small error">Redo failed: {s.error}</div>}
              <div className="small">{s.description}</div>
              {s.overlay_text && <div className="small muted">On screen: “{s.overlay_text}”</div>}
              {open === s.id && <ShotEditor videoId={id} shot={s} onChange={onChange} />}
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  )
}

function ShotEditor({ videoId, shot, onChange }: { videoId: string; shot: ShotRow; onChange: () => void }) {
  const [prompt, setPrompt] = useState(shot.prompt_json.keyframePrompt ?? '')
  const [notes, setNotes] = useState('')
  const [policy, setPolicy] = useState<ImagePolicy>('AUTO')
  const { data: versions, refresh } = usePoll<ShotVersion[]>(() => api.shotVersions(shot.id), 5000, [shot.id, shot.keyframe_asset_id])
  const { error, busy, run } = useAction(() => { onChange(); void refresh() })
  const changed = prompt.trim() !== (shot.prompt_json.keyframePrompt ?? '').trim()
  const redoing = shot.status === 'REDOING'

  return (
    <div className="shot-editor">
      <label className="small" htmlFor={`prompt-${shot.id}`}>
        <strong>Image prompt</strong> {shot.prompt_json.editedByOwner ? <span className="muted">(edited by you)</span> : null}
      </label>
      <textarea id={`prompt-${shot.id}`} rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      <div className="small muted"><strong>Motion (used for real video in M2b):</strong> {shot.prompt_json.motionPrompt}</div>
      <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes for this redo (optional)" />
      <div className="row">
        <PolicySelect value={policy} onChange={setPolicy} />
        <button className="primary" disabled={busy || redoing} onClick={() => run(async () => {
          if (changed) await api.editShotPrompt(shot.id, prompt)
          await api.redoShots(videoId, [shot.id], notes, policy)
          setNotes('')
        })}>{redoing ? 'Redoing…' : changed ? 'Save prompt & redo' : 'Redo this shot'}</button>
        {changed && <button disabled={busy} onClick={() => run(() => api.editShotPrompt(shot.id, prompt))}>Save prompt only</button>}
      </div>
      {error && <p className="error">{error}</p>}
      {versions && versions.length > 1 && (
        <>
          <div className="small"><strong>Versions</strong> <span className="muted">(click one to use it; free)</span></div>
          <div className="versions">
            {versions.map((v) => (
              <button key={v.id} type="button" className={`version ${v.active ? 'active' : ''}`} disabled={v.active || busy}
                title={`${v.provider === 'gemini_api' ? 'Nano Banana' : 'Z-Image'} · ${new Date(v.created_at).toLocaleString()}`}
                onClick={() => run(() => api.selectKeyframe(shot.id, v.id))}>
                <img src={assetUrl(v.id)} alt={v.active ? 'Current version' : 'Earlier version'} loading="lazy" />
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function StoryCard({ id, p, onChange }: { id: string; p: Production; onChange: () => void }) {
  return (
    <section className="card">
      <div className="card-head"><h2>Story</h2>{p.script && <span className="pill">Voice: {p.script.voiceName}</span>}</div>
      {p.concept && (
        <div style={{ marginBottom: 14 }}>
          <p style={{ margin: '0 0 6px' }}><strong>{p.concept.logline}</strong></p>
          <p className="muted small" style={{ margin: 0 }}>{p.concept.bigIdea}</p>
          {p.concept.clockLabel && <p className="small" style={{ margin: '6px 0 0' }}>On-screen clock: {p.concept.clockLabel}</p>}
        </div>
      )}
      {p.music && (
        <div className="row" style={{ marginBottom: 14 }}>
          <span className="small"><strong>Score</strong> · {Math.round(p.music.bpm)} BPM</span>
          <audio src={assetUrl(p.music.asset_id)} controls preload="none" className="grow" />
        </div>
      )}
      {p.script && (
        <ScriptEditor videoId={id} lines={p.script.lines} linesNeedingVoice={p.linesNeedingVoice} onChange={onChange} />
      )}
    </section>
  )
}

function ResearchCard({ facts, queries }: { facts: Fact[]; queries: string[] }) {
  return (
    <section className="card">
      <div className="card-head"><h2>Research</h2><span className="pill">{facts.length} facts · Google Search</span></div>
      <ul className="facts">
        {facts.map((f, i) => (
          <li key={i}>
            {f.text}
            {f.sources.length > 0 && (
              <span className="small muted"> — {f.sources.map((s, j) => (
                <a key={j} href={s.uri} target="_blank" rel="noreferrer noopener">{s.title}</a>
              )).reduce<ReactNode[]>((acc, el, j) => (j ? [...acc, ', ', el] : [el]), [])}</span>
            )}
          </li>
        ))}
      </ul>
      {queries?.length > 0 && <p className="muted small">Searched: {queries.join(' · ')}</p>}
    </section>
  )
}
