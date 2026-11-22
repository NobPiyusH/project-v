import { useState } from 'react'
import { assetUrl } from '../../api'
import { STEP_LABEL, DREAM_STEPS, dreamApi, type DreamMode, type DreamStep } from '../../dreamApi'
import { usePoll } from '../../hooks'
import { Evidence } from './Evidence'
import { GpuCard } from './GpuCard'
import { HookCard } from './HookCard'
import { PathCard } from './PathCard'
import { QualityCheck } from './QualityCheck'
import { RoutingAndCosts } from './RoutingAndCosts'
import { ScoutCard } from './ScoutCard'
import { ShotReview } from './ShotReview'
import { ShotsGrid } from './ShotsGrid'
import { StepTimeline } from './StepTimeline'
import { DreamStatusBadge } from './DreamStatusBadge'
import { EDIT_COLUMN, EDIT_SWITCHES, errorText } from './shared'

/** One Dream Short: step modes, the owner's actions for the current step, and every card below. */
export function DreamView({ id }: { id: string }) {
  const { data: d, error, refresh } = usePoll(() => dreamApi.get(id), 4000, [id])
  const [picked, setPicked] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [from, setFrom] = useState<DreamStep>('MUSIC')
  const [actionError, setActionError] = useState<string | null>(null)

  async function act(fn: () => Promise<void>) {
    setActionError(null)
    try {
      await fn()
      setNotes('')
      setPicked([])
    } catch (e) {
      setActionError(errorText(e))
    }
    void refresh()
  }

  if (error && !d) return <p className="error">{error}</p>
  if (!d) return <p className="muted">Loading…</p>
  const status = d.view?.status ?? d.status
  const step = (d.view?.step ?? d.step) as DreamStep | null
  const modes = d.view?.modes ?? (typeof d.modes_json === 'string' ? JSON.parse(d.modes_json) : d.modes_json) as Record<DreamStep, DreamMode>
  const waiting = status === 'WAITING' || status === 'WAITING_REVIEW'
  const live = status !== 'COMPLETED' && status !== 'CANCELLED'
  const spent = d.costs.reduce((s, c) => s + Number(c.amount_inr), 0)

  return (
    <>
      <section className="card">
        <div className="card-head">
          <div><a href="#/dream" className="small">← Dream Shorts</a><h2 style={{ margin: '4px 0' }}>{d.title ?? d.idea}</h2>
            <div className="muted small">{d.idea} · {d.look} · {d.quality} · ₹{spent.toFixed(1)} recorded (estimated GPU)</div></div>
          <DreamStatusBadge status={status} />
        </div>
        <div className="row" style={{ marginBottom: 10 }}>
          {DREAM_STEPS.map((s) => (
            <span key={s} className={`pill ${s === step ? 'b-info' : ''}`}>
              {STEP_LABEL[s]}{' '}
              <select value={modes?.[s] ?? 'AUTO'} disabled={!live}
                onChange={(e) => void act(() => dreamApi.setMode(id, s, e.target.value as DreamMode))}>
                <option value="AUTO">Auto</option><option value="REVIEW">Review</option><option value="MANUAL">Manual</option>
                <option value="PRODUCER">AI Producer</option>
              </select>
            </span>))}
        </div>
        {d.message && <p className={status === 'FAILED_WAITING' ? 'error' : ''}>{d.view?.message ?? d.message}</p>}
        {status === 'WAITING_REVIEW' && d.view?.deadline && <p className="muted small">Goes on by itself at {new Date(d.view.deadline).toLocaleTimeString()} unless you decide first.</p>}
        {actionError && <p className="error small">{actionError}</p>}
        <div className="row">
          {waiting && step && <>
            <button className="primary" onClick={() => void act(() => dreamApi.approve(id, step))}>Approve {STEP_LABEL[step].toLowerCase()}</button>
            <input type="text" value={notes} placeholder={step === 'WRITE' ? 'More mushrooms, softer light' : 'Notes for the redo'}
              onChange={(e) => setNotes(e.target.value)} style={{ flex: 1, minWidth: 180 }} />
            {step === 'MUSIC' && <button onClick={() => void act(() => dreamApi.redo(id, step, [], notes))}>New music {d.music_source === 'lyria' ? '(≈₹10)' : '(another piece, free)'}</button>}
            {step !== 'FINISH' && step !== 'MUSIC' && <button onClick={() => void act(() => dreamApi.redo(id, step, picked, notes))}>
              Redo {step === 'WRITE' ? 'all' : picked.length ? `${picked.length} shot${picked.length > 1 ? 's' : ''}` : 'all'}</button>}
            {step === 'FINISH' && <button onClick={() => void act(() => dreamApi.redo(id, step, [], notes))}>Finish again</button>}
          </>}
          {status === 'WAITING_COST' && <button className="primary" onClick={() => void act(() => dreamApi.approve(id, 'CLIPS'))}>Approve cost and start clips</button>}
          {status === 'WAITING_BATCH' && step && <button onClick={() => void act(() => dreamApi.approve(id, step))}>Start now</button>}
          {status === 'FAILED_WAITING' && <button className="primary" onClick={() => void act(() => dreamApi.retry(id))}>Retry</button>}
          {status === 'RUNNING' && step === 'CLIPS' && <button className="danger" onClick={() => void act(() => dreamApi.stop(id))}>Stop clips</button>}
          <span style={{ flex: 1 }} />
          {!live && <>
            <label className="small">Redo from <select value={from} onChange={(e) => setFrom(e.target.value as DreamStep)}>
              {DREAM_STEPS.map((s) => <option key={s} value={s}>{STEP_LABEL[s]}</option>)}</select></label>
            <button onClick={() => { if (window.confirm(`Redo this Short from ${STEP_LABEL[from]}? That step and the ones after it run again; paid steps ask before spending.`)) void act(() => dreamApi.rerun(id, from)) }}>Redo</button>
          </>}
          {live && <button className="danger" onClick={() => { if (window.confirm('Cancel this Dream Short?')) void act(() => dreamApi.cancel(id)) }}>Cancel Short</button>}
        </div>
      </section>

      <GpuCard id={id} />

      {d.hook_title && <HookCard d={d} onSave={(text, color) => act(() => dreamApi.setHook(id, text, color))} />}

      {d.path_choice && <PathCard d={d} onSave={(a, b) => act(() => dreamApi.setPaths(id, a, b))}
        onContinue={(p) => act(async () => { const r = await dreamApi.continueDream(id, p); window.location.hash = `#/dream/${r.id}` })} />}

      <StepTimeline d={d} />

      {d.final_asset_id && (
        <section className="card">
          <div className="card-head"><h2>Finished Short</h2>
            <a className="button" href={assetUrl(d.final_asset_id)} download>Download MP4</a></div>
          <div className="small" style={{ marginBottom: 8 }}>Editing (applies at the next finish; Redo from Finish is free):{' '}
            {EDIT_SWITCHES.map(([k, label]) => <label key={k} style={{ marginRight: 12 }}><input type="checkbox"
              checked={d[EDIT_COLUMN[k]]} onChange={(e) => void act(() => dreamApi.editing(id, { [k]: e.target.checked }))} /> {label}</label>)}</div>
          <video src={assetUrl(d.final_asset_id)} controls playsInline style={{ maxHeight: 640, borderRadius: 8 }} />
        </section>
      )}

      {d.music_asset_id && (
        <section className="card">
          <div className="card-head"><h2>Music</h2><span className="pill">{d.music_source === 'lyria' ? 'Lyria' : 'Your track'}</span></div>
          <audio src={assetUrl(d.music_asset_id)} controls style={{ width: '100%' }} />
        </section>
      )}

      <ShotsGrid d={d} selectable={waiting && step !== 'WRITE'} picked={picked} onPick={setPicked} />

      <QualityCheck json={d.check_json} title="Quality check" />
      <QualityCheck json={d.picture_check_json} title="Picture check (before clips)" />
      {d.final_asset_id && <section className="card"><div className="card-head"><h2>Your review of this Short</h2></div>
        <ShotReview dreamId={d.id} shotId={null} /></section>}

      <ScoutCard d={d} onPick={(n) => void act(() => dreamApi.pickAngle(id, n))} />

      <Evidence d={d} />

      <RoutingAndCosts d={d} />
    </>
  )
}
