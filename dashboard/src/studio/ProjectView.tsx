import { useState, type ReactNode } from 'react'
import { assetUrl, type Mode } from '../api'
import { Countdown, ModeSwitch } from '../components'
import { errorText } from '../pages/dream/shared'
import { STAGE_LABEL, type Actions, type NextAction, type ProjectView as View, type StageView } from './projectAdapter'
import { inr } from './format'
import { Timeline } from './ui'

const VERDICT: Record<string, string> = { approve: 'Approve', redo: 'Redo', ask: 'Ask you' }

/** The one "what's next" card: Approve, Redo (with a note), Retry, Continue without it (rule 2b). */
function NextCard({ next, actions, shotIds, onChanged }: { next: NextAction; actions: Actions; shotIds: string[]; onChanged: () => void }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setErr(null)
    try {
      await fn()
      setNote('')
      onChanged()
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  const failed = next.kind === 'retry'
  const redoLabel = shotIds.length > 0 ? `Redo ${shotIds.length} picked` : 'Redo'
  return (
    <section className={`card sv-next ${failed ? 'bad' : ''}`}>
      <div className="card-head">
        <h2>{failed ? '⚠' : '⏳'} Needs you: {next.title}</h2>
        <span className="pill">{STAGE_LABEL[next.stage]}</span>
        {next.deadline && <Countdown deadline={next.deadline} />}
      </div>
      {next.detail && <p className="muted" style={{ marginTop: 0 }}>{next.detail}</p>}
      {next.producer && <p className="sv-note">AI Producer: <strong>{VERDICT[next.producer.verdict] ?? next.producer.verdict}</strong>{next.producer.summary ? `. ${next.producer.summary}` : ''}</p>}
      {next.kind !== 'cost' && next.kind !== 'batch' && !failed && (
        <input type="text" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note"
          placeholder={next.canRedo ? 'A note for the redo (what to change)' : 'Optional note'} />
      )}
      {err && <p className="error small">{err}</p>}
      <div className="row" style={{ marginTop: 10 }}>
        {failed ? (
          <>
            <button type="button" className="primary" disabled={busy} onClick={() => void run(() => actions.retry(next, note))}>Retry</button>
            {next.canContinueWithout && <button type="button" disabled={busy} onClick={() => void run(() => actions.continueWithout(next, note))}>Continue without it</button>}
          </>
        ) : (
          <>
            <button type="button" className="primary" disabled={busy} onClick={() => void run(() => actions.approve(next, note))}>
              {next.kind === 'cost' ? 'Approve cost and start clips' : next.kind === 'batch' ? 'Start now' : 'Approve'}</button>
            {next.canRedo && <button type="button" disabled={busy || (!!next.redoNeedsNote && !note.trim())} title={next.redoNeedsNote && !note.trim() ? 'Write a note first: tell it what to change' : undefined}
              onClick={() => void run(() => actions.redo(next, note, shotIds))}>{redoLabel}</button>}
          </>
        )}
      </div>
    </section>
  )
}

/** The shared project page. Simple by default; Advanced (remembered) adds today's per-stage controls and detail cards. */
export function ProjectView({ view, actions, shotIds = [], onChanged, extraHead, advanced, classicHref }: {
  view: View; actions: Actions; shotIds?: string[]; onChanged: () => void; extraHead?: ReactNode; advanced: ReactNode; classicHref: string
}) {
  const [adv, setAdv] = useState(() => {
    try { return localStorage.getItem('pv-sv-advanced') === '1' } catch { return false }
  })
  const [modeErr, setModeErr] = useState<string | null>(null)
  const choose = (a: boolean) => {
    setAdv(a)
    try { localStorage.setItem('pv-sv-advanced', a ? '1' : '0') } catch { /* not remembered */ }
  }
  const setModes = async (fn: () => Promise<void>) => {
    setModeErr(null)
    try { await fn(); onChanged() } catch (e) { setModeErr(errorText(e)) }
  }
  const jump = (s: StageView) => {
    if (!adv) choose(true)
    window.setTimeout(() => document.getElementById(`sv-stage-${s.key}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 80)
  }

  return (
    <>
      <div className="sv-head">
        <a href="#/studio" className="small">← Studio</a>
        <span className="grow" />
        {extraHead}
        <div className="seg" role="group" aria-label="View">
          <button type="button" className={!adv ? 'on' : ''} aria-pressed={!adv} onClick={() => choose(false)}>Simple</button>
          <button type="button" className={adv ? 'on' : ''} aria-pressed={adv} onClick={() => choose(true)}>Advanced</button>
        </div>
      </div>
      <div>
        <h1>{view.title}</h1>
        <p className="muted small" style={{ margin: '4px 0 0' }}>{view.subtitle} · {view.statusText} · {inr(view.costInr)} so far</p>
      </div>
      <section className="card"><Timeline stages={view.stages} onPick={jump} /></section>
      {view.next && <NextCard next={view.next} actions={actions} shotIds={shotIds} onChanged={onChanged} />}
      {view.live && (
        <section className="card row small">
          <span className="muted">All remaining stages</span>
          <ModeSwitch value={(view.allMode ?? '') as Mode} onChange={(m) => void setModes(() => actions.setAllModes(m))} />
          {view.allMode == null && <span className="muted">(mixed: set per stage in Advanced)</span>}
          {view.allMode === 'REVIEW' && <span className="muted">you get {view.reviewMinutes} min at each stage</span>}
          {modeErr && <span className="error">{modeErr}</span>}
        </section>
      )}
      <div className="sv-two">
        <section className="card">
          <div className="card-head"><h2>{view.playerIsFinal ? 'Final' : 'Latest preview'}</h2>
            <span className="row">{view.downloads.map((d) => <a key={d.assetId} className="button" href={assetUrl(d.assetId)} download={d.file}>{d.label}</a>)}</span></div>
          {view.playerAssetId
            ? <div className="sv-player"><video key={view.playerAssetId} src={assetUrl(view.playerAssetId)} controls playsInline preload="metadata" /></div>
            : <div className="sv-player empty">The first preview appears here once pictures or clips exist.</div>}
        </section>
        <section className="card">
          <div className="card-head"><h2>Cost</h2><span className="pill">{inr(view.costInr)}</span></div>
          {view.costLines.length === 0 ? <p className="muted small">Nothing spent or metered yet.</p> : (
            <div className="sv-kv">{view.costLines.slice(0, 8).map((c) => (
              <div key={c.label} style={{ display: 'contents' }}><span>{c.label}</span><span className="num">{inr(c.inr)}{c.estimated ? '*' : ''}</span></div>))}</div>
          )}
          {view.costLines.some((c) => c.estimated) && <p className="muted small" style={{ marginBottom: 0 }}>* estimated, not yet billed</p>}
        </section>
      </div>
      {view.outputs.length > 0 && (
        <section className="card">
          <div className="card-head"><h2>What was made</h2><span className="muted small">Tap to open</span></div>
          {(['image', 'video', 'audio'] as const).map((t) => {
            const items = view.outputs.filter((o) => o.type === t)
            if (!items.length) return null
            return (
              <div key={t} style={{ marginBottom: 10 }}>
                <h3 className="small muted" style={{ margin: '0 0 6px' }}>{t === 'image' ? 'Pictures' : t === 'video' ? 'Clips' : 'Music'}</h3>
                {t === 'audio' ? items.map((o) => <audio key={o.assetId} src={assetUrl(o.assetId)} controls preload="none" style={{ width: '100%' }} />)
                  : <div className={`sv-grid ${view.vertical ? '' : 'wide'}`}>{items.map((o) => (
                    <figure key={o.assetId}>
                      <a className="open" href={assetUrl(o.assetId)} target="_blank" rel="noreferrer">
                        {t === 'image' ? <img src={assetUrl(o.assetId)} alt={o.label} loading="lazy" /> : <video src={assetUrl(o.assetId)} muted playsInline preload="metadata" />}
                      </a>
                      {o.label}
                    </figure>))}</div>}
              </div>
            )
          })}
        </section>
      )}
      {adv && (
        <div className="sv-adv-block">
          <h2>Advanced</h2>
          {view.live && (
            <section className="card">
              <div className="card-head"><h2>Mode per stage</h2><span className="muted small">Auto, Review (waits, then continues), Manual (holds) or AI Producer</span></div>
              <table className="sv-modes"><tbody>
                {view.stages.filter((s) => s.mode).map((s) => (
                  <tr key={s.key} id={`sv-stage-${s.key}`}><td>{s.label}</td>
                    <td><ModeSwitch value={s.mode as Mode} onChange={(m) => void setModes(() => actions.setStageMode(s, m))} /></td></tr>))}
              </tbody></table>
            </section>
          )}
          {advanced}
          <p className="muted small">Anything not shown here is still on the <a href={classicHref}>classic page for this project</a>.</p>
        </div>
      )}
    </>
  )
}
