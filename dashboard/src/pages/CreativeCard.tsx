import { useState } from 'react'
import type { CreativeReport } from '../api'

/** 1-2 wrong, 3 weak, 4-5 good: same thresholds as the control plane (CreativeDirector.FAIL_AT / WARN_AT). */
function Score({ label, value }: { label: string; value: number }) {
  const cls = value <= 2 ? 'b-bad' : value === 3 ? 'b-warn' : 'b-ok'
  return <span className={`badge ${cls}`} title={`${label}: ${value} out of 5`}>{label} {value}/5</span>
}

const STAGE_LABEL: Record<CreativeReport['stage'], string> = {
  SHOT_PLAN: 'Shot plan (before images)',
  KEYFRAMES: 'Images (what was actually made)',
}

/**
 * The Creative Director's report card: does the picture story match the narration, for the whole video, each
 * paragraph and each shot? Shows the images review when there is one, else the shot-plan review.
 */
export function CreativeCard({ reports, canReview, onReview }: {
  reports: CreativeReport[]; canReview: boolean; onReview: () => void
}) {
  const ordered = [...reports].sort((a, b) => (a.stage === 'KEYFRAMES' ? -1 : 1) - (b.stage === 'KEYFRAMES' ? -1 : 1))
  const [stage, setStage] = useState<CreativeReport['stage'] | null>(null)
  const reviewButton = canReview && (
    <button className="small" style={{ padding: '2px 8px' }} title="A vision model checks every image against its line, paragraph and the whole video. Nothing is regenerated."
      onClick={() => { if (window.confirm('Review the existing images? Uses a little Gemini credit (about ₹1–2 for a Short).')) onReview() }}>
      Review images
    </button>
  )
  if (ordered.length === 0) {
    return canReview ? (
      <section className="card">
        <div className="card-head"><h2>Creative Director</h2><span className="grow" />{reviewButton}</div>
        <p className="muted small" style={{ marginTop: 0 }}>Not reviewed yet. New videos are reviewed automatically after the shot plan and the images.</p>
      </section>
    ) : null
  }
  const report = ordered.find((r) => r.stage === stage) ?? ordered[0]
  const { video, acts, shots } = report.review
  const weakShots = (shots ?? []).filter((s) => Math.min(s.meaning, s.accuracy, s.continuity) <= 3 || s.literal)
  return (
    <section className="card">
      <div className="card-head">
        <h2>Creative Director</h2>
        <span className="grow" />
        {ordered.length > 1 && ordered.map((r) => (
          <button key={r.stage} className="small" style={{ padding: '2px 8px' }} disabled={r.stage === report.stage}
            onClick={() => setStage(r.stage)}>{STAGE_LABEL[r.stage]}</button>
        ))}
        {reviewButton}
      </div>
      <p className="muted small" style={{ marginTop: 0 }}>
        {STAGE_LABEL[report.stage]} · attempt {report.attempt}{report.model ? ` · reviewed by ${report.model}` : ''}.
        Judges whether the pictures show the meaning of what is said: per shot, per paragraph and for the whole video.
      </p>

      {video && (
        <div style={{ marginBottom: 12 }}>
          <div className="row"><strong>Whole video</strong>
            <Score label="Arc" value={video.arc} /><Score label="Hook" value={video.hook} />
            <Score label="Ending" value={video.ending} /><Score label="Variety" value={video.variety} />
          </div>
          {(video.problem || video.summary) && <div className="small">{video.problem || video.summary}</div>}
        </div>
      )}

      {(acts ?? []).length > 0 && (
        <table>
          <thead><tr><th>Paragraph</th><th>Scores</th><th className="hide-sm">Notes</th></tr></thead>
          <tbody>
            {acts.map((a, i) => (
              <tr key={i}>
                <td>{a.act}</td>
                <td>
                  <div className="row" style={{ gap: 4 }}>
                    <Score label="Flow" value={a.flow} /><Score label="Key moment" value={a.keyMoment} />
                    <Score label="Continuity" value={a.continuity} /><Score label="Mood" value={a.mood} />
                  </div>
                </td>
                <td className="hide-sm small">
                  {a.problem || a.summary}
                  {a.shotsToFix?.length > 0 && <span className="muted"> (fix: shots {a.shotsToFix.join(', ')})</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 style={{ marginBottom: 4 }}>Shots</h3>
      {weakShots.length === 0 ? (
        <p className="muted small">Every shot shows the meaning of its line ({(shots ?? []).length} checked).</p>
      ) : (
        <ul className="small" style={{ paddingLeft: 18, margin: 0 }}>
          {weakShots.map((s) => (
            <li key={s.shot} style={{ marginBottom: 6 }}>
              <strong>Shot {s.shot}</strong>{' '}
              <Score label="Meaning" value={s.meaning} /> <Score label="Accuracy" value={s.accuracy} />{' '}
              <Score label="Continuity" value={s.continuity} />
              {s.literal && <span className="badge b-bad">shows one word, not the idea</span>}
              <div>{s.problem}</div>
              {s.fixPrompt && <div className="muted">Better prompt: {s.fixPrompt}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
