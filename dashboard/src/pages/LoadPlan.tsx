import { useState } from 'react'
import { api, type Mode, type PlanCheck, type StageInfo } from '../api'
import { ModeSwitch } from '../components'

/** Stages that cannot be dropped from a plan but can be left out of it: stage id -> the plan's own switch (video.<field> = "none"). */
const LEAVE_OUT: Record<string, 'music' | 'narration'> = { MUSIC: 'music', NARRATION: 'narration' }

/**
 * "Load plan file" inside New video: paste or pick a plan JSON, see what it will do and which stages call paid services, then Start.
 * The paid stages begin on Manual (their result waits for Approve). A mode is applied after a stage ran, so the only way to stop the
 * spend of Music or Voice is "Leave out", which sends the plan with video.music / video.narration = "none".
 */
export function LoadPlan({ stages }: { stages: StageInfo[] }) {
  const [text, setText] = useState('')
  const [check, setCheck] = useState<PlanCheck | null>(null)
  const [defaultMode, setDefaultMode] = useState<Mode>('AUTO')
  const [paidModes, setPaidModes] = useState<Record<string, Mode>>({})
  const [leaveOut, setLeaveOut] = useState<Record<string, boolean>>({})
  const [reviewMinutes, setReviewMinutes] = useState(10)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const parse = (): Record<string, unknown> | null => {
    try {
      const doc = JSON.parse(text)
      if (doc && typeof doc === 'object' && !Array.isArray(doc)) return doc as Record<string, unknown>
    } catch { /* reported below */ }
    return null
  }

  const run = async (raw: string) => {
    setError(null)
    setCheck(null)
    let doc: unknown
    try { doc = JSON.parse(raw) } catch (e) {
      setCheck({ ok: false, error: `This is not valid JSON (${e instanceof Error ? e.message : String(e)})` })
      return
    }
    setBusy(true)
    try { setCheck(await api.validatePlan(doc)) } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }

  const pick = async (f: File | undefined) => {
    if (!f) return
    const raw = await f.text()
    setText(raw)
    void run(raw)
  }

  const modeOf = (id: string): Mode => paidModes[id] ?? 'MANUAL'

  const start = async () => {
    const doc = parse()
    if (!doc || !check?.ok) return
    const plan = JSON.parse(JSON.stringify(doc)) as { video?: Record<string, unknown> }
    for (const [id, field] of Object.entries(LEAVE_OUT)) {
      if (leaveOut[id]) plan.video = { ...(plan.video ?? {}), [field]: 'none' }
    }
    const modes: Record<string, Mode> = {}
    for (const p of check.paidStages) if (!leaveOut[p.id]) modes[p.id] = modeOf(p.id)
    setBusy(true)
    setError(null)
    try {
      const { id } = await api.createVideoFromPlan(plan, defaultMode, modes, reviewMinutes)
      window.location.hash = `#/video/${id}`
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const label = (id: string) => stages.find((s) => s.id === id)?.label ?? id

  return (
    <details style={{ marginTop: 14 }}>
      <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Load plan file</summary>
      <p className="muted small">Paste a plan (like docs/shorts/moon-10x-closer-plan.json) or pick the file. Nothing is created or spent until you press Start video.</p>
      <textarea rows={5} value={text} onChange={(e) => { setText(e.target.value); setCheck(null) }} spellCheck={false}
        placeholder={'{ "video": { … }, "scenes": [ … ], "script": [ … ] }'} />
      <div className="row" style={{ marginTop: 8 }}>
        <input type="file" accept=".json,application/json" onChange={(e) => void pick(e.target.files?.[0])} />
        <span className="grow" />
        <button type="button" disabled={busy || text.trim() === ''} onClick={() => void run(text)}>Check plan</button>
      </div>
      {error && <p className="error">{error}</p>}
      {check && !check.ok && <p className="error"><strong>This plan can't start:</strong> {check.error}</p>}
      {check?.ok && (
        <div style={{ marginTop: 12, display: 'grid', gap: 12 }}>
          <div>
            <strong>{check.title}</strong>
            <div className="muted small">
              {check.beats.length} beat{check.beats.length === 1 ? '' : 's'} · {check.totalSec} s · {check.width}×{check.height}
              {' · '}Camera: {check.cameraPresets.length ? check.cameraPresets.join(', ') : 'none named'}
              {' · '}Moon layer: <strong>{check.moonLayer ? 'yes' : 'no'}</strong>
              {check.moonLayer && check.moonImage && <> (picture {check.moonImage})</>}
            </div>
          </div>
          <table>
            <thead><tr><th>Beat</th><th>Starts</th><th>Seconds</th><th>Camera</th><th>Prompt</th></tr></thead>
            <tbody>
              {check.beats.map((b) => (
                <tr key={b.n}>
                  <td>{b.n}</td><td>{b.startSec}s</td><td>{b.durationSec}s</td><td>{b.camera ?? '—'}</td>
                  <td className="muted small">{b.prompt.length > 110 ? `${b.prompt.slice(0, 110)}…` : b.prompt}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div>
            <div className="small muted">Narration ({check.lines.length} line{check.lines.length === 1 ? '' : 's'})</div>
            {check.lines.map((l) => (
              <div key={l.n} className="small">{l.n}. “{l.text}” <span className="muted">{l.beat ? `beat ${l.beat}` : ''}{l.startSec != null ? ` · ${l.startSec}s` : ''}</span></div>
            ))}
          </div>
          <div className="small">
            {check.estimate
              ? <>Draft clips: about <strong>₹{check.estimate.inrLow}–₹{check.estimate.inrHigh}</strong> (${check.estimate.usdLow}–${check.estimate.usdHigh}),
                  {' '}{check.estimate.clips} clips, ~{check.estimate.minutes} min. <span className="muted">{check.estimate.note}</span></>
              : <span className="muted">{check.estimateError ?? 'No cost estimate for this plan.'}</span>}
          </div>

          <div className="card" style={{ margin: 0 }}>
            <strong>These stages call paid services</strong>
            <p className="muted small" style={{ margin: '4px 0 8px' }}>
              They start on <strong>Manual</strong>: the stage runs, then waits for your Approve before the next one starts. A mode never stops
              the stage's own spend. To make sure Music or Voice spends nothing, tick <strong>Leave out</strong>.
            </p>
            <table>
              <tbody>
                {check.paidStages.map((p) => (
                  <tr key={p.id}>
                    <td><strong>{label(p.id)}</strong><div className="muted small">{p.note}</div></td>
                    <td style={{ textAlign: 'right' }}>
                      {p.leaveOut && (
                        <label className="small" style={{ marginRight: 10 }}>
                          <input type="checkbox" checked={!!leaveOut[p.id]} onChange={(e) => setLeaveOut((o) => ({ ...o, [p.id]: e.target.checked }))} /> Leave out (no spend)
                        </label>
                      )}
                      <ModeSwitch value={modeOf(p.id)} disabled={!!leaveOut[p.id]} onChange={(m) => setPaidModes((o) => ({ ...o, [p.id]: m }))} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="row">
            <span className="muted small">Every other stage</span>
            <ModeSwitch value={defaultMode} onChange={setDefaultMode} />
            <label className="row small muted">
              Review window
              <input type="number" min={1} max={1440} value={reviewMinutes} style={{ width: 70 }}
                onChange={(e) => setReviewMinutes(Math.max(1, Number(e.target.value) || 10))} /> min
            </label>
            <span className="grow" />
            <button className="primary" disabled={busy} onClick={() => void start()}>{busy ? 'Starting…' : 'Start video'}</button>
          </div>
        </div>
      )}
    </details>
  )
}
