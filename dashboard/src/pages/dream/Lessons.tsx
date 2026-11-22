import { useState } from 'react'
import { dreamApi, type DreamLesson, type LessonTarget } from '../../dreamApi'
import { usePoll } from '../../hooks'

const TARGET_LABEL: Record<LessonTarget, string> = { WRITER: 'Writer and camera', PICTURE: 'Pictures', ALL: 'Everything' }

/** Lessons from the owner's reviews: proposed ones wait for approval; only active ones go into the prompts. */
export function Lessons() {
  const { data, refresh } = usePoll(() => dreamApi.lessons(), 30000)
  const [edits, setEdits] = useState<Record<string, string>>({})
  if (!data || data.length === 0) return null
  const set = (l: DreamLesson, change: Parameters<typeof dreamApi.lesson>[1]) => void dreamApi.lesson(l.id, change).then(refresh)
  return (
    <section className="card">
      <div className="card-head"><h2>Lessons</h2><span className="muted small">Only active lessons go into the next Shorts</span></div>
      <table className="small" style={{ width: '100%' }}><tbody>
        {data.map((l) => (
          <tr key={l.id}>
            <td><span className={`badge ${l.status === 'ACTIVE' ? 'b-ok' : 'b-info'}`}>{l.status === 'ACTIVE' ? 'Active' : 'Proposed'}</span></td>
            <td style={{ width: '60%' }}><input value={edits[l.id] ?? l.rule} aria-label="Lesson rule" style={{ width: '100%' }}
              onChange={(e) => setEdits({ ...edits, [l.id]: e.target.value })}
              onBlur={() => edits[l.id] !== undefined && edits[l.id] !== l.rule && set(l, { rule: edits[l.id] })} />
              {l.shot_ord != null && <span className="muted"> from clip {l.shot_ord + 1}{l.rating ? `, rated ${l.rating}/5` : ''}</span>}</td>
            <td><select value={l.target} aria-label="Applies to" onChange={(e) => set(l, { target: e.target.value as LessonTarget })}>
              {(Object.keys(TARGET_LABEL) as LessonTarget[]).map((t) => <option key={t} value={t}>{TARGET_LABEL[t]}</option>)}</select></td>
            <td className="row">{l.status !== 'ACTIVE' && <button className="primary" onClick={() => set(l, { status: 'ACTIVE' })}>Approve</button>}
              <button onClick={() => set(l, { status: 'RETIRED' })}>Retire</button></td>
          </tr>))}
      </tbody></table>
    </section>
  )
}
