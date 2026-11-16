import type { Mode } from './api'

const STATUS_CLASS: Record<string, string> = {
  COMPLETED: 'b-ok', APPROVED: 'b-ok', AUTO_PASSED: 'b-ok',
  RUNNING: 'b-info', CREATED: 'b-idle',
  WAITING: 'b-warn', WAITING_APPROVAL: 'b-warn', PAUSED: 'b-warn',
  REJECTED: 'b-bad', FAILED: 'b-bad', CANCELLED: 'b-bad',
  SKIPPED: 'b-idle', PENDING: 'b-idle',
}

const STATUS_LABEL: Record<string, string> = {
  AUTO_PASSED: 'Done (auto)', APPROVED: 'Approved', WAITING_APPROVAL: 'Waiting for you',
  WAITING: 'Waiting for you', RUNNING: 'Running', REJECTED: 'Rejected', SKIPPED: 'Skipped',
  COMPLETED: 'Completed', PAUSED: 'Paused', PENDING: 'Pending', CREATED: 'Created', FAILED: 'Failed',
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${STATUS_CLASS[status] ?? 'b-idle'}`}>{STATUS_LABEL[status] ?? status}</span>
}

export function ModeSwitch({ value, onChange, disabled }: { value: Mode; onChange: (m: Mode) => void; disabled?: boolean }) {
  return (
    <div className="seg" role="group" aria-label="Stage mode">
      {(['AUTO', 'MANUAL'] as Mode[]).map((m) => (
        <button key={m} type="button" className={value === m ? 'on' : ''} disabled={disabled}
          aria-pressed={value === m} onClick={() => value !== m && onChange(m)}>
          {m === 'AUTO' ? 'Auto' : 'Manual'}
        </button>
      ))}
    </div>
  )
}
