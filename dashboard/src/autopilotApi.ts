// Client for the daily autopilot API (/api/autopilot).
import type { DreamMode, DreamStep } from './dreamApi'
import { request } from './http'

export type Privacy = 'private' | 'unlisted' | 'public'

export interface Settings {
  enabled: boolean; startTime: string; startMode: 'AUTO' | 'REVIEW' | 'MANUAL'; reviewMinutes: number; dailyCapInr: number
  stepModes: Record<DreamStep, DreamMode>; look: string | null; shots: number; useScout: boolean; privacy: Privacy
}
export interface Status { enabled: boolean; zone: string; now: string; waitingOn: string[]; ideasLeft: number; youtubeConnected: boolean }
export interface Idea { id: string; idea: string; look: string | null; source: 'owner' | 'scout'; used_at: string | null }
export interface Run {
  id: string; run_date: string; status: string; message: string | null; idea: string | null; dream_short_id: string | null
  title: string | null; description: string | null; hashtags: string | null; privacy: Privacy | null; youtube_id: string | null
  final_asset_id: string | null; spent_inr: number; starts_at: string | null
}

const call = <T>(method: string, path: string, body?: unknown) => request<T>(method, `/api/autopilot${path}`, body)

export const autopilotApi = {
  status: () => call<Status>('GET', ''),
  settings: () => call<Settings>('GET', '/settings'),
  save: (s: Settings) => call<Settings>('PUT', '/settings', s),
  ideas: () => call<Idea[]>('GET', '/ideas'),
  addIdea: (idea: string, look: string | null) => call<{ id: string }>('POST', '/ideas', { idea, look }),
  deleteIdea: (id: string) => call<void>('DELETE', `/ideas/${id}`),
  runs: () => call<Run[]>('GET', '/runs'),
  start: (id: string) => call<void>('POST', `/runs/${id}/start`),
  post: (id: string, b: { title: string; description: string; hashtags: string; privacy: Privacy }) => call<void>('POST', `/runs/${id}/post`, b),
  dismiss: (id: string) => call<void>('POST', `/runs/${id}/dismiss`),
  retryKit: (id: string) => call<void>('POST', `/runs/${id}/retry-kit`),
}

