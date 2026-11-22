import { useState } from 'react'
import type { DreamDetail } from '../../dreamApi'
import { HOOK_COLOURS } from './shared'

/** Edit the hook title; the starred word is the big coloured one. It shows at the next finish (Redo from Finish is free). */
export function HookCard({ d, onSave }: { d: DreamDetail; onSave: (text: string, color: string) => void }) {
  const [text, setText] = useState(d.hook_text ?? '')
  const [color, setColor] = useState(d.hook_color ?? 'red')
  const [edited, setEdited] = useState(false)
  const shown = edited ? text : d.hook_text ?? ''
  return (
    <section className="card">
      <div className="card-head"><h2>Hook title</h2><span className="pill">first ~2 s of the finished Short</span></div>
      <div className="row">
        <input value={shown} placeholder="Written with the shots: edit it here (*star* the big word)" style={{ flex: 1, minWidth: 240 }}
          onChange={(e) => { setText(e.target.value); setEdited(true) }} />
        <label className="small">Big word <select value={color} onChange={(e) => { setColor(e.target.value); setEdited(true) }}>
          {HOOK_COLOURS.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        <button disabled={!edited} onClick={() => { onSave(text, color); setEdited(false) }}>Save</button>
      </div>
      <p className="muted small">Up to 3 lines; the starred word is large and coloured. Saved text is kept when the shots are rewritten.</p>
    </section>
  )
}
