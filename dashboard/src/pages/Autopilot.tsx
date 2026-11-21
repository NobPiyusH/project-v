import { useEffect, useState } from 'react'
import { assetUrl } from '../api'
import { autopilotApi, type Privacy, type Run, type Settings, type Status } from '../autopilotApi'
import { DREAM_STEPS, STEP_LABEL, type DreamMode } from '../dreamApi'
import { usePoll } from '../hooks'

// Daily autopilot: settings, idea list, and each day's Short. A finished Short waits here until you tap Post.

const LOOKS: [string, string][] = [['lonely', 'Lonely object'], ['drive', 'Drive-by'], ['walkthrough', 'Walk-through'],
  ['dreamcore', 'Dreamcore'], ['impossible', 'Impossible place']]
const RUN_TEXT: Record<string, [string, string]> = {
  WAITING_START: ['Waiting to start', 'b-warn'], RUNNING: ['Making it', 'b-info'], NEEDS_YOU: ['Needs you', 'b-warn'],
  FAILED: ['Failed', 'b-bad'], READY: ['Ready to post', 'b-ok'], POSTING: ['Uploading', 'b-info'], POSTED: ['Posted', 'b-ok'],
  POST_FAILED: ['Upload failed', 'b-bad'], SKIPPED: ['Skipped', 'b-idle'],
}

export function Autopilot() {
  const { data: status, refresh: refreshStatus } = usePoll(autopilotApi.status, 10000)
  const { data: runs, error, refresh: refreshRuns } = usePoll(autopilotApi.runs, 5000)
  const [err, setErr] = useState<string | null>(null)
  const act = (fn: () => Promise<unknown>) => {
    setErr(null)
    fn().then(() => { refreshRuns(); refreshStatus() }).catch((e: Error) => setErr(e.message))
  }
  const ready = (runs ?? []).filter((r) => r.status === 'READY' || r.status === 'POST_FAILED' || r.status === 'POSTING')

  return (
    <>
      {ready.map((r) => <ReadyCard key={r.id} run={r} connected={status?.youtubeConnected ?? false} act={act} />)}
      {err && <p className="error">{err}</p>}
      <SettingsCard status={status ?? undefined} onSaved={refreshStatus} />
      <IdeasCard />
      <section className="card" style={{ minWidth: 0 }}>
        <div className="card-head"><h2>Daily runs</h2><span className="muted small">{status ? `Laptop time zone for the schedule: ${status.zone}` : ''}</span></div>
        {error && <p className="error">{error}</p>}
        {status && status.waitingOn.length > 0 && <p className="error small">New days wait until you deal with: {status.waitingOn.join(', ')}</p>}
        {runs && runs.length === 0 && <p className="muted">No runs yet. Turn the autopilot on and add a few ideas.</p>}
        {runs && runs.length > 0 && (
          <div style={{ overflowX: 'auto' }}><table>
            <thead><tr><th>Day</th><th>Short</th><th>Status</th><th className="hide-sm">Spent</th><th></th></tr></thead>
            <tbody>{runs.map((r) => (
              <tr key={r.id}>
                <td>{r.run_date.slice(0, 10)}</td>
                <td>{r.dream_short_id ? <a href={`#/dream/${r.dream_short_id}`}>{r.title ?? r.idea}</a> : r.idea ?? ''}
                  {r.message && <div className="muted small">{r.message}</div>}</td>
                <td><span className={`badge ${RUN_TEXT[r.status]?.[1] ?? 'b-idle'}`}>{RUN_TEXT[r.status]?.[0] ?? r.status}</span></td>
                <td className="hide-sm">₹{Number(r.spent_inr).toFixed(0)}</td>
                <td className="row">
                  {r.status === 'WAITING_START' && <button className="primary" onClick={() => act(() => autopilotApi.start(r.id))}>Start now</button>}
                  {r.status === 'NEEDS_YOU' && r.final_asset_id && <button onClick={() => act(() => autopilotApi.retryKit(r.id))}>Retry title</button>}
                  {['WAITING_START', 'NEEDS_YOU', 'FAILED', 'RUNNING'].includes(r.status) &&
                    <button onClick={() => { if (window.confirm('Stop holding up the next days because of this run? The Short itself is not deleted.')) act(() => autopilotApi.dismiss(r.id)) }}>Dismiss</button>}
                  {r.youtube_id && <a href={`https://youtube.com/shorts/${r.youtube_id}`} target="_blank" rel="noreferrer">Open on YouTube</a>}
                </td>
              </tr>))}
            </tbody>
          </table></div>
        )}
      </section>
    </>
  )
}

