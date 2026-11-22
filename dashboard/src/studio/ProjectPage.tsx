import { useMemo, useState } from 'react'
import { api, type Fact, type Mode, type StageInfo } from '../api'
import { autopilotApi } from '../autopilotApi'
import { StatusBadge } from '../components'
import { dreamApi, STEP_LABEL, DREAM_STEPS, type DreamStep } from '../dreamApi'
import { usePoll } from '../hooks'
import { CreativeCard } from '../pages/CreativeCard'
import { ProducerCard } from '../pages/ProducerCard'
import { AnimaticCard, ResearchCard, StoryCard } from '../pages/ProductionCards'
import { RoutingDecisionsCard } from '../pages/Routing'
import { Storyboard } from '../pages/Storyboard'
import { CostsCard, InspectorPanel, PlanCard, StageRow, TimeCostCard, latestRuns } from '../pages/VideoDetail'
import { Evidence } from '../pages/dream/Evidence'
import { GpuCard } from '../pages/dream/GpuCard'
import { HookCard } from '../pages/dream/HookCard'
import { PathCard } from '../pages/dream/PathCard'
import { QualityCheck } from '../pages/dream/QualityCheck'
import { RoutingAndCosts } from '../pages/dream/RoutingAndCosts'
import { ScoutCard } from '../pages/dream/ScoutCard'
import { ShotReview } from '../pages/dream/ShotReview'
import { ShotsGrid } from '../pages/dream/ShotsGrid'
import { StepTimeline } from '../pages/dream/StepTimeline'
import { EDIT_COLUMN, EDIT_SWITCHES, errorText } from '../pages/dream/shared'
import { ProjectView } from './ProjectView'
import { shortActions, shortProject, videoActions, videoProject, type Kind } from './projectAdapter'

export function ProjectPage({ kind, id, stages }: { kind: Kind; id: string; stages: StageInfo[] }) {
  return kind === 'short' ? <ShortPage id={id} /> : <VideoPage id={id} stages={stages} />
}

// ---------------------------------------------------------------- Short

function ShortPage({ id }: { id: string }) {
  const { data: d, error, refresh } = usePoll(() => dreamApi.get(id), 4000, [id])
  const { data: runs } = usePoll(autopilotApi.runs, 30000)
  const [picked, setPicked] = useState<string[]>([])
  const [actionError, setActionError] = useState<string | null>(null)
  const [from, setFrom] = useState<DreamStep>('MUSIC')
  const view = useMemo(() => (d ? shortProject(d, runs ?? []) : null), [d, runs])
  const actions = useMemo(() => shortActions(id), [id])
  if (error && !d) return <p className="error">{error}</p>
  if (!d || !view) return <p className="muted">Loading…</p>

  const status = d.view?.status ?? d.status
  const step = (d.view?.step ?? d.step) as DreamStep | null
  const waiting = status === 'WAITING' || status === 'WAITING_REVIEW'
  const act = async (fn: () => Promise<unknown>) => {
    setActionError(null)
    try { await fn() } catch (e) { setActionError(errorText(e)) }
    void refresh()
  }
  const changed = () => { setPicked([]); void refresh() }

  return (
    <ProjectView view={view} actions={actions} shotIds={waiting && step !== 'WRITE' ? picked : []} onChanged={changed} classicHref={`#/dream/${id}`}
      extraHead={view.live && <>
        {status === 'RUNNING' && step === 'CLIPS' && <button type="button" className="danger" onClick={() => void act(() => dreamApi.stop(id))}>Stop clips</button>}
        <button type="button" className="danger" onClick={() => { if (window.confirm('Cancel this Short?')) void act(() => dreamApi.cancel(id)) }}>Cancel</button>
      </>}
      advanced={
        <>
          {actionError && <p className="error">{actionError}</p>}
          <GpuCard id={id} />
          {d.hook_title && <HookCard d={d} onSave={(text, color) => act(() => dreamApi.setHook(id, text, color))} />}
          {d.path_choice && <PathCard d={d} onSave={(a, b) => act(() => dreamApi.setPaths(id, a, b))}
            onContinue={(p) => act(async () => { const r = await dreamApi.continueDream(id, p); window.location.hash = `#/p/short/${r.id}` })} />}
          {!view.live && (
            <section className="card row small">
              <label>Redo from <select value={from} onChange={(e) => setFrom(e.target.value as DreamStep)}>{DREAM_STEPS.map((s) => <option key={s} value={s}>{STEP_LABEL[s]}</option>)}</select></label>
              <button type="button" onClick={() => { if (window.confirm(`Redo this Short from ${STEP_LABEL[from]}? That step and the ones after it run again; paid steps ask before spending.`)) void act(() => dreamApi.rerun(id, from)) }}>Redo</button>
            </section>
          )}
          <StepTimeline d={d} />
          {d.final_asset_id && (
            <section className="card small">
              Editing (applies at the next finish; Redo from Finish is free):{' '}
              {EDIT_SWITCHES.map(([k, label]) => <label key={k} style={{ marginRight: 12 }}><input type="checkbox" checked={d[EDIT_COLUMN[k]]}
                onChange={(e) => void act(() => dreamApi.editing(id, { [k]: e.target.checked }))} /> {label}</label>)}
            </section>
          )}
          <ShotsGrid d={d} selectable={waiting && step !== 'WRITE'} picked={picked} onPick={setPicked} />
          <QualityCheck json={d.check_json} title="Quality check" />
          <QualityCheck json={d.picture_check_json} title="Picture check (before clips)" />
          {d.final_asset_id && <section className="card"><div className="card-head"><h2>Your review of this Short</h2></div><ShotReview dreamId={d.id} shotId={null} /></section>}
          <ScoutCard d={d} onPick={(n) => void act(() => dreamApi.pickAngle(id, n))} />
          <Evidence d={d} />
          <RoutingAndCosts d={d} />
        </>
      } />
  )
}

