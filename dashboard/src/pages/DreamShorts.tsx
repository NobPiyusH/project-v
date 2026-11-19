import { useState } from 'react'
import { assetUrl } from '../api'
import { DREAM_STEPS, dreamApi, type DreamDetail, type DreamMode, type DreamStep, type DreamSummary } from '../dreamApi'
import { usePoll } from '../hooks'

type EditKey = 'musicEdit' | 'ambience' | 'pacing'
const EDIT_SWITCHES: [EditKey, string][] = [
  ['musicEdit', 'Cut to the music (bars, rise, energy)'],
  ['ambience', 'J-cut ambience'],
  ['pacing', 'Pacing (hook first, no shot under 3.4 s, climax mid-way)'],
]
const EDIT_COLUMN: Record<EditKey, 'edit_music' | 'edit_ambience' | 'edit_pacing'> = {
  musicEdit: 'edit_music', ambience: 'edit_ambience', pacing: 'edit_pacing',
}
const STEP_LABEL: Record<DreamStep, string> = { SCOUT: 'Scout', WRITE: 'Write', PICTURES: 'Pictures', CLIPS: 'Clips', MUSIC: 'Music', FINISH: 'Finish' }

function statusBadge(status: string) {
  const cls = status === 'COMPLETED' ? 'b-ok' : status.startsWith('WAITING') ? 'b-warn'
    : status === 'FAILED_WAITING' ? 'b-bad' : status === 'CANCELLED' ? 'b-idle' : 'b-info'
  const text: Record<string, string> = {
    RUNNING: 'Working', WAITING: 'Waiting for you', WAITING_REVIEW: 'In review', WAITING_COST: 'Cost to approve',
    FAILED_WAITING: 'Failed: waiting', COMPLETED: 'Done', CANCELLED: 'Cancelled',
  }
  return <span className={`badge ${cls}`}>{text[status] ?? status}</span>
}

/** Dream Shorts (docs/plans/dream-shorts.md): list + new-Short form, or one Short's steps. */
export function DreamShorts({ id }: { id?: string }) {
  return id ? <DreamView id={id} /> : <DreamHome />
}