/** A finished Short: watch it, edit the words, tap Post. Nothing is uploaded without this tap. */
function ReadyCard({ run, connected, act }: { run: Run; connected: boolean; act: (fn: () => Promise<unknown>) => void }) {
  const [title, setTitle] = useState(run.title ?? '')
  const [description, setDescription] = useState(run.description ?? '')
  const [hashtags, setHashtags] = useState(run.hashtags ?? '#Shorts')
  const [privacy, setPrivacy] = useState<Privacy>(run.privacy ?? 'private')
  const posting = run.status === 'POSTING'
  return (
    <section className="card">
      <div className="card-head"><h2>Ready to post · {run.run_date.slice(0, 10)}</h2>
        <span className={`badge ${RUN_TEXT[run.status]?.[1]}`}>{RUN_TEXT[run.status]?.[0]}</span></div>
      {run.status === 'POST_FAILED' && <p className="error small">{run.message}</p>}
      <div className="row" style={{ alignItems: 'flex-start' }}>
        {run.final_asset_id && <video src={assetUrl(run.final_asset_id)} controls playsInline
          style={{ maxHeight: 520, maxWidth: '100%', borderRadius: 8 }} />}
        <div style={{ flex: 1, minWidth: 240, display: 'grid', gap: 8 }}>
          <label className="small">Title ({title.length}/100)
            <input type="text" value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} style={{ width: '100%' }} /></label>
          <label className="small">Description
            <textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} style={{ width: '100%' }} /></label>
          <label className="small">Hashtags
            <input type="text" value={hashtags} onChange={(e) => setHashtags(e.target.value)} style={{ width: '100%' }} /></label>
          <label className="small">Who can see it <select value={privacy} onChange={(e) => setPrivacy(e.target.value as Privacy)}>
            <option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>
          {!connected && <p className="small error">YouTube is not connected yet: run the one-time sign-in first (see docs/autopilot.md).</p>}
          <div className="row">
            <button className="primary" disabled={posting || !title.trim() || !connected}
              onClick={() => { if (window.confirm(`Upload "${title}" to YouTube as ${privacy}?`)) act(() => autopilotApi.post(run.id, { title, description, hashtags, privacy })) }}>
              {posting ? 'Uploading…' : 'Post to YouTube'}</button>
            {run.dream_short_id && <a href={`#/dream/${run.dream_short_id}`} className="small">Open the Short</a>}
          </div>
        </div>
      </div>
    </section>
  )
}

