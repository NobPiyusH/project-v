import { useState } from 'react'
import type { DreamDetail } from '../../dreamApi'

/** "Choose the path": the two end-card options (editable), the episode chain, and Continue this dream (owner picks A or B). */
export function PathCard({ d, onSave, onContinue }: { d: DreamDetail; onSave: (a: string, b: string) => void; onContinue: (p: 'A' | 'B') => void }) {
  const [a, setA] = useState(d.path_a ?? '')
  const [b, setB] = useState(d.path_b ?? '')
  const [edited, setEdited] = useState(false)
  const shownA = edited ? a : d.path_a ?? ''
  const shownB = edited ? b : d.path_b ?? ''
  const finished = d.status === 'COMPLETED' && !!d.final_asset_id
  return (
    <section className="card">
      <div className="card-head"><h2>Choose the path</h2><span className="pill">end card, last ~2 s · episode {d.episode}</span></div>
      {d.chain.length > 1 && <div className="row small" style={{ marginBottom: 8 }}>
        {d.chain.map((e) => <a key={e.id} href={`#/dream/${e.id}`} className={`pill ${e.id === d.id ? 'b-info' : ''}`}>
          Episode {e.episode}{e.chosen_path ? ` · ${e.chosen_path}` : ''}{e.pick_source === 'comments' ? ' (comments)' : ''}</a>)}
      </div>}
      <div className="row">
        <label className="small">← A <input value={shownA} placeholder="the door" style={{ width: 160 }}
          onChange={(e) => { setA(e.target.value); if (!edited) setB(shownB); setEdited(true) }} /></label>
        <label className="small">B → <input value={shownB} placeholder="the stairs" style={{ width: 160 }}
          onChange={(e) => { setB(e.target.value); if (!edited) setA(shownA); setEdited(true) }} /></label>
        <button disabled={!edited} onClick={() => { onSave(a, b); setEdited(false) }}>Save</button>
        <span style={{ flex: 1 }} />
        {finished && d.path_a && d.path_b && <>
          <span className="small">Continue this dream:</span>
          <button className="primary" onClick={() => { if (window.confirm(`Start episode ${d.episode + 1} down path A: "${d.path_a}"? It starts from this Short's last frame; paid steps ask before spending.`)) onContinue('A') }}>A: {d.path_a}</button>
          <button className="primary" onClick={() => { if (window.confirm(`Start episode ${d.episode + 1} down path B: "${d.path_b}"? It starts from this Short's last frame; paid steps ask before spending.`)) onContinue('B') }}>B: {d.path_b}</button>
        </>}
      </div>
      {d.children.length > 0 && <p className="small">Continued: {d.children.map((c) => <a key={c.id} href={`#/dream/${c.id}`} style={{ marginRight: 10 }}>{c.title ?? 'new episode'} ({c.chosen_path})</a>)}</p>}
      <p className="muted small">2-4 words each, written with the shots; your edit is kept when the shots are rewritten and shows at the next finish (Redo from Finish is free).</p>
    </section>
  )
}