function DreamHome() {
  const { data: shorts, error, refresh } = usePoll(dreamApi.list, 5000)
  const [idea, setIdea] = useState('')
  const [look, setLook] = useState('lonely')
  const [shots, setShots] = useState(5)
  const [quality, setQuality] = useState('480p')
  const [mode, setMode] = useState<DreamMode>('REVIEW')
  const [review, setReview] = useState(10)
  const [music, setMusic] = useState<'bed' | 'lyria'>('bed')
  const [scout, setScout] = useState(true)
  const [ed, setEd] = useState({ musicEdit: true, ambience: true, pacing: true })
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  async function start() {
    if (!idea.trim()) {
      setFormError('Type an idea first')
      return
    }
    setBusy(true)
    try {
      const r = await dreamApi.create({ idea, look, shots, quality, defaultMode: mode, reviewMinutes: review, music, scout, ...ed })
      window.location.hash = `#/dream/${r.id}`
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
      void refresh()
    }
  }

  return (
    <>
      <section className="card">
        <div className="card-head"><h2>New Dream Short</h2><span className="pill">Modal Wan clips · your music · laptop finish</span></div>
        <textarea rows={2} value={idea} placeholder="A pink house under a cloud at the end of a tiled pier"
          onChange={(e) => { setIdea(e.target.value); setFormError(null) }} style={{ width: '100%' }} />
        {formError && <p className="error small">{formError}</p>}
        <div className="row" style={{ marginTop: 10 }}>
          <label className="small">Look <select value={look} onChange={(e) => setLook(e.target.value)}>
            <option value="lonely">Lonely object</option><option value="drive">Drive-by</option><option value="walkthrough">Walk-through</option><option value="dreamcore">Dreamcore (soft pastel)</option><option value="impossible">Impossible place</option></select></label>
          <label className="small">Shots <select value={shots} onChange={(e) => setShots(Number(e.target.value))}>
            {[3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          {EDIT_SWITCHES.map(([k, label]) => <label key={k} className="small"><input type="checkbox" checked={ed[k]}
            onChange={(e) => setEd({ ...ed, [k]: e.target.checked })} /> {label}</label>)}
          <label className="small"><input type="checkbox" checked={scout} onChange={(e) => setScout(e.target.checked)} /> Scout similar Shorts first (≈₹6)</label>
          <label className="small">Music <select value={music} onChange={(e) => setMusic(e.target.value as 'bed' | 'lyria')}>
            <option value="bed">Your track (free)</option><option value="lyria">Lyria (≈₹10)</option></select></label>
          <label className="small">Clips <select value={quality} onChange={(e) => setQuality(e.target.value)}>
            <option value="480p">480p (cheapest)</option><option value="720p">720p</option></select></label>
          <label className="small">Mode <select value={mode} onChange={(e) => setMode(e.target.value as DreamMode)}>
            <option value="AUTO">Auto</option><option value="REVIEW">Review</option><option value="MANUAL">Manual</option></select></label>
          {mode === 'REVIEW' && <label className="small">Review window (min) <select value={review}
            onChange={(e) => setReview(Number(e.target.value))}>{[5, 10, 20, 30, 60].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>}
          <span style={{ flex: 1 }} />
          <button className="primary" disabled={busy} onClick={() => void start()}>Start</button>
        </div>
      </section>
      <section className="card">
        <div className="card-head"><h2>Your Dream Shorts</h2></div>
        {error && <p className="error">{error}</p>}
        {shorts && shorts.length === 0 && <p className="muted">Start your first Dream Short above.</p>}
        {shorts && shorts.length > 0 && (
          <table>
            <thead><tr><th></th><th>Short</th><th>Status</th><th className="hide-sm">Step</th><th>Spent</th></tr></thead>
            <tbody>{shorts.map((d: DreamSummary) => (
              <tr key={d.id} style={{ cursor: 'pointer' }} onClick={() => { window.location.hash = `#/dream/${d.id}` }}>
                <td style={{ width: 40 }}>{d.thumb_asset_id && <img src={assetUrl(d.thumb_asset_id)} alt="" style={{ width: 32, borderRadius: 4 }} />}</td>
                <td><strong>{d.title ?? d.idea}</strong>{d.title && <div className="muted small">{d.idea}</div>}</td>
                <td>{statusBadge(d.status)}</td>
                <td className="hide-sm">{d.step ? STEP_LABEL[d.step as DreamStep] ?? d.step : ''}</td>
                <td>₹{Number(d.spent_inr).toFixed(0)}</td>
              </tr>))}
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}

function DreamView({ id }: { id: string }) {
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
      setActionError(e instanceof Error ? e.message : String(e))
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
  const progress = new Map((d.clipProgress ?? []).map((p) => [p.shotId, p.state]))
  const spent = d.costs.reduce((s, c) => s + Number(c.amount_inr), 0)

  return (
    <>
      <section className="card">
        <div className="card-head">
          <div><a href="#/dream" className="small">← Dream Shorts</a><h2 style={{ margin: '4px 0' }}>{d.title ?? d.idea}</h2>
            <div className="muted small">{d.idea} · {d.look} · {d.quality} · ₹{spent.toFixed(1)} recorded (estimated GPU)</div></div>
          {statusBadge(status)}
        </div>
        <div className="row" style={{ marginBottom: 10 }}>
          {DREAM_STEPS.map((s) => (
            <span key={s} className={`pill ${s === step ? 'b-info' : ''}`}>
              {STEP_LABEL[s]}{' '}
              <select value={modes?.[s] ?? 'AUTO'} disabled={!live}
                onChange={(e) => void act(() => dreamApi.setMode(id, s, e.target.value as DreamMode))}>
                <option value="AUTO">Auto</option><option value="REVIEW">Review</option><option value="MANUAL">Manual</option>
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

      <section className="card">
        <div className="card-head"><h2>Shots</h2>{waiting && step !== 'WRITE' && <span className="muted small">Tick shots to redo only those</span>}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))', gap: 12 }}>
          {d.shots.map((s) => (
            <div key={s.id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
              <div className="row small" style={{ justifyContent: 'space-between' }}>
                <strong>#{s.ord + 1}</strong>
                {waiting && step !== 'WRITE' && <input type="checkbox" checked={picked.includes(s.id)} aria-label={`Pick shot ${s.ord + 1}`}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, s.id] : picked.filter((x) => x !== s.id))} />}
              </div>
              {s.clip_asset_id
                ? <video src={assetUrl(s.clip_asset_id)} poster={s.picture_asset_id ? assetUrl(s.picture_asset_id) : undefined}
                    muted loop playsInline controls preload="metadata" style={{ width: '100%', borderRadius: 6 }} />
                : s.picture_asset_id
                  ? <img src={assetUrl(s.picture_asset_id)} alt={s.scene} style={{ width: '100%', borderRadius: 6 }} />
                  : <div className="muted small" style={{ aspectRatio: '9/16', display: 'grid', placeItems: 'center', background: 'var(--surface-2)', borderRadius: 6 }}>No picture yet</div>}
              {progress.get(s.id) && progress.get(s.id) !== 'done' && <span className="badge b-info">{progress.get(s.id)}</span>}
              <p className="small" style={{ margin: '6px 0 0' }}>{s.scene}</p>
              {s.error && <p className="error small">{s.error}</p>}
            </div>))}
        </div>
      </section>

      <Check d={d} />

      <Scout d={d} onPick={(n) => void act(() => dreamApi.pickAngle(id, n))} />

      <Evidence d={d} />

      <section className="card">
        <div className="card-head"><h2>Why these providers, and what it cost</h2></div>
        {d.decisions.length === 0 && <p className="muted small">No routing decisions yet.</p>}
        {d.decisions.map((r, i) => <div key={i} className="small" style={{ marginBottom: 6 }}>
          <strong>{r.stage}</strong> → <code>{r.chosen_provider_id}</code> <span className="muted">{r.reason_text}</span></div>)}
        {d.costs.length > 0 && <table style={{ marginTop: 10 }}>
          <thead><tr><th>Provider</th><th>What</th><th>₹</th></tr></thead>
          <tbody>{d.costs.map((c, i) => <tr key={i}><td><code>{c.provider_id}</code></td><td className="small">{c.note}</td>
            <td>{Number(c.amount_inr).toFixed(2)}{c.estimated ? ' (est.)' : ''}</td></tr>)}</tbody>
        </table>}
      </section>
    </>
  )
}

// The owner's example Shorts, measured the same way (19 Nov 2026): what "good" looks like for the learning loop.
const EXAMPLES: Record<string, string> = {
  motion: '1.2–1.5 (lonely object), 3.2 (walk-through)', colour: '26–27 (lonely object), 15 (walk-through)',
  shadows: '—', loudnessLufs: '−14', cuts: '3–4 per 15–20 s', climaxAtPct: '38–68', shortestShotSec: '≥ 3.4',
}
const METRIC_LABEL: Record<string, string> = {
  motion: 'Camera / scene motion', colour: 'Colour strength', shadows: 'Darkest areas (higher = more detail)',
  cuts: 'Cuts', cutsOnBeat: 'Cuts on the beat', loudnessLufs: 'Loudness (LUFS)', durationSec: 'Length (s)',
  shots: 'Shots', speed: 'Playback speed', hookShot: 'Opening (hook) shot', climaxAtPct: 'Climax at (% of length)',
  musicRiseSec: 'Music rise at (s)', shortestShotSec: 'Shortest shot (s)', ambiences: 'Ambience beds',
}

function parse(s: string | null): Record<string, unknown> {
  try { return s ? JSON.parse(s) : {} } catch { return {} }
}

/** Learning loop, phase 1: what this Short measured, what each paid step cost against its estimate, every decision. */
function Evidence({ d }: { d: DreamDetail }) {
  const latest = d.metrics[0] ? parse(d.metrics[0].metrics) as Record<string, number> : null
  const costs = d.events.filter((e) => e.kind === 'cost')
  const decisions = d.events.filter((e) => e.kind !== 'cost')
  return (
    <section className="card">
      <div className="card-head"><h2>Evidence</h2><span className="muted small">what the studio learns from</span></div>
      {latest && <table className="small" style={{ width: '100%', marginBottom: 12 }}>
        <thead><tr><th align="left">Measured</th><th align="left">This Short</th><th align="left">Your examples</th></tr></thead>
        <tbody>{Object.keys(METRIC_LABEL).filter((k) => latest[k] !== undefined).map((k) => (
          <tr key={k}><td>{METRIC_LABEL[k]}</td><td>{latest[k]}</td><td className="muted">{EXAMPLES[k] ?? ''}</td></tr>))}</tbody>
      </table>}
      {costs.length > 0 && <div className="small" style={{ marginBottom: 12 }}>
        {costs.map((e, i) => { const x = parse(e.data) as Record<string, number>; return (
          <div key={i}>{e.step}: estimated ₹{x.estimateInr} → actually ₹{x.actualInr}
            {x.ratio > 1.2 || (x.ratio > 0 && x.ratio < 0.8) ? <span className="error"> (off by {x.ratio}×)</span> : null}</div>) })}
      </div>}
      {decisions.length === 0 && !latest && <p className="muted small">Nothing recorded yet.</p>}
      {decisions.map((e, i) => (
        <div key={i} className="small muted">{new Date(e.created_at).toLocaleString()} · {e.kind}{e.step ? ` ${e.step}` : ''}
          {e.notes ? ` · “${e.notes}”` : ''}{e.data && e.kind === 'mode' ? ` · ${String(parse(e.data).mode)}` : ''}</div>))}
    </section>
  )
}

interface ScoutData {
  source: string
  videos: { url: string; title: string | null; channel: string | null; views: number | null; shows: string; look: string;
    camera: string; shots: number; music: string; hook: string; tools: string; works: string }[]
  brief: { cliches: string[]; gaps: string[]; angles: { title: string; idea: string; why: string }[] }
}

/** What the Scout watched, what to avoid, and the angle the writer will follow (pick before approving the Scout). */
function Scout({ d, onPick }: { d: DreamDetail; onPick: (n: number) => void }) {
  if (!d.scout_json) return null
  const s = parse(d.scout_json) as unknown as ScoutData
  if (!s.brief) return null
  return (
    <section className="card">
      <div className="card-head"><h2>Scout</h2><span className="muted small">{s.videos.length} similar Shorts watched ({s.source})</span></div>
      <h3 className="small">Ways to be different: the writer follows the one you pick</h3>
      {s.brief.angles.map((a, i) => (
        <label key={i} className="small" style={{ display: 'block', marginBottom: 8 }}>
          <input type="radio" name="angle" checked={d.scout_angle === i + 1} onChange={() => onPick(i + 1)} />{' '}
          <strong>{a.title}</strong>: {a.idea} <span className="muted">({a.why})</span>
        </label>))}
      <p className="small"><strong>Most of them do:</strong> {s.brief.cliches.join('; ')}</p>
      <p className="small"><strong>None of them do:</strong> {s.brief.gaps.join('; ')}</p>
      <details className="small"><summary>What the Scout watched</summary>
        {s.videos.map((v, i) => (
          <div key={i} style={{ marginTop: 8 }}>
            <a href={v.url} target="_blank" rel="noreferrer">{v.title ?? v.url}</a>
            {v.views != null && <span className="muted"> · {v.views.toLocaleString()} views</span>}
            <div>{v.shows}</div>
            <div className="muted">Look: {v.look} · Camera: {v.camera} · {v.shots} shots · Music: {v.music} · Hook: {v.hook} · Made with: {v.tools}</div>
          </div>))}
      </details>
    </section>
  )
}

const CHECK_LABEL: Record<string, string> = {
  noPeople: 'No people', cleanText: 'Clean text, no brands', looksReal: 'Looks real (no warping)', continuity: 'Continuity',
  humanCamera: 'Human camera', musicFit: 'Music fit', story: 'Story and ending', feelGood: 'Feels good',
}

/** Learning loop, phase 3: Gemini's check of the finished Short against the owner's checklist. */
function Check({ d }: { d: DreamDetail }) {
  if (!d.check_json) return null
  const c = parse(d.check_json) as { overall: number; summary: string; scores: Record<string, number>;
    issues: { shot: number; atSec: number; problem: string; fix: string }[] }
  return (
    <section className="card">
      <div className="card-head"><h2>Quality check</h2><span className="pill">{c.overall}/10</span></div>
      <p className="small">{c.summary}</p>
      {c.issues.length > 0 && <ul className="small">{c.issues.map((i, k) => (
        <li key={k}><strong>{i.shot > 0 ? `Shot ${i.shot}` : 'Whole Short'}{i.atSec ? ` at ${i.atSec.toFixed(1)} s` : ''}:</strong>{' '}
          {i.problem} <span className="muted">Fix: {i.fix}</span></li>))}</ul>}
      <div className="small muted">{Object.entries(c.scores).map(([k, v]) => `${CHECK_LABEL[k] ?? k} ${v}`).join(' · ')}</div>
    </section>
  )
}
