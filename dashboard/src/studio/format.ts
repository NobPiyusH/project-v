import type { Kind, RowStatus, StageState } from './projectAdapter'

export const KIND_LABEL: Record<Kind, string> = { short: 'Short', video: 'Video' }

/** "3 h ago", "2 d ago": the list only needs a rough age. */
export function age(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

export const inr = (v: number) => `₹${v >= 100 ? v.toFixed(0) : v.toFixed(2)}`

export const STATE_TEXT: Record<StageState, string> = { done: 'done', running: 'working', waiting: 'needs you', failed: 'failed', skipped: 'skipped', pending: '' }

export const statusClass = (s: RowStatus) => `st-${s}`

