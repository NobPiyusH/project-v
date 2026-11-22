import { useEffect, useRef, useState } from 'react'
import { api, type GpuLogLine, type GpuPod } from './api'

const MAX_LINES = 1500 // older lines are dropped so a long run never grows the page

// terminal colour codes and carriage-return progress bars, which would show as noise in a web page
// oxlint-disable-next-line no-control-regex
const clean = (s: string) => s.replace(/\u001b\[[0-9;]*m/g, '').replace(/\r/g, '\n').trim()

/** The log of one pod. Mounted with `key={pod id}`, so picking another pod starts a fresh view. */
function LogView({ podId }: { podId: string }) {
  const [lines, setLines] = useState<GpuLogLine[]>([])
  const [note, setNote] = useState<string | null>(null)
  const [follow, setFollow] = useState(true)
  const box = useRef<HTMLPreElement>(null)

  useEffect(() => {
    let stop = false
    let timer = 0
    let since: string | null = null
    const tick = async () => {
      try {
        const p = await api.gpuLogs(podId, since)
        if (stop) return
        setNote(p.error)
        if (p.next) since = p.next
        if (p.lines.length) setLines((cur) => [...cur, ...p.lines].slice(-MAX_LINES))
      } catch (e) {
        if (!stop) setNote(e instanceof Error ? e.message : String(e))
      }
      if (!stop) timer = window.setTimeout(() => void tick(), 4000) // next read only after this one finished
    }
    void tick()
    return () => { stop = true; window.clearTimeout(timer) }
  }, [podId])

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight
  }, [lines, follow])

  return (
    <>
      {note && <p className="small muted">{note}</p>}
      <pre ref={box} className="gpu-log" tabIndex={0}>
        {lines.length === 0 ? 'Waiting for log lines…' : lines.map((l, i) => (
          <div key={i} className={l.source === 'system' ? 'sys' : undefined}>
            <span className="t">{l.ts.slice(11, 19)}</span> {clean(l.line)}
          </div>
        ))}
      </pre>
      <label className="small row"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow newest line</label>
    </>
  )
}

/**
 * The pod list and live log. It only talks to the backend while mounted: the pod list every 10 s and the log every 4 s
 * (the backend does one bounded read per call), so a closed panel costs nothing.
 */
export function GpuLogs() {
  const [pods, setPods] = useState<GpuPod[] | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [picked, setPicked] = useState<string | null>(null)

  useEffect(() => {
    let stop = false
    const load = async () => {
      try {
        const r = await api.gpuPods()
        if (stop) return
        setPods(r.pods)
        setNote(!r.configured ? 'No RunPod key is set on the control plane (RUNPOD_API_KEY in .env).' : (r.error ?? null))
      } catch (e) {
        if (!stop) setNote(e instanceof Error ? e.message : String(e))
      }
    }
    void load()
    const t = window.setInterval(() => void load(), 10_000)
    return () => { stop = true; window.clearInterval(t) }
  }, [])

  // the owner's pick wins while that pod still exists; otherwise the first running pod
  const pod = pods?.find((p) => p.id === picked) ?? pods?.find((p) => p.status === 'RUNNING') ?? pods?.[0]
  return (
    <>
      {note && <p className="small" style={{ color: 'var(--bad)' }}>{note}</p>}
      {pods && pods.length === 0 && !note && <p className="muted small">No pods on the RunPod account. Nothing is billing.</p>}
      {pods && pods.length > 0 && (
        <select value={pod?.id ?? ''} onChange={(e) => setPicked(e.target.value)} aria-label="Pod">
          {pods.map((p) => <option key={p.id} value={p.id}>{p.name || p.id} · {p.status}</option>)}
        </select>
      )}
      {pod && (
        <div className="gpu-stats small">
          <span><b>{pod.status}</b></span>
          <span>${pod.costPerHrUsd.toFixed(2)}/h</span>
          <span>{pod.runningMinutes} min</span>
          <span>≈ ${pod.spentUsdEstimate.toFixed(2)} so far</span>
        </div>
      )}
      {pod && <LogView key={pod.id} podId={pod.id} />}
    </>
  )
}

/** Classic side panel (opened from the old top bar). */
export function GpuPanel({ onClose }: { onClose: () => void }) {
  return (
    <aside className="gpu-panel" aria-label="GPU logs">
      <div className="gpu-head">
        <strong>GPU logs</strong>
        <span className="spacer" />
        <button className="icon-btn" onClick={onClose} aria-label="Close GPU logs">Close</button>
      </div>
      <GpuLogs />
    </aside>
  )
}
