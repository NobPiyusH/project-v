import { useMemo, useState } from 'react'
import { api, type Mode } from '../api'
import { autopilotApi } from '../autopilotApi'
import { dreamApi, type NewDream } from '../dreamApi'
import { errorText, EDIT_SWITCHES } from '../pages/dream/shared'
import type { Kind, ProjectRow } from './projectAdapter'
import { inr } from './format'

type Channel = 'science' | 'dreamscapes'
type ClipMethod = 'off' | 'last' | 'svi'
/** New projects start in Review with a 10-minute window (owner decision, 22 Nov 2026). */
const DEFAULT_MODE: Mode = 'REVIEW'
const DEFAULT_REVIEW = 10
// A channel only prefills the Short's look; the backend has no channel field yet.
const CHANNEL_LOOK: Record<Channel, string> = { dreamscapes: 'dreamcore', science: 'lonely' }

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b)
  return s.length ? s[Math.floor(s.length / 2)] : null
}

/** One idea box for both products. "Story" stays hidden until Phase 2. */
export function Create({ rows }: { rows: ProjectRow[] }) {
  const [idea, setIdea] = useState('')
  const [kind, setKind] = useState<Kind>('short')
  const [channel, setChannel] = useState<Channel | null>(null)
  const [mode, setMode] = useState<Mode>(DEFAULT_MODE)
  const [review, setReview] = useState(DEFAULT_REVIEW)
  // Short options (defaults copied from the Dream form this replaces)
  const [look, setLook] = useState('lonely')
  const [shots, setShots] = useState(5)
  const [quality, setQuality] = useState('480p')
  const [pictureModel, setPictureModel] = useState<'flux2-klein' | 'zimage'>('flux2-klein')
  const [music, setMusic] = useState<'bed' | 'lyria'>('bed')
  const [scout, setScout] = useState(true)
  const [gpuHold, setGpuHold] = useState<'auto' | 'manual'>('auto')
  const [clip, setClip] = useState<ClipMethod>('off')
  const [sviScenes, setSviScenes] = useState(1)
  const [sviSeconds, setSviSeconds] = useState(20)
  const [ed, setEd] = useState({ musicEdit: true, ambience: true, pacing: true })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // The estimate comes from what finished projects of this product really cost, never a hard-coded price.
  const estimate = useMemo(() => {
    const done = rows.filter((r) => r.kind === kind && r.status === 'done' && r.costInr > 0).map((r) => r.costInr)
    const m = median(done)
    return m == null ? null : { inr: m, n: done.length }
  }, [rows, kind])

  const pickChannel = (c: Channel) => {
    setChannel(c)
    setLook(CHANNEL_LOOK[c])
  }
  const modeText = mode === 'AUTO' ? 'Auto: it never waits for you' : mode === 'MANUAL' ? 'Manual: it waits for you at every stage'
    : mode === 'PRODUCER' ? 'AI Producer reviews each stage' : `Review: you get ${review} min at each stage, then it goes on by itself`

  async function make() {
    if (!idea.trim()) { setMsg({ ok: false, text: 'Type an idea first.' }); return }
    setBusy(true)
    setMsg(null)
    try {
      if (kind === 'video') {
        const r = await api.createVideo(idea, mode, {}, review)
        window.location.hash = `#/p/video/${r.id}`
      } else {
        const body: NewDream = { idea, look, shots, quality, defaultMode: mode, reviewMinutes: review, music, scout: scout && look !== 'unfiltered', ...ed, gpuHold, pictureModel,
          ...(clip === 'last' ? { chainShots: true, chainGroup: 0 } : {}), ...(clip === 'svi' ? { sviScenes, sviSceneSeconds: sviSeconds, sviBetween: 'cut' as const } : {}) }
        const r = await dreamApi.create(body)
        window.location.hash = `#/p/short/${r.id}`
      }
    } catch (e) {
      setMsg({ ok: false, text: errorText(e) })
    } finally {
      setBusy(false)
    }
  }

  async function queue() {
    if (!idea.trim()) { setMsg({ ok: false, text: 'Type an idea first.' }); return }
    try {
      await autopilotApi.addIdea(idea, kind === 'short' ? look : null)
      setMsg({ ok: true, text: 'Added to the Autopilot queue. It will be made on a daily run (Settings → Autopilot).' })
      setIdea('')
    } catch (e) {
      setMsg({ ok: false, text: errorText(e) })
    }
  }

  return (
    <>
      <h1>Create</h1>
      <section className="card">
        <textarea rows={5} value={idea} onChange={(e) => { setIdea(e.target.value); setMsg(null) }} aria-label="Your idea"
          placeholder={kind === 'short' ? 'A quiet lighthouse on a black rock at dusk, warm light in its window, calm sea…'
            : 'Describe the video: "8-minute cinematic timelapse of the Himalayas forming, sparse narration" or "45-second Short on how UPI fraud works, in Hindi".'} />
        <div className="sv-chips" style={{ marginTop: 10 }} role="group" aria-label="Product">
          {(['short', 'video'] as Kind[]).map((k) => (
            <button key={k} type="button" className={`sv-chip ${kind === k ? 'on' : ''}`} aria-pressed={kind === k} onClick={() => setKind(k)}>{k === 'short' ? 'Short' : 'Video'}</button>))}
        </div>
        <div className="sv-chips" style={{ marginTop: 8 }} role="group" aria-label="Channel">
          {(['dreamscapes', 'science'] as Channel[]).map((c) => (
            <button key={c} type="button" className={`sv-chip ${channel === c ? 'on' : ''}`} aria-pressed={channel === c} onClick={() => pickChannel(c)}>{c === 'science' ? 'Science' : 'Dreamscapes'}</button>))}
        </div>
        <p className="sv-note" style={{ marginTop: 10 }}>
          {estimate ? `Past ${kind === 'short' ? 'Shorts' : 'Videos'} cost about ${inr(estimate.inr)} (middle of ${estimate.n} finished). ` : 'No finished projects to estimate from yet. '}
          {modeText}. You can step in at any stage.
        </p>
        <details className="sv-adv" style={{ marginTop: 8 }}>
          <summary>Advanced options</summary>
          <div className="sv-fields">
            <label>Mode for all stages
              <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
                <option value="REVIEW">Review (waits, then continues)</option><option value="AUTO">Auto</option><option value="MANUAL">Manual</option><option value="PRODUCER">AI Producer</option></select></label>
            {mode === 'REVIEW' && <label>Review window (min)
              <select value={review} onChange={(e) => setReview(Number(e.target.value))}>{[5, 10, 20, 30, 60].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>}
            {kind === 'short' && <>
              <label>Look <select value={look} onChange={(e) => setLook(e.target.value)}>
                <option value="lonely">Lonely object</option><option value="drive">Drive-by</option><option value="walkthrough">Walk-through</option><option value="dreamcore">Dreamcore (soft pastel)</option>
                <option value="impossible">Impossible place</option><option value="architecture">Impossible architecture</option><option value="unfiltered">Unfiltered (my prompt, exactly)</option></select></label>
              {look !== 'unfiltered' && clip !== 'svi' && <label>Shots <select value={shots} onChange={(e) => setShots(Number(e.target.value))}>{[3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>}
              <label>Clip quality <select value={quality} onChange={(e) => setQuality(e.target.value)}><option value="480p">480p (cheapest)</option><option value="720p">720p</option></select></label>
              <label>Pictures <select value={pictureModel} onChange={(e) => setPictureModel(e.target.value as 'flux2-klein' | 'zimage')}>
                <option value="flux2-klein">FLUX.2 klein (default)</option><option value="zimage">Z-Image</option></select></label>
              <label>Music <select value={music} onChange={(e) => setMusic(e.target.value as 'bed' | 'lyria')}><option value="bed">Your track (free)</option><option value="lyria">Lyria (≈₹10)</option></select></label>
              <label>Clip method <select value={clip} onChange={(e) => setClip(e.target.value as ClipMethod)}>
                <option value="off">Separate clips</option><option value="last">Last-frame chain</option><option value="svi">SVI scenes (smooth long shots)</option></select></label>
              {clip === 'svi' && <label>Scenes <select value={sviScenes} onChange={(e) => setSviScenes(Number(e.target.value))}>{[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>}
              {clip === 'svi' && <label>Scene length <select value={sviSeconds} onChange={(e) => setSviSeconds(Number(e.target.value))}>{[10, 15, 20].map((n) => <option key={n} value={n}>{n} s</option>)}</select></label>}
              <label>GPU power <select value={gpuHold} onChange={(e) => setGpuHold(e.target.value as 'auto' | 'manual')}><option value="auto">Auto (off when done)</option><option value="manual">Manual (I turn it off)</option></select></label>
              <label className="check"><input type="checkbox" checked={scout} onChange={(e) => setScout(e.target.checked)} /> Scout similar Shorts first (≈₹6)</label>
              {EDIT_SWITCHES.map(([k, label]) => <label key={k} className="check"><input type="checkbox" checked={ed[k]} onChange={(e) => setEd({ ...ed, [k]: e.target.checked })} /> {label}</label>)}
            </>}
          </div>
          {kind === 'short' && <p className="muted small">Hook title, idea packs and "choose the path" are still in the <a href="#/dream">classic Dream form</a>.</p>}
        </details>
        {msg && <p className={msg.ok ? 'sv-note' : 'error'}>{msg.text}</p>}
        <div className="row" style={{ marginTop: 12 }}>
          <button type="button" className="primary" disabled={busy} onClick={() => void make()}>{busy ? 'Starting…' : 'Make it'}</button>
          <button type="button" disabled={busy} onClick={() => void queue()} title="Adds the idea to the Autopilot list; it is made on a daily run">Add to Autopilot queue instead</button>
        </div>
      </section>
    </>
  )
}
