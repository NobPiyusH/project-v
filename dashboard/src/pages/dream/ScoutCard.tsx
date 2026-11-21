import type { DreamDetail } from '../../dreamApi'
import { parseJson } from './shared'

interface ScoutData {
  source: string
  videos: { url: string; title: string | null; channel: string | null; views: number | null; shows: string; look: string;
    camera: string; shots: number; music: string; hook: string; tools: string; works: string }[]
  brief: { cliches: string[]; gaps: string[]; angles: { title: string; idea: string; why: string }[] }
}

/** What the Scout watched, what to avoid, and the angle the writer will follow (pick before approving the Scout). */
export function ScoutCard({ d, onPick }: { d: DreamDetail; onPick: (n: number) => void }) {
  if (!d.scout_json) return null
  const s = parseJson(d.scout_json) as unknown as ScoutData
  if (!s.brief) return null
  return (
    <section className="card">
      <div className="card-head"><h2>Scout</h2><span className="muted small">{s.videos.length} similar Shorts watched ({s.source})</span></div>
      <h3 className="small">Ways to be different: the writer follows the one you pick</h3>
      {s.brief.angles.map((a, i) => (
        <label key={i} className="small" style={{ display: 'block', marginBottom: 8 }}>
          <input type="radio" name="angle" checked={d.scout_angle === i + 1} onChange={() => onPick(i + 1)} />{' '}
          <strong>{a.title}</strong>: {a.idea} <span className="muted">({a.why})</span>
        </label>))}
      <p className="small"><strong>Most of them do:</strong> {s.brief.cliches.join('; ')}</p>
      <p className="small"><strong>None of them do:</strong> {s.brief.gaps.join('; ')}</p>
      <details className="small"><summary>What the Scout watched</summary>
        {s.videos.map((v, i) => (
          <div key={i} style={{ marginTop: 8 }}>
            <a href={v.url} target="_blank" rel="noreferrer">{v.title ?? v.url}</a>
            {v.views != null && <span className="muted"> · {v.views.toLocaleString()} views</span>}
            <div>{v.shows}</div>
            <div className="muted">Look: {v.look} · Camera: {v.camera} · {v.shots} shots · Music: {v.music} · Hook: {v.hook} · Made with: {v.tools}</div>
          </div>))}
      </details>
    </section>
  )
}
