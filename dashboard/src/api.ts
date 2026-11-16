// Typed client for the control-plane REST API (/api, proxied by Vite in dev).

export type Mode = 'AUTO' | 'MANUAL'

export interface StageInfo { id: string; label: string; description: string }

export interface VideoSummary {
  id: string
  title: string | null
  prompt: string
  status: string
  current_stage: string | null
  spent_inr: number
  created_at: string
  updated_at: string
}

export interface StageRun {
  stage: string
  attempt: number
  mode: Mode
  status: string
  summary: string | null
  artifact_json: string | null
  owner_notes: string | null
  started_at: string
  finished_at: string | null
}

export interface CostRow {
  provider_id: string
  amount_inr: number
  units: number | null
  unit_type: string | null
  estimated: boolean
  note: string | null
  ts: string
}

export interface VideoDetail extends VideoSummary {
  workflow_id: string
  plan_json: string | null
  modes_json: string
  stageRuns: StageRun[]
  costs: CostRow[]
}

export interface Provider {
  id: string
  name: string
  kind: string
  capabilities: string
  config: string
  enabled: boolean
}

export interface StylePreset { id: string; name: string; description: string; defaults: Record<string, unknown> }

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ''}`)
  }
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  system: () => call<{ planner: string }>('GET', '/system'),
  stages: () => call<StageInfo[]>('GET', '/stages'),
  styles: () => call<StylePreset[]>('GET', '/styles'),
  providers: () => call<Provider[]>('GET', '/providers'),
  ledger: () => call<Record<string, unknown>[]>('GET', '/ledger'),
  videos: () => call<VideoSummary[]>('GET', '/videos'),
  video: (id: string) => call<VideoDetail>('GET', `/videos/${id}`),
  createVideo: (prompt: string, defaultMode: Mode, modes: Record<string, Mode>) =>
    call<{ id: string }>('POST', '/videos', { prompt, defaultMode, modes }),
  approve: (id: string, stage: string, notes?: string) =>
    call<void>('POST', `/videos/${id}/stages/${stage}/approve`, { notes: notes ?? null }),
  reject: (id: string, stage: string, notes: string) =>
    call<void>('POST', `/videos/${id}/stages/${stage}/reject`, { notes }),
  setMode: (id: string, stage: string, mode: Mode) => call<void>('PUT', `/videos/${id}/modes/${stage}`, { mode }),
  setAllModes: (id: string, mode: Mode) => call<void>('PUT', `/videos/${id}/modes`, { mode }),
  pause: (id: string) => call<void>('POST', `/videos/${id}/pause`),
  resume: (id: string) => call<void>('POST', `/videos/${id}/resume`),
}
