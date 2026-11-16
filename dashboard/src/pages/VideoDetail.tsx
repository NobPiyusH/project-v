import { Fragment, useState } from 'react'
import { api, type Inspection, type Mode, type StageInfo, type StageRun, type VideoDetail as Detail } from '../api'
import { Countdown, ModeSwitch, StatusBadge } from '../components'
import { fmtTime, usePoll } from '../hooks'
import { ProductionView } from './ProductionView'

interface Modes { default: Mode; stages: Record<string, Mode> }

const FINISHED = new Set(['AUTO_PASSED', 'APPROVED', 'SKIPPED'])

export function VideoDetail({ id, stages }: { id: string; stages: StageInfo[] }) {
  const { data: video, error, refresh } = usePoll(() => api.video(id), 1500, [id])
  const [actionError, setActionError] = useState<string | null>(null)

  if (error && !video) return <p className="error">{error}</p>
  if (!video) return <p className="muted">Loading…</p>

  const modes: Modes = JSON.parse(video.modes_json || '{"default":"AUTO","stages":{}}')
  const modeOf = (stage: string): Mode => modes.stages?.[stage] ?? modes.default
  const latest = latestRuns(video.stageRuns)
  const finished = video.status === 'COMPLETED' || video.status === 'CANCELLED'

  const act = async (fn: () => Promise<unknown>) => {
    setActionError(null)
    try {
      await fn()
      await refresh()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <>
      <section className="card">
        <div className="card-head">
          <div className="grow">
            <a href="#/" className="small">← All videos</a>
            <h1 style={{ marginTop: 6 }}>{video.title ?? 'Untitled video'}</h1>
          </div>
          <StatusBadge status={video.status} />
        </div>
        <p className="muted" style={{ marginTop: 0 }}>“{video.prompt}”</p>
        <div className="row">
          {!finished && (video.status === 'PAUSED'
            ? <button onClick={() => act(() => api.resume(id))}>Resume</button>
            : <button onClick={() => act(() => api.pause(id))}>Pause</button>)}
          <span className="grow" />
          {!finished && (
            <>
              <span className="muted small">Set all remaining stages</span>
              <button onClick={() => act(() => api.setAllModes(id, 'AUTO'))}>All Auto</button>
              <button onClick={() => act(() => api.setAllModes(id, 'REVIEW'))}>All Review</button>
              <button onClick={() => act(() => api.setAllModes(id, 'MANUAL'))}>All Manual</button>
              <label className="row small muted">
                Review window
                <input type="number" min={1} max={1440} defaultValue={video.review_minutes} style={{ width: 70 }}
                  onBlur={(e) => { const m = Number(e.target.value); if (m > 0 && m !== video.review_minutes) void act(() => api.setReviewMinutes(id, m)) }} /> min
              </label>
            </>
          )}
        </div>
        {actionError && <p className="error">{actionError}</p>}
      </section>

      <PlanCard video={video} />

      <ProductionView id={id} />

      <section className="card">
        <div className="card-head">
          <h2>Stages</h2>
          <span className="muted small">Started {fmtTime(video.created_at)}</span>
        </div>
        <div className="stages">
          {stages.map((s, i) => (
            <StageRow key={s.id} index={i + 1} stage={s} run={latest[s.id]} mode={modeOf(s.id)} stages={stages}
              inspection={(video.inspections ?? []).find((x) => x.stage === s.id && x.attempt === latest[s.id]?.attempt)}
              canRedo={finished && s.redoWith.length > 0 && latest[s.id] !== undefined && latest[s.id].status !== 'SKIPPED'}
              onRedo={() => act(() => api.rerun(id, s.id))}
              attempts={video.stageRuns.filter((r) => r.stage === s.id && r.attempt > 0).length}
              locked={finished || (latest[s.id] !== undefined && FINISHED.has(latest[s.id].status))}
              onMode={(m) => act(() => api.setMode(id, s.id, m))}
              onApprove={(notes) => act(() => api.approve(id, s.id, notes))}
              onReject={(notes) => act(() => api.reject(id, s.id, notes))} />
          ))}
        </div>
      </section>

      <CostsCard video={video} />
    </>
  )
}

function latestRuns(runs: StageRun[]): Record<string, StageRun> {
  const out: Record<string, StageRun> = {}
  for (const r of runs) {
    const cur = out[r.stage]
    if (!cur || r.attempt >= cur.attempt) out[r.stage] = r
  }
  return out
}

function StageRow(props: {
  index: number; stage: StageInfo; run?: StageRun; mode: Mode; attempts: number; locked: boolean
  inspection?: Inspection
  stages: StageInfo[]; canRedo: boolean; onRedo: () => void
  onMode: (m: Mode) => void; onApprove: (notes?: string) => void; onReject: (notes: string) => void
}) {
  const { index, stage, run, mode, attempts, locked } = props
  const [notes, setNotes] = useState('')
  const [showArtifact, setShowArtifact] = useState(false)
  const status = run?.status ?? 'PENDING'
  const failed = status === 'FAILED'
  const waiting = status === 'WAITING_APPROVAL' || failed

  return (
    <div className={`stage ${waiting ? 'waiting' : ''} ${status === 'SKIPPED' ? 'skipped' : ''}`}>
      <span className="num">{index}</span>
      <div>
        <div className="row">
          <span className="title">{stage.label}</span>
          <StatusBadge status={status} />
          {attempts > 1 && <span className="muted small">attempt {run?.attempt}</span>}
          {waiting && run?.review_deadline && <Countdown deadline={run.review_deadline} />}
        </div>
        <div className="muted small">{run?.summary ?? stage.description}</div>
        {run?.owner_notes && <div className="small">Your notes: “{run.owner_notes}”</div>}
        {props.inspection && <InspectorPanel report={props.inspection} />}
        <div className="row" style={{ marginTop: 6 }}>
          {run?.artifact_json && (
            <button className="small" style={{ padding: '2px 8px' }} onClick={() => setShowArtifact((s) => !s)}>
              {showArtifact ? 'Hide output' : 'Show output'}
            </button>
          )}
          {props.canRedo && (
            <button className="small" style={{ padding: '2px 8px' }}
              title={'Redoes: ' + stage.redoWith.map((r) => props.stages.find((x) => x.id === r)?.label ?? r).join(' → ')}
              onClick={() => {
                const names = stage.redoWith.map((r) => props.stages.find((x) => x.id === r)?.label ?? r).join(' → ')
                if (window.confirm(`Redo ${names}? Everything else is kept. This may use paid Gemini credits.`)) props.onRedo()
              }}>
              Redo
            </button>
          )}
        </div>
        {showArtifact && run?.artifact_json && (
          <pre className="card" style={{ marginTop: 8, overflow: 'auto', maxHeight: 320 }}>
            {JSON.stringify(JSON.parse(run.artifact_json), null, 2)}
          </pre>
        )}
        {waiting && (
          <div className="actions">
            <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
              placeholder={failed ? 'Optional notes for the retry' : 'Notes (required to reject: tell the system what to change)'} />
            <div className="row">
              {failed ? (
                <>
                  <button className="primary" onClick={() => { props.onReject(notes || 'Retry'); setNotes('') }}>Retry</button>
                  <button onClick={() => { props.onApprove(notes || undefined); setNotes('') }}>Continue without it</button>
                </>
              ) : (
                <>
                  <button className="primary" onClick={() => { props.onApprove(notes || undefined); setNotes('') }}>Approve</button>
                  <button className="danger" disabled={notes.trim() === ''}
                    onClick={() => { props.onReject(notes); setNotes('') }}>Reject & redo</button>
                  <span className="muted small">
                    {run?.review_deadline ? 'Or switch to Manual to hold it, or do nothing and it continues by itself.' : 'Or switch this stage to Auto to let it continue.'}
                  </span>
                </>
              )}
            </div>
          </div>
        )}
      </div>
      <ModeSwitch value={mode} onChange={props.onMode} disabled={locked} />
    </div>
  )
}

const VERDICT: Record<Inspection['verdict'], [string, string]> = {
  PASS: ['b-ok', 'Inspector: passed'],
  WARN: ['b-warn', 'Inspector: warnings'],
  FAIL: ['b-bad', 'Inspector: problems'],
}

/** Second-level check results for a stage: what the Inspector looked at and what it found. */
function InspectorPanel({ report }: { report: Inspection }) {
  const [open, setOpen] = useState(report.verdict === 'FAIL')
  const [cls, label] = VERDICT[report.verdict]
  const issues = report.findings.filter((f) => f.severity !== 'INFO')
  return (
    <div style={{ marginTop: 6 }}>
      <button className="small" style={{ padding: '2px 8px' }} onClick={() => setOpen((o) => !o)}
        title="Independent second-level checks of this stage's output">
        <span className={`badge ${cls}`}>{label}</span>{' '}
        {issues.length > 0 ? `${issues.length} finding${issues.length === 1 ? '' : 's'}` : 'all checks passed'}
        {report.checked.length > 0 && ` · ${report.checked.length} files checked`}
      </button>
      {open && issues.length > 0 && (
        <ul className="small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
          {issues.map((f, i) => (
            <li key={i}>
              <span className={`badge ${f.severity === 'FAIL' ? 'b-bad' : 'b-warn'}`}>{f.severity === 'FAIL' ? 'Problem' : 'Warning'}</span>{' '}
              {f.ref && <strong>{f.ref}: </strong>}{f.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function PlanCard({ video }: { video: Detail }) {
  if (!video.plan_json) {
    return <section className="card"><h2>Plan</h2><p className="muted">The planner is working…</p></section>
  }
  const { plan, source, fallbackReason } = JSON.parse(video.plan_json)
  const rows: [string, string][] = [
    ['Format', `${plan.format === 'short' ? 'Short / Reel' : 'Long video'} · ${plan.durationSec}s · ${plan.aspectRatio} · ${plan.outputResolution}`],
    ['Style', plan.stylePreset],
    ['Tone', plan.tone],
    ['Language', plan.language],
    ['Narration', plan.narration === 'none' ? 'None' : `${plan.narration} (${plan.voice})`],
    ['Music', plan.music],
    ['Research', plan.needsResearch ? 'Yes' : 'No'],
    ['Visuals', (plan.visualTypes ?? []).join(', ')],
    ['Shorts', String(plan.shortsCount)],
  ]
  if (plan.mustInclude?.length) rows.push(['Must include', plan.mustInclude.join(', ')])
  return (
    <section className="card">
      <div className="card-head">
        <h2>Plan</h2>
        <span className="pill">{source === 'rules' ? 'Rule-based planner' : `Planned by ${source}`}</span>
      </div>
      {plan.summary && <p style={{ marginTop: 0 }}>{plan.summary}</p>}
      <dl className="kv">{rows.map(([k, v]) => (<Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>))}</dl>
      {plan.rationale && <p className="muted small">{plan.rationale}</p>}
      {fallbackReason && <p className="muted small">Fallback reason: {fallbackReason}</p>}
    </section>
  )
}

function CostsCard({ video }: { video: Detail }) {
  return (
    <section className="card">
      <div className="card-head"><h2>Cost ledger</h2><span className="pill">₹{Number(video.spent_inr).toFixed(2)} spent</span></div>
      {video.costs.length === 0 ? <p className="muted small">Nothing spent or metered yet.</p> : (
        <table>
          <thead><tr><th>Provider</th><th>₹</th><th>Units</th><th className="hide-sm">Note</th></tr></thead>
          <tbody>
            {video.costs.map((c, i) => (
              <tr key={i}>
                <td>{c.provider_id}</td>
                <td>{Number(c.amount_inr).toFixed(2)}{c.estimated ? ' (est.)' : ''}</td>
                <td>{c.units ?? '—'} {c.unit_type ?? ''}</td>
                <td className="hide-sm muted small">{c.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
