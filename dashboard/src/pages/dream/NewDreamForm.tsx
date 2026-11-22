import { useState } from 'react'
import { dreamApi, type DreamMode } from '../../dreamApi'
import { usePoll } from '../../hooks'
import { IdeaPicker } from './IdeaPicker'
import { EDIT_SWITCHES, HOOK_COLOURS, errorText } from './shared'

type ClipMethod = 'off' | 'last' | 'bridge' | 'svi'

/** The New Dream Short form. Options the owner turned off on purpose stay here as choices, off by default. */
export function NewDreamForm({ onSettled }: { onSettled: () => void }) {
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
  const [chainStyle, setChainStyle] = useState<ClipMethod>('off')
  const chain = chainStyle === 'last'
  const svi = chainStyle === 'svi'
  const [sviScenes, setSviScenes] = useState(1)
  const [sviSeconds, setSviSeconds] = useState(20)
  const [sviBetween, setSviBetween] = useState<'cut' | 'dissolve'>('cut')
  const [chainGroup, setChainGroup] = useState(0)
  const [locked, setLocked] = useState(false)
  const [pathChoice, setPathChoice] = useState(false)
  const [gpuHold, setGpuHold] = useState<'auto' | 'manual'>('auto')
  const [gpuIdle, setGpuIdle] = useState(10)
  const [pictureModel, setPictureModel] = useState<'flux2-klein' | 'zimage'>('flux2-klein')
  const raw = look === 'unfiltered' // your prompt exactly as written: nothing is added, judged or filtered
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
      const r = await dreamApi.create({ idea, look, shots, quality, defaultMode: mode, reviewMinutes: review, music, scout: scout && !raw, ...ed,
        gpuHold, gpuIdleMinutes: gpuIdle, pictureModel,
        ...(hook && !raw ? { hookTitle: true, hookText, hookColor } : {}), ...(chain ? { chainShots: true, chainGroup, chainLocked: locked && chainGroup === 0 } : {}),
        ...(chainStyle === 'bridge' ? { chainBridge: true } : {}),
        ...(svi ? { sviScenes, sviSceneSeconds: sviSeconds, sviBetween } : {}),
        ...(ideaRef ? { ideaPack: ideaRef.pack, ideaId: ideaRef.id } : {}), ...(pathChoice ? { pathChoice: true } : {}) })
      window.location.hash = `#/dream/${r.id}`
    } catch (e) {
      setFormError(errorText(e))
    } finally {
      setBusy(false)
      onSettled()
    }
  }

  return (
    <section className="card">
      <div className="card-head"><h2>New Dream Short</h2><span className="pill">Modal Wan clips · your music · laptop finish</span></div>
      <textarea rows={raw ? 5 : 2} value={idea}
        placeholder={raw ? (svi ? 'Used word for word. Each line = one 5 s beat of a scene; a blank line starts the next scene (up to 3 scenes of 5 beats).'
          : 'Write exactly what you want. It is used word for word: nothing is added or removed. One line = one shot.') : 'A pink house under a cloud at the end of a tiled pier'}
        onChange={(e) => { setIdea(e.target.value); setFormError(null) }} style={{ width: '100%' }} />
      {formError && <p className="error small">{formError}</p>}
      {raw && <p className="muted small">Unfiltered: no writer, style, "no people" rule, lessons, Scout, quality judging or colour grading. One line of your prompt = one shot of about 5 s (up to 8).</p>}
      {!raw && <IdeaPicker packs={packs} picked={ideaRef} onPick={(pack, i) => {
        if (!pack || !i) { setIdeaRef(null); return }
        setIdeaRef({ pack: pack.id, id: i.id }); setIdea(i.concept); setLook(pack.look); setScout(false)
        setHookText(i.hook); setFormError(null)
      }} />}
      <div className="row" style={{ marginTop: 10 }}>
        <label className="small">Look <select value={look} onChange={(e) => setLook(e.target.value)}>
          <option value="lonely">Lonely object</option><option value="drive">Drive-by</option><option value="walkthrough">Walk-through</option><option value="dreamcore">Dreamcore (soft pastel)</option><option value="impossible">Impossible place</option><option value="architecture">Impossible architecture</option>
          <option value="unfiltered">Unfiltered (my prompt, exactly)</option></select></label>
        {!raw && !svi && <label className="small">Shots <select value={shots} onChange={(e) => setShots(Number(e.target.value))}>
          {[3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>}
        {!raw && EDIT_SWITCHES.map(([k, label]) => <label key={k} className="small"><input type="checkbox" checked={ed[k]}
          onChange={(e) => setEd({ ...ed, [k]: e.target.checked })} /> {label}</label>)}
        {!raw && <label className="small"><input type="checkbox" checked={scout} onChange={(e) => setScout(e.target.checked)} /> Scout similar Shorts first (≈₹6)</label>}
        {!raw && <label className="small" title="A big two-colour title in the first ~2 s (off by default)"><input type="checkbox" checked={hook}
          onChange={(e) => setHook(e.target.checked)} /> Hook title</label>}
        {hook && !raw && <input className="small" value={hookText} placeholder="blank = written for you; *star* the big word" style={{ width: 250 }}
          onChange={(e) => setHookText(e.target.value)} />}
        {hook && !raw && <label className="small">Big word <select value={hookColor} onChange={(e) => setHookColor(e.target.value)}>
          {HOOK_COLOURS.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
        <label className="small" title="Separate: every shot is its own clip. Last frame: each clip starts from the last frame of the one before (one continuous move, clips run one at a time). First-last: every shot keeps its picture and each clip travels from its picture to the next one, so joins land on approved pictures (rented GPU pod only). SVI scenes: each scene is one smooth continuous shot of 10-20 s from one picture, no visible restarts (rented GPU pod only). Separate by default">
          Clip method <select value={chainStyle} onChange={(e) => setChainStyle(e.target.value as ClipMethod)}>
            <option value="off">separate clips</option><option value="last">last-frame chain</option><option value="bridge">first-last frame</option>
            <option value="svi">SVI scenes (smooth long shots)</option></select></label>
        {svi && <label className="small" title="1 = a single long shot; 2-3 = a story Short with a cut between scenes">Scenes <select value={sviScenes}
          onChange={(e) => setSviScenes(Number(e.target.value))}>{[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>}
        {svi && <label className="small" title="Each scene is made of 5 s beats (10 s = 3, 15 s = 4, 20 s = 5); longer than 20 s drifts">Scene length <select value={sviSeconds}
          onChange={(e) => setSviSeconds(Number(e.target.value))}>{[10, 15, 20].map((n) => <option key={n} value={n}>{n} s</option>)}</select></label>}
        {svi && sviScenes > 1 && <label className="small">Between scenes <select value={sviBetween}
          onChange={(e) => setSviBetween(e.target.value as 'cut' | 'dissolve')}><option value="cut">hard cut</option><option value="dissolve">0.5 s dissolve</option></select></label>}
        {chain && <label className="small">Chain <select value={chainGroup} onChange={(e) => setChainGroup(Number(e.target.value))}>
          <option value={0}>all shots in one scene</option><option value={2}>pairs (1+2, 3+4...)</option><option value={3}>threes (1+2+3, 4+5+6...)</option></select></label>}
        {chain && chainGroup === 0 && <label className="small" title="One camera path for the whole Short, one world and seed, cleaned last frames">
          <input type="checkbox" checked={locked} onChange={(e) => setLocked(e.target.checked)} /> Locked scene</label>}
        {!raw && <label className="small" title="Ends on a two-way choice (end card); a finished Short can be continued down the chosen path as the next episode (off by default)">
          <input type="checkbox" checked={pathChoice} onChange={(e) => setPathChoice(e.target.checked)} /> Choose the path</label>}
        <label className="small">Music <select value={music} onChange={(e) => setMusic(e.target.value as 'bed' | 'lyria')}>
          <option value="bed">Your track (free)</option><option value="lyria">Lyria (≈₹10)</option></select></label>
        <label className="small">Clips <select value={quality} onChange={(e) => setQuality(e.target.value)}>
          <option value="480p">480p (cheapest)</option><option value="720p">720p</option></select></label>
        <label className="small" title="Picture model on the rented GPU pod (the Modal route always uses Z-Image)">
          Pictures <select value={pictureModel} onChange={(e) => setPictureModel(e.target.value as 'flux2-klein' | 'zimage')}>
            <option value="flux2-klein">FLUX.2 klein (default)</option><option value="zimage">Z-Image</option></select></label>
        <label className="small">Mode <select value={mode} onChange={(e) => setMode(e.target.value as DreamMode)}>
          <option value="AUTO">Auto</option><option value="REVIEW">Review</option><option value="MANUAL">Manual</option></select></label>
        <label className="small" title="Auto: the studio turns the GPU off when the GPU steps are over. Manual: it stays on (quick redos) until you turn it off, or until nothing used it for the idle minutes">
          GPU power <select value={gpuHold} onChange={(e) => setGpuHold(e.target.value as 'auto' | 'manual')}>
            <option value="auto">Auto (off when done)</option><option value="manual">Manual (I turn it off)</option></select></label>
        {gpuHold === 'manual' && <label className="small">Auto-off after idle <select value={gpuIdle} onChange={(e) => setGpuIdle(Number(e.target.value))}>
          {[5, 10, 15, 30].map((n) => <option key={n} value={n}>{n} min</option>)}</select></label>}
        {mode === 'REVIEW' && <label className="small">Review window (min) <select value={review}
          onChange={(e) => setReview(Number(e.target.value))}>{[5, 10, 20, 30, 60].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>}
        <span style={{ flex: 1 }} />
        <button className="primary" disabled={busy} onClick={() => void start()}>Start</button>
      </div>
    </section>
  )
}
