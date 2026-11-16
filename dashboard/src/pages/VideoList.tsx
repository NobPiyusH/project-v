import { useState } from 'react'
import { api, type Mode, type StageInfo } from '../api'
import { ModeSwitch, StatusBadge } from '../components'
import { fmtTime, usePoll } from '../hooks'

export function VideoList({ stages }: { stages: StageInfo[] }) {
  const { data: videos, error } = usePoll(api.videos, 3000)

  return (
    <>
      <NewVideo stages={stages} />
      <section className="card">
        <div className="card-head"><h2>Videos</h2></div>
        {error && <p className="error">Can't reach the control plane: {error}</p>}
        {videos && videos.length === 0 && <p className="empty">No videos yet. Write a prompt above to start one.</p>}
        {videos && videos.length > 0 && (
          <table>
            <thead>
              <tr><th>Title</th><th>Status</th><th className="hide-sm">Stage</th><th className="hide-sm">Created</th></tr>
            </thead>
            <tbody>
              {videos.map((v) => (
                <tr key={v.id} className="clickable" onClick={() => (window.location.hash = `#/video/${v.id}`)}>
                  <td>
                    <a href={`#/video/${v.id}`}>{v.title ?? v.prompt}</a>
                    {v.title && v.title !== v.prompt && <div className="muted small">{v.prompt}</div>}
                  </td>
                  <td><StatusBadge status={v.status} /></td>
                  <td className="hide-sm">{stages.find((s) => s.id === v.current_stage)?.label ?? ''}</td>
                  <td className="hide-sm muted small">{fmtTime(v.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}

function NewVideo({ stages }: { stages: StageInfo[] }) {
  const [prompt, setPrompt] = useState('')
  const [defaultMode, setDefaultMode] = useState<Mode>('AUTO')
  const [overrides, setOverrides] = useState<Record<string, Mode>>({})
  const [showStages, setShowStages] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const { id } = await api.createVideo(prompt, defaultMode, overrides)
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
        <span className="grow muted small">
          Auto: the system does the stage and moves on. Manual: it prepares the stage and waits for you.
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
