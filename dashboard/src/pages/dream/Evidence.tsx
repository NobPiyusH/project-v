import type { DreamDetail } from '../../dreamApi'
import { parseJson } from './shared'

// The owner's example Shorts, measured the same way (19 Nov 2026): what "good" looks like for the learning loop.
const EXAMPLES: Record<string, string> = {
  motion: '1.2–1.5 (lonely object), 3.2 (walk-through)', colour: '26–27 (lonely object), 15 (walk-through)',
  shadows: '—', loudnessLufs: '−14', cuts: '3–4 per 15–20 s', climaxAtPct: '38–68', shortestShotSec: '≥ 3.4',
}
const METRIC_LABEL: Record<string, string> = {
  motion: 'Camera / scene motion', colour: 'Colour strength', shadows: 'Darkest areas (higher = more detail)',
  cuts: 'Cuts', cutsOnBeat: 'Cuts on the beat', loudnessLufs: 'Loudness (LUFS)', durationSec: 'Length (s)',
  shots: 'Shots', speed: 'Playback speed', hookShot: 'Opening (hook) shot', climaxAtPct: 'Climax at (% of length)',
  musicRiseSec: 'Music rise at (s)', shortestShotSec: 'Shortest shot (s)', ambiences: 'Ambience beds',
}

/** Learning loop, phase 1: what this Short measured, what each paid step cost against its estimate, every decision. */
export function Evidence({ d }: { d: DreamDetail }) {
  const latest = d.metrics[0] ? parseJson(d.metrics[0].metrics) as Record<string, number> : null
  const costs = d.events.filter((e) => e.kind === 'cost')
  const decisions = d.events.filter((e) => e.kind !== 'cost')
  return (
    <section className="card">
      <div className="card-head"><h2>Evidence</h2><span className="muted small">what the studio learns from</span></div>
      {latest && <table className="small" style={{ width: '100%', marginBottom: 12 }}>
        <thead><tr><th align="left">Measured</th><th align="left">This Short</th><th align="left">Your examples</th></tr></thead>
        <tbody>{Object.keys(METRIC_LABEL).filter((k) => latest[k] !== undefined).map((k) => (
          <tr key={k}><td>{METRIC_LABEL[k]}</td><td>{latest[k]}</td><td className="muted">{EXAMPLES[k] ?? ''}</td></tr>))}</tbody>
      </table>}
      {costs.length > 0 && <div className="small" style={{ marginBottom: 12 }}>
        {costs.map((e, i) => { const x = parseJson(e.data) as Record<string, number>; return (
          <div key={i}>{e.step}: estimated ₹{x.estimateInr} → actually ₹{x.actualInr}
            {x.ratio > 1.2 || (x.ratio > 0 && x.ratio < 0.8) ? <span className="error"> (off by {x.ratio}×)</span> : null}</div>) })}
      </div>}
      {decisions.length === 0 && !latest && <p className="muted small">Nothing recorded yet.</p>}
      {decisions.map((e, i) => (
        <div key={i} className="small muted">{new Date(e.created_at).toLocaleString()} · {e.kind}{e.step ? ` ${e.step}` : ''}
          {e.notes ? ` · “${e.notes}”` : ''}{e.data && e.kind === 'mode' ? ` · ${String(parseJson(e.data).mode)}` : ''}</div>))}
    </section>
  )
}
