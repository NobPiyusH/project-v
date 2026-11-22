const DREAM_STATUS_TEXT: Record<string, string> = {
  RUNNING: 'Working', WAITING: 'Waiting for you', WAITING_REVIEW: 'In review', WAITING_COST: 'Cost to approve',
  WAITING_BATCH: 'Batch queue',
  FAILED_WAITING: 'Failed: waiting', COMPLETED: 'Done', CANCELLED: 'Cancelled',
}

export function DreamStatusBadge({ status }: { status: string }) {
  const cls = status === 'COMPLETED' ? 'b-ok' : status.startsWith('WAITING') ? 'b-warn'
    : status === 'FAILED_WAITING' ? 'b-bad' : status === 'CANCELLED' ? 'b-idle' : 'b-info'
  return <span className={`badge ${cls}`}>{DREAM_STATUS_TEXT[status] ?? status}</span>
}
