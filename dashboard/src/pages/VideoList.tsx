import { useState } from 'react'
import { api, type Mode, type StageInfo } from '../api'
import { ModeSwitch } from '../components'

export function NewVideo({ stages }: { stages: StageInfo[] }) {
  const [prompt, setPrompt] = useState('')
  const [defaultMode, setDefaultMode] = useState<Mode>('AUTO')
  const [overrides, setOverrides] = useState<Record<string, Mode>>({})
  const [reviewMinutes, setReviewMinutes] = useState(10)
  const [showStages, setShowStages] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const { id } = await api.createVideo(prompt, defaultMode, overrides, reviewMinutes)
      window.location.hash = `#/video/${id}`
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>New video</h2>
        <div className="row small">
          <span className="muted">All stages</span>
          <ModeSwitch value={defaultMode} onChange={(m) => { setDefaultMode(m); setOverrides({}) }} />
        </div>
      </div>
      <textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)}
        placeholder={'Describe the video you want. For example: "8-minute cinematic timelapse of the Himalayas forming, awe-inspiring, sparse narration" or "45-second Short on how UPI fraud works, in Hindi".'} />
      <div className="row" style={{ marginTop: 10 }}>
        <button type="button" onClick={() => setShowStages((s) => !s)}>
          {showStages ? 'Hide' : 'Set'} mode per stage
        </button>
        <label className="row small muted">
          Review window
          <input type="number" min={1} max={1440} value={reviewMinutes} style={{ width: 70 }}
            onChange={(e) => setReviewMinutes(Math.max(1, Number(e.target.value) || 10))} /> min
        </label>
        <span className="grow muted small">
          Auto: does the stage and moves on. Review: waits the review window for you, then moves on by itself. Manual: waits until you approve.
        </span>
        <button className="primary" disabled={busy || prompt.trim().length === 0} onClick={submit}>
          {busy ? 'Starting…' : 'Create video'}
        </button>
      </div>
      {showStages && (
        <table style={{ marginTop: 12 }}>
          <tbody>
            {stages.map((s) => (
              <tr key={s.id}>
                <td><strong>{s.label}</strong><div className="muted small">{s.description}</div></td>
                <td style={{ textAlign: 'right' }}>
                  <ModeSwitch value={overrides[s.id] ?? defaultMode}
                    onChange={(m) => setOverrides((o) => ({ ...o, [s.id]: m }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  )
}
