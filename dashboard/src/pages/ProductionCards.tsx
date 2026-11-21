// Cards for a studio video's production (animatic, story, research) and the shot editor the Storyboard opens.
import { useState, type ReactNode } from 'react'
import { api, assetUrl, type Fact, type ImagePolicy, type Production, type ShotRow, type ShotVersion } from '../api'
import { usePoll } from '../hooks'
import { ScriptEditor } from './ScriptEditor'

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

export function AnimaticCard({ id, p, onChange }: { id: string; p: Production; onChange: () => void }) {
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

export function PolicySelect({ value, onChange }: { value: ImagePolicy; onChange: (p: ImagePolicy) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as ImagePolicy)} aria-label="Image model">
      {(Object.keys(POLICY_LABEL) as ImagePolicy[]).map((k) => <option key={k} value={k}>{POLICY_LABEL[k]}</option>)}
    </select>
  )
}

export function ShotEditor({ videoId, shot, onChange }: { videoId: string; shot: ShotRow; onChange: () => void }) {
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
      <div className="small muted"><strong>Motion prompt (for the video clip):</strong> {shot.prompt_json.motionPrompt}</div>
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
      <FootagePanel videoId={videoId} shot={shot} onChange={onChange} />
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

/**
 * Free real NASA footage for this shot: the Director's suggestions (or your own search), each with a preview. Picking
 * one replaces the generated clip for this shot, so no GPU time is spent on it.
 */
function FootagePanel({ videoId, shot, onChange }: { videoId: string; shot: ShotRow; onChange: () => void }) {
  const [query, setQuery] = useState(shot.prompt_json.footageQuery ?? '')
  const [start, setStart] = useState('')
  const [preview, setPreview] = useState<string | null>(null)
  const { error, busy, run } = useAction(onChange)
  const candidates = shot.prompt_json.footageCandidates ?? []
  return (
    <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
      <div className="small"><strong>Real footage (NASA, free)</strong>{' '}
        <span className="muted">{shot.footage_asset_id ? 'This shot uses real footage.' : 'Use a real clip instead of generating one.'}</span>
      </div>
      {shot.footage_asset_id && (
        <div className="row small">
          <video src={assetUrl(shot.footage_asset_id)} muted playsInline controls preload="metadata" style={{ width: 120 }} />
          <button disabled={busy} onClick={() => run(() => api.clearFootage(shot.id))}>Use the generated clip instead</button>
        </div>
      )}
      <div className="row">
        <input className="grow" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. solar flare SDO, Earth night lights ISS"
          aria-label="Search NASA footage" />
        <button disabled={busy || query.trim() === ''} onClick={() => run(() => api.footage(videoId, shot.id, { query }))}>
          {busy ? 'Searching…' : 'Search'}
        </button>
      </div>
      {shot.prompt_json.footageError && <div className="small error">Search failed: {shot.prompt_json.footageError}</div>}
      {candidates.length > 0 && (
        <>
          <label className="row small muted">Start at second
            <input type="number" min={0} step={0.5} value={start} onChange={(e) => setStart(e.target.value)} style={{ width: 70 }}
              placeholder="auto" /> (auto skips the title card)</label>
          {candidates.map((c) => (
            <div key={c.nasaId} className="row small" style={{ alignItems: 'flex-start' }}>
              {preview === c.nasaId
                ? <video src={c.previewUrl} autoPlay muted loop playsInline controls style={{ width: 160 }} />
                : <img src={c.thumbUrl} alt={c.title} loading="lazy" style={{ width: 160, cursor: 'pointer' }}
                  title="Play preview" onClick={() => setPreview(c.nasaId)} />}
              <div className="grow">
                <div><strong>{c.title}</strong> <span className="muted">{c.date}</span></div>
                <div className="muted">{c.description}</div>
                <div className="muted">Credit: NASA{c.credit ? ` / ${c.credit}` : ''}</div>
                <button className="primary" disabled={busy} style={{ marginTop: 4 }}
                  onClick={() => run(() => api.footage(videoId, shot.id, { nasaId: c.nasaId, startSec: start === '' ? null : Number(start) }))}>
                  Use this clip
                </button>
              </div>
            </div>
          ))}
        </>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  )
}

export function StoryCard({ id, p, onChange }: { id: string; p: Production; onChange: () => void }) {
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

export function ResearchCard({ facts, queries }: { facts: Fact[]; queries: string[] }) {
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
