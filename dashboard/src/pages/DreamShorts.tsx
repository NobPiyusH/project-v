import { useState } from 'react'
import { assetUrl } from '../api'
import { DREAM_STEPS, dreamApi, type DreamDetail, type IdeaPack, type DreamLesson, type DreamMode, type DreamStep, type DreamSummary, type LessonTarget } from '../dreamApi'
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

const HOOK_COLOURS = ['red', 'yellow', 'orange', 'pink', 'cyan']

/** The idea pack picker: choose a curated concept seed instead of typing an idea (the Scout is then skipped). */
function IdeaPicker({ packs, picked, onPick }: {
  packs: IdeaPack[] | null | undefined
  picked: { pack: string; id: string } | null
  onPick: (pack: IdeaPack | null, idea: IdeaPack['ideas'][number] | null) => void
}) {
  if (!packs || packs.length === 0) return null
  const pack = packs.find((p) => p.id === picked?.pack) ?? packs[0]
  const idea = pack.ideas.find((i) => i.id === picked?.id)
  return (
    <div className="small" style={{ marginTop: 8 }}>
      <label>Idea pack <select value={picked ? pack.id : ''} onChange={(e) => {
        const p = packs.find((x) => x.id === e.target.value)
        onPick(p ?? null, p ? p.ideas[0] : null)
      }}>
        <option value="">None (type your own)</option>
        {packs.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.ideas.length})</option>)}</select></label>{' '}
      {picked && <label>Idea <select value={picked.id} onChange={(e) => onPick(pack, pack.ideas.find((i) => i.id === e.target.value) ?? null)}>
        {pack.ideas.map((i) => <option key={i.id} value={i.id}>{i.title}</option>)}</select></label>}
      {picked && idea && <div className="muted">Hook: {idea.hook.replace(/\*/g, '')} · Camera: {idea.camera}</div>}
    </div>
  )
}

