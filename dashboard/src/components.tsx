import { useEffect, useState } from 'react'
import type { Mode } from './api'

const STATUS_CLASS: Record<string, string> = {
  COMPLETED: 'b-ok', APPROVED: 'b-ok', AUTO_PASSED: 'b-ok', AUTO_APPROVED: 'b-ok',
  RUNNING: 'b-info', CREATED: 'b-idle',
  WAITING: 'b-warn', WAITING_APPROVAL: 'b-warn', PAUSED: 'b-warn',
  REJECTED: 'b-bad', FAILED: 'b-bad', CANCELLED: 'b-bad',
  SKIPPED: 'b-idle', PENDING: 'b-idle',
}

const STATUS_LABEL: Record<string, string> = {
  AUTO_PASSED: 'Done (auto)', AUTO_APPROVED: 'Done (review time ended)', APPROVED: 'Approved', WAITING_APPROVAL: 'Waiting for you',
  WAITING: 'Waiting for you', RUNNING: 'Running', REJECTED: 'Rejected', SKIPPED: 'Skipped',
  COMPLETED: 'Completed', PAUSED: 'Paused', PENDING: 'Pending', CREATED: 'Created', FAILED: 'Failed',
}

export const MODE_HELP: Record<Mode, string> = {
  AUTO: 'Auto: does the stage and moves on',
  REVIEW: 'Review: does the stage, waits the review window for you, then moves on by itself',
  MANUAL: 'Manual: does the stage and waits until you approve',
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${STATUS_CLASS[status] ?? 'b-idle'}`}>{STATUS_LABEL[status] ?? status}</span>
}

export function ModeSwitch({ value, onChange, disabled }: { value: Mode; onChange: (m: Mode) => void; disabled?: boolean }) {
  return (
    <div className="seg" role="group" aria-label="Stage mode">
      {(['AUTO', 'REVIEW', 'MANUAL'] as Mode[]).map((m) => (
        <button key={m} type="button" className={value === m ? 'on' : ''} disabled={disabled}
          aria-pressed={value === m} onClick={() => value !== m && onChange(m)} title={MODE_HELP[m]}>
          {m === 'AUTO' ? 'Auto' : m === 'REVIEW' ? 'Review' : 'Manual'}
        </button>
      ))}
    </div>
  )
}

/** Live "continues in m:ss" for a stage in Review mode. */
export function Countdown({ deadline }: { deadline: string }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])
  const left = Math.max(0, Math.round((new Date(deadline).getTime() - now) / 1000))
  const m = Math.floor(left / 60)
  const s = String(left % 60).padStart(2, '0')
  return <span className="badge b-info">{left > 0 ? `continues by itself in ${m}:${s}` : 'continuing…'}</span>
}