// ---------------------------------------------------------------- Video

function VideoPage({ id, stages }: { id: string; stages: StageInfo[] }) {
  const { data: video, error, refresh } = usePoll(() => api.video(id), 2000, [id])
  const { data: production, refresh: refreshProduction } = usePoll(() => api.production(id), 3000, [id])
  const { data: producer } = usePoll(() => api.producer(id), 15000, [id])
  const [actionError, setActionError] = useState<string | null>(null)
  const view = useMemo(() => (video ? videoProject(video, production, stages, producer?.decisions ?? []) : null), [video, production, stages, producer])
  const actions = useMemo(() => videoActions(id), [id])
  if (error && !video) return <p className="error">{error}</p>
  if (!video || !view) return <p className="muted">Loading…</p>

  const refreshAll = () => { void refresh(); void refreshProduction() }
  const act = async (fn: () => Promise<unknown>) => {
    setActionError(null)
    try { await fn() } catch (e) { setActionError(errorText(e)) }
    refreshAll()
  }
  const p = production
  const runs = latestRuns(video.stageRuns)
  const finished = video.status === 'COMPLETED' || video.status === 'CANCELLED'
  const FINISHED = new Set(['AUTO_PASSED', 'APPROVED', 'SKIPPED'])
  const modes = JSON.parse(video.modes_json || '{}') as { default?: Mode; stages?: Record<string, Mode> }

  return (
    <ProjectView view={view} actions={actions} onChanged={refreshAll} classicHref={`#/video/${id}`}
      extraHead={<>
        <StatusBadge status={video.status} />
        {!finished && (video.status === 'PAUSED'
          ? <button type="button" onClick={() => void act(() => api.resume(id))}>Resume</button>
          : <button type="button" onClick={() => void act(() => api.pause(id))}>Pause</button>)}
      </>}
      advanced={
        <>
          {actionError && <p className="error">{actionError}</p>}
          <section className="card">
            <div className="card-head"><h2>Stages</h2>
              {!finished && <label className="row small muted">Review window
                <input type="number" min={1} max={1440} defaultValue={video.review_minutes} style={{ width: 70 }}
                  onBlur={(e) => { const m = Number(e.target.value); if (m > 0 && m !== video.review_minutes) void act(() => api.setReviewMinutes(id, m)) }} /> min</label>}
            </div>
            <div className="stages">{stages.map((s, i) => {
              return (
                <StageRow key={s.id} index={i + 1} stage={s} run={runs[s.id]} mode={modes.stages?.[s.id] ?? modes.default ?? 'AUTO'} stages={stages}
                  inspection={(video.inspections ?? []).find((x) => x.stage === s.id && x.attempt === runs[s.id]?.attempt)}
                  canRedo={finished && s.redoWith.length > 0 && runs[s.id] !== undefined && runs[s.id].status !== 'SKIPPED'}
                  onRedo={() => void act(() => api.rerun(id, s.id))}
                  attempts={video.stageRuns.filter((r) => r.stage === s.id && r.attempt > 0).length}
                  locked={finished || (runs[s.id] !== undefined && FINISHED.has(runs[s.id].status))}
                  onMode={(mode) => void act(() => api.setMode(id, s.id, mode))}
                  onApprove={(notes) => void act(() => api.approve(id, s.id, notes))}
                  onReject={(notes) => void act(() => api.reject(id, s.id, notes))} />
              )
            })}</div>
          </section>
          {p && p.shots.length > 0 && <Storyboard id={id} p={p} vertical={view.vertical} onChange={refreshAll} />}
          {p && p.animaticAssetId && <AnimaticCard id={id} p={p} onChange={refreshProduction} />}
          {p && (p.concept || p.script || p.music) && <StoryCard id={id} p={p} onChange={refreshProduction} />}
          {p?.research && <ResearchCard facts={JSON.parse(p.research.facts) as Fact[]} queries={p.research.queries} />}
          <CreativeCard reports={video.creativeReviews ?? []} canReview={finished && video.stageRuns.some((r) => r.stage === 'KEYFRAMES' && r.status !== 'SKIPPED')}
            onReview={() => void act(() => api.reviewImages(id))} />
          <section className="card">
            <div className="card-head"><h2>Inspector</h2><span className="pill">independent checks of every stage</span></div>
            {stages.filter((s) => (video.inspections ?? []).some((x) => x.stage === s.id)).map((s) => {
              const report = (video.inspections ?? []).filter((x) => x.stage === s.id).sort((a, b) => b.attempt - a.attempt)[0]
              return <div key={s.id} style={{ marginBottom: 10 }}><strong className="small">{s.label}</strong> <span className="muted small">attempt {report.attempt}</span><InspectorPanel report={report} /></div>
            })}
            {!(video.inspections ?? []).length && <p className="muted small">No inspections yet.</p>}
          </section>
          <PlanCard video={video} />
          <TimeCostCard video={video} stages={stages} />
          <CostsCard video={video} />
          <ProducerCard videoId={id} />
          <RoutingDecisionsCard videoId={id} />
        </>
      } />
  )
}
