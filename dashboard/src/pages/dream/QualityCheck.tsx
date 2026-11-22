import { parseJson } from './shared'

const CHECK_LABEL: Record<string, string> = {
  noPeople: 'No people', cleanText: 'Clean text, no brands', looksReal: 'Looks real (no warping)', continuity: 'Continuity',
  humanCamera: 'Human camera', musicFit: 'Music fit', story: 'Story and ending', feelGood: 'Feels good',
}

/** Learning loop, phase 3: Gemini's check of the finished Short against the owner's checklist. */
export function QualityCheck({ json, title }: { json: string | null; title: string }) {
  if (!json) return null
  const c = parseJson(json) as { overall: number; summary: string; scores: Record<string, number>;
    issues: { shot: number; atSec: number; problem: string; fix: string }[] }
  return (
    <section className="card">
      <div className="card-head"><h2>{title}</h2><span className="pill">{c.overall}/10</span></div>
      <p className="small">{c.summary}</p>
      {c.issues.length > 0 && <ul className="small">{c.issues.map((i, k) => (
        <li key={k}><strong>{i.shot > 0 ? `Shot ${i.shot}` : 'Whole Short'}{i.atSec ? ` at ${i.atSec.toFixed(1)} s` : ''}:</strong>{' '}
          {i.problem} <span className="muted">Fix: {i.fix}</span></li>))}</ul>}
      <div className="small muted">{Object.entries(c.scores).map(([k, v]) => `${CHECK_LABEL[k] ?? k} ${v}`).join(' · ')}</div>
    </section>
  )
}
