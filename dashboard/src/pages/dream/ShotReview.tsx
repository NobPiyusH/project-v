import { useState } from 'react'
import { dreamApi } from '../../dreamApi'

/** Learning loop: the owner rates a clip or the Short; a note becomes a proposed lesson in the Lessons list. */
export function ShotReview({ dreamId, shotId }: { dreamId: string; shotId: string | null }) {
  const [rating, setRating] = useState(0)
  const [note, setNote] = useState('')
  const [saved, setSaved] = useState('')
  const save = () => dreamApi.review(dreamId, shotId, rating, note)
    .then(() => { setSaved(note.trim() ? 'Saved. Your note is now a proposed lesson.' : 'Saved.'); setNote('') })
    .catch((e: Error) => setSaved(e.message))
  return (
    <div className="small" style={{ marginTop: 6 }}>
      <div className="row" style={{ gap: 2 }} role="radiogroup" aria-label="Rating">
        {[1, 2, 3, 4, 5].map((n) => <button key={n} type="button" aria-label={`${n} of 5`} aria-checked={rating === n} role="radio"
          onClick={() => setRating(n)} style={{ padding: '0 4px', color: n <= rating ? 'var(--accent)' : 'var(--muted)' }}>★</button>)}
      </div>
      <textarea rows={2} value={note} placeholder={shotId ? 'What was wrong or right in this clip?' : 'What should the next Shorts do differently?'}
        aria-label="Review note" onChange={(e) => setNote(e.target.value)} style={{ width: '100%', marginTop: 4 }} />
      <div className="row"><button disabled={rating === 0} onClick={() => void save()}>Save review</button>
        {saved && <span className="muted">{saved}</span>}</div>
    </div>
  )
}
