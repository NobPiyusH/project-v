import { useState } from 'react'
import { api, assetUrl, type Fact, type Production } from '../api'
import { usePoll } from '../hooks'

/** What the stages have actually produced so far: research, concept, script + voice, music, shots, animatic. */
export function ProductionView({ id }: { id: string }) {
  const { data: p } = usePoll(() => api.production(id), 4000, [id])
  if (!p) return null
  const empty = !p.research && !p.concept && !p.script && !p.music && p.shots.length === 0
  if (empty) return null

  return (
    <>
      {p.animaticAssetId && <AnimaticCard assetId={p.animaticAssetId} />}
      {p.shots.length > 0 && <ShotGrid p={p} />}
      {(p.concept || p.script || p.music) && <StoryCard p={p} />}
      {p.research && <ResearchCard facts={JSON.parse(p.research.facts) as Fact[]} queries={p.research.queries} />}
    </>
  )
}

function AnimaticCard({ assetId }: { assetId: string }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>Animatic</h2>
        <span className="pill">Draft: keyframes + voice + music, to check story and pacing before video generation</span>
      </div>
      <video className="player" src={assetUrl(assetId)} controls preload="metadata" />
    </section>
  )
}

function ShotGrid({ p }: { p: Production }) {
  const [open, setOpen] = useState<string | null>(null)
  const heroes = p.shots.filter((s) => s.priority === 'hero').length
  return (
    <section className="card">
      <div className="card-head">
        <h2>Shots</h2>
        <span className="pill">{p.shots.length} shots · {heroes} hero</span>
      </div>
      <div className="shot-grid">
        {p.shots.map((s) => (
          <figure key={s.id} className={`shot ${s.priority === 'hero' ? 'hero' : ''}`} onClick={() => setOpen(open === s.id ? null : s.id)}>
            {s.keyframe_asset_id
              ? <img src={assetUrl(s.keyframe_asset_id)} alt={s.description ?? `Shot ${s.ord + 1}`} loading="lazy" />
              : <div className="shot-empty">No keyframe yet</div>}
            <figcaption>
              <div className="row small">
                <strong>#{s.ord + 1}</strong>
                <span className="muted">{Number(s.duration_target_sec).toFixed(1)}s · {s.camera_move ?? ''}</span>
                {s.priority === 'hero' && <span className="badge b-warn">hero</span>}
                {s.keyframe_provider && <span className="muted small">{s.keyframe_provider === 'gemini_api' ? 'Nano Banana' : 'Z-Image'}</span>}
              </div>
              <div className="small">{s.description}</div>
              {s.overlay_text && <div className="small muted">On screen: “{s.overlay_text}”</div>}
              {open === s.id && (
                <div className="small muted" style={{ marginTop: 6 }}>
                  <div><strong>Image prompt:</strong> {s.prompt_json.keyframePrompt}</div>
                  <div><strong>Motion:</strong> {s.prompt_json.motionPrompt}</div>
                </div>
              )}
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  )
}

function StoryCard({ p }: { p: Production }) {
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
        <ol className="script">
          {p.script.lines.map((l) => (
            <li key={l.index} className={l.spoken ? '' : 'visual'}>
              <div>{l.spoken ? l.text : <em>[visual] {l.text}</em>}</div>
              {l.audioAssetId && (
                <div className="row small muted">
                  <audio src={assetUrl(l.audioAssetId)} controls preload="none" />
                  {l.durationSec != null && <span>{l.durationSec.toFixed(1)}s</span>}
                </div>
              )}
            </li>
          ))}
        </ol>
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
              )).reduce<React.ReactNode[]>((acc, el, j) => (j ? [...acc, ', ', el] : [el]), [])}</span>
            )}
          </li>
        ))}
      </ul>
      {queries?.length > 0 && <p className="muted small">Searched: {queries.join(' · ')}</p>}
    </section>
  )
}