function SettingsCard({ status, onSaved }: { status: Status | undefined; onSaved: () => void }) {
  const [s, setS] = useState<Settings | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  useEffect(() => { autopilotApi.settings().then(setS).catch((e: Error) => setMsg(e.message)) }, [])
  if (!s) return <section className="card"><p className="muted">{msg ?? 'Loading…'}</p></section>
  const save = (next: Settings) => {
    setS(next)
    setMsg(null)
    autopilotApi.save(next).then((saved) => { setS(saved); setMsg('Saved.'); onSaved() }).catch((e: Error) => setMsg(e.message))
  }
  return (
    <section className="card">
      <div className="card-head"><h2>Daily autopilot</h2>
        <label><input type="checkbox" checked={s.enabled} onChange={(e) => save({ ...s, enabled: e.target.checked })} />{' '}
          <strong>{s.enabled ? 'On' : 'Off'}</strong></label></div>
      <p className="muted small">Each day at the start time it makes one Dream Short from your idea list (or an unused Scout idea), with the
        AI Producer deciding inside your limits (daily cap below, ₹100 per Short, asks you above ₹50). It never uploads by itself: the finished Short waits
        above for your Post. If a day fails or needs you, the next days wait. The backend must be running (if the laptop was off, it starts
        when the backend is next up that day).</p>
      <div className="row">
        <label className="small">Start at <input type="time" value={s.startTime} onChange={(e) => save({ ...s, startTime: e.target.value })} /></label>
        <label className="small">Start <select value={s.startMode} onChange={(e) => save({ ...s, startMode: e.target.value as Settings['startMode'] })}>
          <option value="AUTO">Auto (at the time)</option><option value="REVIEW">Review (waits, then starts)</option><option value="MANUAL">Manual (waits for Start)</option></select></label>
        {s.startMode === 'REVIEW' && <label className="small">Wait (min) <input type="number" min={1} max={240} value={s.reviewMinutes}
          onChange={(e) => save({ ...s, reviewMinutes: Number(e.target.value) })} style={{ width: 70 }} /></label>}
        <label className="small">Daily cap ₹ <input type="number" min={0} value={s.dailyCapInr}
          onChange={(e) => save({ ...s, dailyCapInr: Number(e.target.value) })} style={{ width: 80 }} /></label>
        <label className="small">Look <select value={s.look ?? ''} onChange={(e) => save({ ...s, look: e.target.value || null })}>
          <option value="">Rotate daily</option>{LOOKS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label className="small">Shots <select value={s.shots} onChange={(e) => save({ ...s, shots: Number(e.target.value) })}>
          {[3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <label className="small"><input type="checkbox" checked={s.useScout} onChange={(e) => save({ ...s, useScout: e.target.checked })} /> Use Scout ideas when the list is empty</label>
        <label className="small">Post as <select value={s.privacy} onChange={(e) => save({ ...s, privacy: e.target.value as Privacy })}>
          <option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        {DREAM_STEPS.map((step) => (
          <span key={step} className="pill">{STEP_LABEL[step]}{' '}
            <select value={s.stepModes[step] ?? 'PRODUCER'} onChange={(e) => save({ ...s, stepModes: { ...s.stepModes, [step]: e.target.value as DreamMode } })}>
              <option value="PRODUCER">AI Producer</option><option value="AUTO">Auto</option><option value="REVIEW">Review</option><option value="MANUAL">Manual</option>
            </select></span>))}
        <span className="pill">Post <select value="MANUAL" disabled title="Uploading always waits for your tap"><option value="MANUAL">Manual (your tap)</option></select></span>
      </div>
      <p className="small muted">{status ? `${status.ideasLeft} idea(s) left · YouTube ${status.youtubeConnected ? 'connected' : 'not connected'}` : ''}{msg ? ` · ${msg}` : ''}</p>
    </section>
  )
}

function IdeasCard() {
  const { data: ideas, refresh } = usePoll(autopilotApi.ideas, 30000)
  const [idea, setIdea] = useState('')
  const [look, setLook] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const open = (ideas ?? []).filter((i) => !i.used_at)
  return (
    <section className="card">
      <div className="card-head"><h2>Idea list</h2><span className="muted small">used oldest first; your own before Scout ideas</span></div>
      <div className="row">
        <input type="text" value={idea} placeholder="A tram gliding through a flooded library at dawn" onChange={(e) => setIdea(e.target.value)}
          style={{ flex: 1, minWidth: 200 }} />
        <select value={look} onChange={(e) => setLook(e.target.value)}><option value="">Any look</option>
          {LOOKS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        <button className="primary" disabled={!idea.trim()} onClick={() => {
          setErr(null)
          autopilotApi.addIdea(idea, look || null).then(() => { setIdea(''); refresh() }).catch((e: Error) => setErr(e.message))
        }}>Add</button>
      </div>
      {err && <p className="error small">{err}</p>}
      <ul>{open.map((i) => (
        <li key={i.id}>{i.idea}{i.look && <span className="muted small"> · {i.look}</span>}
          {i.source === 'scout' && <span className="pill">Scout</span>}{' '}
          <button className="small" onClick={() => void autopilotApi.deleteIdea(i.id).then(refresh)}>Remove</button></li>))}
      </ul>
      {ideas && open.length === 0 && <p className="muted small">No ideas waiting.</p>}
    </section>
  )
}
