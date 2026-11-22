import { Fragment, useState } from 'react'
import { RoutingDecisionsCard } from './Routing'
import { ProducerCard } from './ProducerCard'
import { api, assetUrl, type Fact, type Inspection, type Mode, type StageInfo, type StageRun, type VideoDetail as Detail } from '../api'
import { Countdown, ModeSwitch, STATUS_TEXT, StatusBadge } from '../components'
import { fmtTime, usePoll } from '../hooks'
import { CreativeCard } from './CreativeCard'
import { AnimaticCard, ResearchCard, StoryCard } from './ProductionCards'
import { Storyboard } from './Storyboard'

interface Modes { default: Mode; stages: Record<string, Mode> }

const FINISHED = new Set(['AUTO_PASSED', 'APPROVED', 'SKIPPED'])

type Tab = 'storyboard' | 'story' | 'reviews' | 'stages' | 'plan'

const DONE = new Set(['AUTO_PASSED', 'APPROVED', 'AUTO_APPROVED'])

function pipeClass(status: string | undefined) {
  if (!status) return ''
  if (DONE.has(status)) return 'done'
  if (status === 'RUNNING') return 'running'
  if (status === 'WAITING_APPROVAL') return 'waiting'
  if (status === 'FAILED' || status === 'INSPECTION_FAILED' || status === 'REJECTED') return 'failed'
  if (status === 'SKIPPED') return 'skipped'
  return ''
}

/**
 * The video workspace: what the video looks like now (master, else animatic, else first frame), what needs the owner,
 * the whole pipeline at a glance, and tabs for the storyboard, story, reviews, stages and plan/costs.
 */