/** Edit the hook title; the starred word is the big coloured one. It shows at the next finish (Redo from Finish is free). */
function HookCard({ d, onSave }: { d: DreamDetail; onSave: (text: string, color: string) => void }) {
  const [text, setText] = useState(d.hook_text ?? '')
  const [color, setColor] = useState(d.hook_color ?? 'red')
  const [edited, setEdited] = useState(false)
  const shown = edited ? text : d.hook_text ?? ''
  return (
    <section className="card">
      <div className="card-head"><h2>Hook title</h2><span className="pill">first ~2 s of the finished Short</span></div>
      <div className="row">
        <input value={shown} placeholder="Written with the shots: edit it here (*star* the big word)" style={{ flex: 1, minWidth: 240 }}
          onChange={(e) => { setText(e.target.value); setEdited(true) }} />
        <label className="small">Big word <select value={color} onChange={(e) => { setColor(e.target.value); setEdited(true) }}>
          {HOOK_COLOURS.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        <button disabled={!edited} onClick={() => { onSave(text, color); setEdited(false) }}>Save</button>
      </div>
      <p className="muted small">Up to 3 lines; the starred word is large and coloured. Saved text is kept when the shots are rewritten.</p>
    </section>
  )
}

/** "Choose the path": the two end-card options (editable), the episode chain, and Continue this dream (owner picks A or B). */
function PathCard({ d, onSave, onContinue }: { d: DreamDetail; onSave: (a: string, b: string) => void; onContinue: (p: 'A' | 'B') => void }) {
  const [a, setA] = useState(d.path_a ?? '')
  const [b, setB] = useState(d.path_b ?? '')
  const [edited, setEdited] = useState(false)
  const shownA = edited ? a : d.path_a ?? ''
  const shownB = edited ? b : d.path_b ?? ''
  const finished = d.status === 'COMPLETED' && !!d.final_asset_id
  return (
    <section className="card">
      <div className="card-head"><h2>Choose the path</h2><span className="pill">end card, last ~2 s · episode {d.episode}</span></div>
      {d.chain.length > 1 && <div className="row small" style={{ marginBottom: 8 }}>
        {d.chain.map((e) => <a key={e.id} href={`#/dream/${e.id}`} className={`pill ${e.id === d.id ? 'b-info' : ''}`}>
          Episode {e.episode}{e.chosen_path ? ` · ${e.chosen_path}` : ''}{e.pick_source === 'comments' ? ' (comments)' : ''}</a>)}
      </div>}
      <div className="row">
        <label className="small">← A <input value={shownA} placeholder="the door" style={{ width: 160 }}
          onChange={(e) => { setA(e.target.value); if (!edited) setB(shownB); setEdited(true) }} /></label>
        <label className="small">B → <input value={shownB} placeholder="the stairs" style={{ width: 160 }}
          onChange={(e) => { setB(e.target.value); if (!edited) setA(shownA); setEdited(true) }} /></label>
        <button disabled={!edited} onClick={() => { onSave(a, b); setEdited(false) }}>Save</button>
        <span style={{ flex: 1 }} />
        {finished && d.path_a && d.path_b && <>
          <span className="small">Continue this dream:</span>
          <button className="primary" onClick={() => { if (window.confirm(`Start episode ${d.episode + 1} down path A: "${d.path_a}"? It starts from this Short's last frame; paid steps ask before spending.`)) onContinue('A') }}>A: {d.path_a}</button>
          <button className="primary" onClick={() => { if (window.confirm(`Start episode ${d.episode + 1} down path B: "${d.path_b}"? It starts from this Short's last frame; paid steps ask before spending.`)) onContinue('B') }}>B: {d.path_b}</button>
        </>}
      </div>
      {d.children.length > 0 && <p className="small">Continued: {d.children.map((c) => <a key={c.id} href={`#/dream/${c.id}`} style={{ marginRight: 10 }}>{c.title ?? 'new episode'} ({c.chosen_path})</a>)}</p>}
      <p className="muted small">2-4 words each, written with the shots; your edit is kept when the shots are rewritten and shows at the next finish (Redo from Finish is free).</p>
    </section>
  )
}

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
  const [hook, setHook] = useState(false)
  const [hookText, setHookText] = useState('')
  const [hookColor, setHookColor] = useState('red')
  const [chain, setChain] = useState(false)
  const [chainGroup, setChainGroup] = useState(0)
  const [locked, setLocked] = useState(false)
  const [pathChoice, setPathChoice] = useState(false)
  const [ideaRef, setIdeaRef] = useState<{ pack: string; id: string } | null>(null)
  const { data: packs } = usePoll(dreamApi.ideaPacks, 600000)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  async function start() {
    if (!idea.trim() && !ideaRef) {
      setFormError('Type an idea first')
      return
    }
    setBusy(true)
    try {
      const r = await dreamApi.create({ idea, look, shots, quality, defaultMode: mode, reviewMinutes: review, music, scout, ...ed,
        ...(hook ? { hookTitle: true, hookText, hookColor } : {}), ...(chain ? { chainShots: true, chainGroup, chainLocked: locked && chainGroup === 0 } : {}),
        ...(ideaRef ? { ideaPack: ideaRef.pack, ideaId: ideaRef.id } : {}), ...(pathChoice ? { pathChoice: true } : {}) })
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
        <IdeaPicker packs={packs} picked={ideaRef} onPick={(pack, i) => {
          if (!pack || !i) { setIdeaRef(null); return }
          setIdeaRef({ pack: pack.id, id: i.id }); setIdea(i.concept); setLook(pack.look); setScout(false)
          setHookText(i.hook); setFormError(null)
        }} />
        <div className="row" style={{ marginTop: 10 }}>
          <label className="small">Look <select value={look} onChange={(e) => setLook(e.target.value)}>
            <option value="lonely">Lonely object</option><option value="drive">Drive-by</option><option value="walkthrough">Walk-through</option><option value="dreamcore">Dreamcore (soft pastel)</option><option value="impossible">Impossible place</option><option value="architecture">Impossible architecture</option></select></label>
          <label className="small">Shots <select value={shots} onChange={(e) => setShots(Number(e.target.value))}>
            {[3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          {EDIT_SWITCHES.map(([k, label]) => <label key={k} className="small"><input type="checkbox" checked={ed[k]}
            onChange={(e) => setEd({ ...ed, [k]: e.target.checked })} /> {label}</label>)}
          <label className="small"><input type="checkbox" checked={scout} onChange={(e) => setScout(e.target.checked)} /> Scout similar Shorts first (≈₹6)</label>
          <label className="small" title="A big two-colour title in the first ~2 s (off by default)"><input type="checkbox" checked={hook}
            onChange={(e) => setHook(e.target.checked)} /> Hook title</label>
          {hook && <input className="small" value={hookText} placeholder="blank = written for you; *star* the big word" style={{ width: 250 }}
            onChange={(e) => setHookText(e.target.value)} />}
          {hook && <label className="small">Big word <select value={hookColor} onChange={(e) => setHookColor(e.target.value)}>
            {HOOK_COLOURS.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
          <label className="small" title="Each clip starts from the last frame of the one before: one continuous camera move. Clips then run one at a time (off by default)">
            <input type="checkbox" checked={chain} onChange={(e) => setChain(e.target.checked)} /> Chain shots</label>
          {chain && <label className="small">Chain <select value={chainGroup} onChange={(e) => setChainGroup(Number(e.target.value))}>
            <option value={0}>all shots in one scene</option><option value={2}>pairs (1+2, 3+4...)</option><option value={3}>threes (1+2+3, 4+5+6...)</option></select></label>}
          {chain && chainGroup === 0 && <label className="small" title="One camera path for the whole Short, one world and seed, cleaned last frames">
            <input type="checkbox" checked={locked} onChange={(e) => setLocked(e.target.checked)} /> Locked scene</label>}
          <label className="small" title="Ends on a two-way choice (end card); a finished Short can be continued down the chosen path as the next episode (off by default)">
            <input type="checkbox" checked={pathChoice} onChange={(e) => setPathChoice(e.target.checked)} /> Choose the path</label>
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
      <Lessons />
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
                <td><strong>{d.title ?? d.idea}</strong>{d.episode > 1 && <span className="pill" style={{ marginLeft: 6 }}>Ep {d.episode}</span>}{d.title && <div className="muted small">{d.idea}</div>}</td>
                <td>{statusBadge(d.status)}
                  {d.status === 'WAITING_COST' && (
                    <div><button className="primary small" style={{ marginTop: 6 }}
                      onClick={(e) => { e.stopPropagation(); void dreamApi.approve(d.id, 'CLIPS').then(refresh) }}>
                      Approve cost</button></div>)}</td>
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

      <Timeline d={d} />

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
              {s.clip_asset_id && <Review dreamId={d.id} shotId={s.id} />}
            </div>))}
        </div>
      </section>

      <Check json={d.check_json} title="Quality check" />
      <Check json={d.picture_check_json} title="Picture check (before clips)" />
      {d.final_asset_id && <section className="card"><div className="card-head"><h2>Your review of this Short</h2></div>
        <Review dreamId={d.id} shotId={null} /></section>}

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
function Check({ json, title }: { json: string | null; title: string }) {
  if (!json) return null
  const c = parse(json) as { overall: number; summary: string; scores: Record<string, number>;
    issues: { shot: number; atSec: number; problem: string; fix: string }[] }
  return (
    <section className="card">
      <div className="card-head"><h2>{title}</h2><span className="pill">{c.overall}/10</span></div>
      <p className="small">{c.summary}</p>
      {c.issues.length > 0 && <ul className="small">{c.issues.map((i, k) => (
        <li key={k}><strong>{i.shot > 0 ? `Shot ${i.shot}` : 'Whole Short'}{i.atSec ? ` at ${i.atSec.toFixed(1)} s` : ''}:</strong>{' '}
          {i.problem} <span className="muted">Fix: {i.fix}</span></li>))}</ul>}
      <div className="small muted">{Object.entries(c.scores).map(([k, v]) => `${CHECK_LABEL[k] ?? k} ${v}`).join(' · ')}</div>
    </section>
  )
}

/** Learning loop: the owner rates a clip or the Short; a note becomes a proposed lesson in the Lessons list. */
function Review({ dreamId, shotId }: { dreamId: string; shotId: string | null }) {
  const [rating, setRating] = useState(0)
  const [note, setNote] = useState('')
  const [saved, setSaved] = useState('')
  const save = () => dreamApi.review(dreamId, shotId, rating, note)
    .then(() => { setSaved(note.trim() ? 'Saved. Your note is now a proposed lesson.' : 'Saved.'); setNote('') })
    .catch((e: Error) => setSaved(e.message))
  return (
    <div className="small" style={{ marginTop: 6 }}>
      <div className="row" style={{ gap: 2 }} role="radiogroup" aria-label="Rating">
        {[1, 2, 3, 4, 5].map((n) => <button key={n} type="button" aria-label={`${n} of 5`} aria-checked={rating === n} role="radio"
          onClick={() => setRating(n)} style={{ padding: '0 4px', color: n <= rating ? 'var(--accent)' : 'var(--muted)' }}>★</button>)}
      </div>
      <textarea rows={2} value={note} placeholder={shotId ? 'What was wrong or right in this clip?' : 'What should the next Shorts do differently?'}
        aria-label="Review note" onChange={(e) => setNote(e.target.value)} style={{ width: '100%', marginTop: 4 }} />
      <div className="row"><button disabled={rating === 0} onClick={() => void save()}>Save review</button>
        {saved && <span className="muted">{saved}</span>}</div>
    </div>
  )
}

const TARGET_LABEL: Record<LessonTarget, string> = { WRITER: 'Writer and camera', PICTURE: 'Pictures', ALL: 'Everything' }

/** Lessons from the owner's reviews: proposed ones wait for approval; only active ones go into the prompts. */
function Lessons() {
  const { data, refresh } = usePoll(() => dreamApi.lessons(), 30000)
  const [edits, setEdits] = useState<Record<string, string>>({})
  if (!data || data.length === 0) return null
  const set = (l: DreamLesson, change: Parameters<typeof dreamApi.lesson>[1]) => void dreamApi.lesson(l.id, change).then(refresh)
  return (
    <section className="card">
      <div className="card-head"><h2>Lessons</h2><span className="muted small">Only active lessons go into the next Shorts</span></div>
      <table className="small" style={{ width: '100%' }}><tbody>
        {data.map((l) => (
          <tr key={l.id}>
            <td><span className={`badge ${l.status === 'ACTIVE' ? 'b-ok' : 'b-info'}`}>{l.status === 'ACTIVE' ? 'Active' : 'Proposed'}</span></td>
            <td style={{ width: '60%' }}><input value={edits[l.id] ?? l.rule} aria-label="Lesson rule" style={{ width: '100%' }}
              onChange={(e) => setEdits({ ...edits, [l.id]: e.target.value })}
              onBlur={() => edits[l.id] !== undefined && edits[l.id] !== l.rule && set(l, { rule: edits[l.id] })} />
              {l.shot_ord != null && <span className="muted"> from clip {l.shot_ord + 1}{l.rating ? `, rated ${l.rating}/5` : ''}</span>}</td>
            <td><select value={l.target} aria-label="Applies to" onChange={(e) => set(l, { target: e.target.value as LessonTarget })}>
              {(Object.keys(TARGET_LABEL) as LessonTarget[]).map((t) => <option key={t} value={t}>{TARGET_LABEL[t]}</option>)}</select></td>
            <td className="row">{l.status !== 'ACTIVE' && <button className="primary" onClick={() => set(l, { status: 'ACTIVE' })}>Approve</button>}
              <button onClick={() => set(l, { status: 'RETIRED' })}>Retire</button></td>
          </tr>))}
      </tbody></table>
    </section>
  )
}

/** Auto (the studio picks the cheapest, learning from your picks) or Manual (you pick from the live, price-sorted list). */
function GpuCard({ id }: { id: string }) {
  const { data: on } = usePoll(() => dreamApi.gpuEnabled(), 60000)
  const { data: pick, refresh } = usePoll(() => dreamApi.gpuPick(id), 5000, [id])
  const { data: opts, refresh: refreshOpts } = usePoll(() => dreamApi.gpuOptions(), 60000)
  const [err, setErr] = useState<string | null>(null)
  if (!on?.enabled || !pick) return null
  const set = async (mode: 'auto' | 'manual', choice?: string) => {
    setErr(null)
    try { await dreamApi.setGpuPick(id, mode, choice) } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    void refresh()
  }
  return (
    <section className="card">
      <div className="card-head"><h2>GPU</h2>
        <span className="muted small">{pick.rented ? 'A GPU is rented for this Short; a change applies to the next rental' : 'Chosen when the Short needs a GPU'}</span></div>
      <div className="row">
        <label><input type="radio" checked={pick.mode === 'auto'} onChange={() => void set('auto')} /> Auto (cheapest; learns from your picks)</label>
        <label><input type="radio" checked={pick.mode === 'manual'} onChange={() => void set('manual', pick.choice || opts?.find((o) => o.available)?.key || undefined)} /> Manual (I pick)</label>
        <button onClick={() => void refreshOpts()}>Refresh prices</button>
      </div>
      {pick.mode === 'manual' && (
        <table className="small" style={{ width: '100%' }}><tbody>
          {(opts ?? []).map((o, i) => (
            <tr key={`${o.provider}-${o.key ?? i}`} style={o.available ? undefined : { opacity: 0.55 }}>
              <td><input type="radio" disabled={!o.available} checked={!!o.key && pick.choice === o.key} aria-label={`Pick ${o.provider} ${o.gpu ?? ''}`}
                onChange={() => o.key && void set('manual', o.key)} /></td>
              <td>{o.provider}</td><td>{o.gpu ? o.gpu.replace('|', ' ') : '–'}</td>
              <td>{o.available ? `$${o.hourlyUsd.toFixed(2)}/h` : '–'}</td><td>{o.available ? o.stock : (o.why ?? 'unavailable')}</td>
            </tr>))}
        </tbody></table>)}
      {err && <p className="error small">{err}</p>}
    </section>
  )
}

const IST: Intl.DateTimeFormatOptions = { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit' }
const ist = (t: string | null) => (t ? new Date(t).toLocaleTimeString('en-IN', IST) : '–')
function dur(sec: number | null) {
  if (sec == null) return '–'
  const m = Math.floor(sec / 60), s = Math.round(sec % 60)
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : m ? `${m} min ${s} s` : `${s} s`
}

/** Per step: when it started and finished (IST), how long it worked, what it cost; the running step against its usual time. */
function Timeline({ d }: { d: DreamDetail }) {
  if (!d.timeline) return null
  return (
    <section className="card">
      <div className="card-head"><h2>Steps</h2><span className="muted small">Times in IST · work time excludes waiting for you</span></div>
      <table>
        <thead><tr><th>Step</th><th>Status</th><th>Started</th><th>Finished</th><th>Took</th><th>₹</th></tr></thead>
        <tbody>{d.timeline.map((t) => (
          <tr key={t.step}>
            <td>{STEP_LABEL[t.step]}</td>
            <td className="small">{t.status === 'PENDING' ? <span className="muted">not started</span> : t.status.toLowerCase().replace('_', ' ')}</td>
            <td className="small">{ist(t.startedAt)}</td>
            <td className="small">{ist(t.finishedAt)}</td>
            <td className="small">{t.startedAt ? dur(t.workSeconds) : '–'}
              {t.startedAt && !t.finishedAt && <span className="muted">{' '}/ {t.typicalSeconds != null ? `usually ${dur(t.typicalSeconds)}` : 'no estimate yet'}</span>}</td>
            <td className="small">{t.costInr ? t.costInr.toFixed(2) : '–'}</td>
          </tr>))}</tbody>
      </table>
      <h3 style={{ margin: '14px 0 6px' }}>Log</h3>
      {d.log.length === 0 && <p className="muted small">Nothing yet.</p>}
      <div className="small" style={{ maxHeight: 280, overflowY: 'auto' }}>
        {d.log.map((l, i) => <div key={i} style={{ marginBottom: 3 }}><span className="muted">{ist(l.at)}</span> {l.text}</div>)}
      </div>
    </section>
  )
}