export function VideoDetail({ id, stages }: { id: string; stages: StageInfo[] }) {
  const { data: video, error, refresh } = usePoll(() => api.video(id), 2000, [id])
  const { data: production, refresh: refreshProduction } = usePoll(() => api.production(id), 3000, [id])
  const [actionError, setActionError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab | null>(null)
  const [screen, setScreen] = useState<'master' | 'animatic' | null>(null)

  if (error && !video) return <p className="error">{error}</p>
  if (!video) return <p className="muted">Loading…</p>

  const modes: Modes = JSON.parse(video.modes_json || '{"default":"AUTO","stages":{}}')
  const modeOf = (stage: string): Mode => modes.stages?.[stage] ?? modes.default
  const latest = latestRuns(video.stageRuns)
  const finished = video.status === 'COMPLETED' || video.status === 'CANCELLED'
  const vertical = (video.plan_json ?? '').includes('"9:16"')
  const p = production
  const current = tab ?? (p && p.shots.length > 0 ? 'storyboard' : 'stages')
  const waitingStage = stages.find((s) => ['WAITING_APPROVAL', 'FAILED'].includes(latest[s.id]?.status ?? ''))
  const reviewCount = (video.inspections ?? []).filter((x) => x.verdict !== 'PASS').length + (video.creativeReviews?.length ?? 0)

  const act = async (fn: () => Promise<unknown>) => {
    setActionError(null)
    try {
      await fn()
      await refresh()
      await refreshProduction()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    }
  }
  const stageRow = (s: StageInfo, i: number) => (
    <StageRow key={s.id} index={i + 1} stage={s} run={latest[s.id]} mode={modeOf(s.id)} stages={stages}
      inspection={(video.inspections ?? []).find((x) => x.stage === s.id && x.attempt === latest[s.id]?.attempt)}
      canRedo={finished && s.redoWith.length > 0 && latest[s.id] !== undefined && latest[s.id].status !== 'SKIPPED'}
      onRedo={() => act(() => api.rerun(id, s.id))}
      attempts={video.stageRuns.filter((r) => r.stage === s.id && r.attempt > 0).length}
      locked={finished || (latest[s.id] !== undefined && FINISHED.has(latest[s.id].status))}
      onMode={(m) => act(() => api.setMode(id, s.id, m))}
      onApprove={(notes) => act(() => api.approve(id, s.id, notes))}
      onReject={(notes) => act(() => api.reject(id, s.id, notes))} />
  )

  const masterId = video.master_asset_id
  const animaticId = p?.animaticAssetId ?? null
  const show = screen === 'animatic' && animaticId ? 'animatic' : masterId ? 'master' : animaticId ? 'animatic' : null
  const firstImage = p?.shots.find((s) => s.keyframe_asset_id)?.keyframe_asset_id

  return (
    <>
      <section className="workspace-head">
        <div className="row">
          <a href="#/" className="small">← Studio</a>
          <span className="grow" />
          <StatusBadge status={video.status} />
          {!finished && (video.status === 'PAUSED'
            ? <button onClick={() => act(() => api.resume(id))}>Resume</button>
            : <button onClick={() => act(() => api.pause(id))}>Pause</button>)}
        </div>
        <h1>{video.title ?? 'Untitled video'}</h1>
        <p className="muted" style={{ margin: 0 }}>“{video.prompt}”</p>
        {actionError && <p className="error">{actionError}</p>}
      </section>

      <div className="stage-hero">
        <div style={{ display: 'grid', gap: 8 }}>
          <div className={`screen ${vertical ? 'vertical' : ''}`}>
            {show === 'master' && masterId && <video key={masterId} src={assetUrl(masterId)} controls preload="metadata" />}
            {show === 'animatic' && animaticId && <video key={animaticId} src={assetUrl(animaticId)} controls preload="metadata" />}
            {!show && (firstImage ? <img src={assetUrl(firstImage)} alt="" />
              : <div className="empty-screen">The first pictures appear here once the shot plan is made.</div>)}
          </div>
          <div className="row">
            {masterId && animaticId && (
              <div className="seg" role="group" aria-label="What to watch">
                <button className={show === 'master' ? 'on' : ''} onClick={() => setScreen('master')}>Master</button>
                <button className={show === 'animatic' ? 'on' : ''} onClick={() => setScreen('animatic')}>Animatic</button>
              </div>
            )}
            <span className="grow" />
            {masterId && <a className="button" href={assetUrl(masterId)} download="master.mp4">Download MP4</a>}
            {video.master_package_asset_id && (
              <a className="button" href={assetUrl(video.master_package_asset_id)} download="premiere_package.zip"
                title="Unzip, then in Premiere Pro: File > Import > project.xml (keep the media folder next to it)">Premiere</a>
            )}
            {video.master_captions_asset_id && (
              <a className="button" href={assetUrl(video.master_captions_asset_id)} download="captions.srt">SRT</a>
            )}
          </div>
        </div>
        <div style={{ display: 'grid', gap: 12 }}>
          {waitingStage ? (
            <div className="waiting-banner">
              <strong>Waiting for you: {waitingStage.label}</strong>
              <div className="stages">{stageRow(waitingStage, stages.indexOf(waitingStage))}</div>
            </div>
          ) : (
            <div className="card">
              <div className="small muted">{finished ? 'Done' : video.current_stage ? 'Working on' : 'Status'}</div>
              <div style={{ fontWeight: 700 }}>
                {finished ? (masterId ? 'Master ready' : 'All stages finished')
                  : stages.find((s) => s.id === video.current_stage)?.label ?? video.status}
              </div>
              {video.current_stage && latest[video.current_stage]?.summary && (
                <div className="small muted clamp2">{latest[video.current_stage].summary}</div>
              )}
            </div>
          )}
          <div className="card small" style={{ display: 'grid', gap: 6 }}>
            <div className="row"><span className="muted">Spent</span><span className="grow" /><strong>₹{Number(video.spent_inr).toFixed(2)}</strong></div>
            {p && <div className="row"><span className="muted">Shots</span><span className="grow" /><strong>{p.shots.length}</strong></div>}
            <div className="row"><span className="muted">Started</span><span className="grow" />{fmtTime(video.created_at)}</div>
            {!finished && (
              <div className="row">
                <span className="muted">All remaining stages</span><span className="grow" />
                <ModeSwitch value={modes.default} onChange={(m) => act(() => api.setAllModes(id, m))} />
              </div>
            )}
          </div>
        </div>
      </div>

      <nav className="pipeline" aria-label="Pipeline">
        {stages.map((s) => {
          const r = latest[s.id]
          return (
            <button key={s.id} className={`pipe ${pipeClass(r?.status)}`} title={r?.summary ?? s.description}
              onClick={() => { setTab('stages'); window.setTimeout(() => document.getElementById(`stage-${s.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50) }}>
              <span className="name">{s.label}</span>
              <span>{r ? (STATUS_TEXT[r.status] ?? r.status) : 'Pending'}</span>
            </button>
          )
        })}
      </nav>

      <div className="tabs" role="tablist">
        {p && p.shots.length > 0 && <button className={current === 'storyboard' ? 'on' : ''} onClick={() => setTab('storyboard')}>Storyboard<span className="count">{p.shots.length}</span></button>}
        {p && (p.script || p.research) && <button className={current === 'story' ? 'on' : ''} onClick={() => setTab('story')}>Story</button>}
        <button className={current === 'reviews' ? 'on' : ''} onClick={() => setTab('reviews')}>Reviews{reviewCount > 0 && <span className="count">{reviewCount}</span>}</button>
        <button className={current === 'stages' ? 'on' : ''} onClick={() => setTab('stages')}>Stages</button>
        <button className={current === 'plan' ? 'on' : ''} onClick={() => setTab('plan')}>Plan &amp; costs</button>
      </div>

      {current === 'storyboard' && p && <Storyboard id={id} p={p} vertical={vertical} onChange={() => { void refreshProduction(); void refresh() }} />}
      {current === 'story' && p && (
        <>
          {p.animaticAssetId && <AnimaticCard id={id} p={p} onChange={refreshProduction} />}
          {(p.concept || p.script || p.music) && <StoryCard id={id} p={p} onChange={refreshProduction} />}
          {p.research && <ResearchCard facts={JSON.parse(p.research.facts) as Fact[]} queries={p.research.queries} />}
        </>
      )}
      {current === 'reviews' && (
        <>
          <CreativeCard reports={video.creativeReviews ?? []}
            canReview={finished && video.stageRuns.some((r) => r.stage === 'KEYFRAMES' && r.status !== 'SKIPPED')}
            onReview={() => act(() => api.reviewImages(id))} />
          <section className="card">
            <div className="card-head"><h2>Inspector</h2><span className="pill">independent checks of every stage</span></div>
            {stages.filter((s) => (video.inspections ?? []).some((x) => x.stage === s.id)).map((s) => {
              const report = (video.inspections ?? []).filter((x) => x.stage === s.id).sort((a, b) => b.attempt - a.attempt)[0]
              return (
                <div key={s.id} style={{ marginBottom: 10 }}>
                  <strong className="small">{s.label}</strong> <span className="muted small">attempt {report.attempt}</span>
                  <InspectorPanel report={report} />
                </div>
              )
            })}
            {!(video.inspections ?? []).length && <p className="muted small">No inspections yet.</p>}
          </section>
        </>
      )}
      {current === 'stages' && (
        <section className="card">
          <div className="card-head">
            <h2>Stages</h2>
            {!finished && (
              <label className="row small muted">
                Review window
                <input type="number" min={1} max={1440} defaultValue={video.review_minutes} style={{ width: 70 }}
                  onBlur={(e) => { const m = Number(e.target.value); if (m > 0 && m !== video.review_minutes) void act(() => api.setReviewMinutes(id, m)) }} /> min
              </label>
            )}
          </div>
          <div className="stages">{stages.map((s, i) => <div key={s.id} id={`stage-${s.id}`}>{stageRow(s, i)}</div>)}</div>
        </section>
      )}
      {current === 'plan' && (
        <>
          <PlanCard video={video} />
          <TimeCostCard video={video} stages={stages} />
          <CostsCard video={video} />
          <ProducerCard videoId={id} />
          <RoutingDecisionsCard videoId={id} />
        </>
      )}
    </>
  )
}

export function latestRuns(runs: StageRun[]): Record<string, StageRun> {
  const out: Record<string, StageRun> = {}
  for (const r of runs) {
    const cur = out[r.stage]
    if (!cur || r.attempt >= cur.attempt) out[r.stage] = r
  }
  return out
}

export function StageRow(props: {
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
                const clips = stage.id === 'DRAFT_GEN' || stage.id === 'FINAL_GEN'
                const cost = clips
                  ? 'Nothing runs yet: the stage first shows the GPU estimate and waits for your Approve.'
                  : 'This may use paid Gemini credits.'
                if (window.confirm(`Redo ${names}? Everything else is kept. ${cost}`)) props.onRedo()
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
export function InspectorPanel({ report }: { report: Inspection }) {
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

export function PlanCard({ video }: { video: Detail }) {
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

/** Cost-ledger notes start with a tag; these tags belong to a differently named stage. */
const COST_STAGE: Record<string, string> = {
  DRAFT: 'DRAFT_GEN', Wan: 'DRAFT_GEN', FINAL: 'FINAL_GEN', KEYFRAME: 'KEYFRAMES', SHARPEN: 'UPSCALE', SMOOTH: 'UPSCALE',
  CREATIVE_REVIEW: 'SHOT_PLAN', 'VOICE': 'NARRATION',
}
const WASTED = new Set(['FAILED', 'INSPECTION_FAILED', 'REJECTED'])

/**
 * Where a video's time and money went, per stage: time spent (incl. waiting for your approval), time lost on failed
 * or redone attempts, and cost. The three slowest stages are highlighted (efficiency pass, 18 Nov 2026).
 */
export function TimeCostCard({ video, stages }: { video: Detail; stages: StageInfo[] }) {
  const rows = new Map<string, { secs: number; wasted: number; attempts: number; inr: number }>()
  const row = (s: string) => rows.get(s) ?? rows.set(s, { secs: 0, wasted: 0, attempts: 0, inr: 0 }).get(s)!
  for (const r of video.stageRuns) {
    if (!r.finished_at) continue
    const secs = Math.max(0, (Date.parse(r.finished_at) - Date.parse(r.started_at)) / 1000)
    const x = row(r.stage)
    x.secs += secs
    x.attempts += 1
    if (WASTED.has(r.status)) x.wasted += secs
  }
  for (const c of video.costs) {
    const tag = (c.note ?? '').split(/[ (]/)[0]
    const stage = COST_STAGE[tag] ?? tag
    if (stages.some((s) => s.id === stage)) row(stage).inr += Number(c.amount_inr)
  }
  const used = stages.filter((s) => (rows.get(s.id)?.secs ?? 0) >= 1 || (rows.get(s.id)?.inr ?? 0) > 0)
  const total = [...rows.values()].reduce((a, r) => ({ secs: a.secs + r.secs, wasted: a.wasted + r.wasted, inr: a.inr + r.inr }),
    { secs: 0, wasted: 0, inr: 0 })
  const slowest = new Set([...used].sort((a, b) => rows.get(b.id)!.secs - rows.get(a.id)!.secs).slice(0, 3).map((s) => s.id))
  const mins = (s: number) => (s < 60 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`)
  if (used.length === 0) return null
  return (
    <section className="card">
      <div className="card-head">
        <h2>Time &amp; cost by stage</h2>
        <span className="pill">{mins(total.secs)} · ₹{total.inr.toFixed(2)}</span>
      </div>
      <p className="muted small">
        Stage time includes waiting for your approval. Lost to failed or redone attempts: <b>{mins(total.wasted)}</b>
        {total.secs > 0 ? ` (${Math.round((100 * total.wasted) / total.secs)}%)` : ''}. Slowest stages are marked ★.
      </p>
      <table>
        <thead><tr><th>Stage</th><th>Time</th><th>Lost</th><th className="hide-sm">Attempts</th><th>₹</th></tr></thead>
        <tbody>
          {used.map((s) => {
            const r = rows.get(s.id)!
            return (
              <tr key={s.id}>
                <td>{slowest.has(s.id) ? '★ ' : ''}{s.label}</td>
                <td>{mins(r.secs)}</td>
                <td className={r.wasted > 0 ? 'error' : 'muted'}>{r.wasted > 0 ? mins(r.wasted) : '—'}</td>
                <td className="hide-sm">{r.attempts}</td>
                <td>{r.inr > 0 ? r.inr.toFixed(2) : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

export function CostsCard({ video }: { video: Detail }) {
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
