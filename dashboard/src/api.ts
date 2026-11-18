// Typed client for the control-plane REST API (/api, proxied by Vite in dev).

export type Mode = 'AUTO' | 'REVIEW' | 'MANUAL'

export interface StageInfo { id: string; label: string; description: string; redoWith: string[] }

export interface VideoSummary {
  id: string
  title: string | null
  prompt: string
  status: string
  current_stage: string | null
  spent_inr: number
  created_at: string
  updated_at: string
  master_asset_id?: string | null
  thumb_asset_id?: string | null
  shots?: number
  current_summary?: string | null
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
  review_deadline: string | null
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

export interface Finding { severity: 'FAIL' | 'WARN' | 'INFO'; code: string; message: string; ref: string | null }

/** Inspector report for one stage attempt (second-level, independent checks). */
export interface Inspection {
  stage: string
  attempt: number
  verdict: 'PASS' | 'WARN' | 'FAIL'
  findings: Finding[]
  checked: string[]
  created_at: string
}

export interface ShotCritique { shot: number; meaning: number; accuracy: number; continuity: number; literal: boolean; problem: string; fixPrompt: string }
export interface ActCritique { act: string; flow: number; keyMoment: number; continuity: number; mood: number; summary: string; problem: string; shotsToFix: number[] }
export interface VideoCritique { arc: number; hook: number; ending: number; variety: number; summary: string; problem: string }

/** Creative Director report card for one stage (SHOT_PLAN = prompts, KEYFRAMES = the real images). */
export interface CreativeReport {
  stage: 'SHOT_PLAN' | 'KEYFRAMES'
  attempt: number
  model: string | null
  review: { video: VideoCritique | null; acts: ActCritique[]; shots: ShotCritique[] }
  created_at: string
}

export interface VideoDetail extends VideoSummary {
  workflow_id: string
  plan_json: string | null
  modes_json: string
  review_minutes: number
  stageRuns: StageRun[]
  costs: CostRow[]
  inspections: Inspection[]
  master_asset_id: string | null
  master_package_asset_id: string | null
  master_captions_asset_id: string | null
  creativeReviews: CreativeReport[]
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

export interface Source { title: string; uri: string }
export interface Fact { text: string; sources: Source[] }

export interface AudioVersion { assetId: string; uri: string; durationSec: number; createdAt: string; check: string | null; style: string | null }

export interface ScriptLine {
  index: number; text: string; act: string | null; purpose: string | null; spoken: boolean
  audioAssetId: string | null; audioUri: string | null; durationSec: number | null
  voiceStatus: string | null; needsVoice: boolean | null; edited: boolean | null; originalText: string | null
  audioVersions: AudioVersion[] | null; check: string | null
}

export interface ShotRow {
  id: string; ord: number; duration_target_sec: number; shot_type: string | null; camera_move: string | null
  description: string | null; priority: string; overlay_text: string | null; keyframe_asset_id: string | null
  keyframe_provider: string | null; scene_title: string; status: string; error: string | null
  draft_clip_asset_id: string | null; final_clip_asset_id: string | null; clip_status: string | null
  clip_error: string | null; final_wanted: boolean; footage_asset_id: string | null
  prompt_json: {
    keyframePrompt?: string; motionPrompt?: string; lineIndex?: number | null; startSec?: number; editedByOwner?: boolean
    footageQuery?: string; footageCandidates?: FootageCandidate[]; footageError?: string
  }
}

export interface Production {
  research: { facts: string; queries: string[]; version: number } | null
  concept: { logline: string; bigIdea: string; clockLabel: string; acts: { title: string; purpose: string; approxSec: number }[]; emotionalArc: string } | null
  script: { version: number; lines: ScriptLine[]; voiceName: string; voiceStyle: string; musicBrief: string } | null
  music: { asset_id: string; bpm: number } | null
  shots: ShotRow[]
  animaticAssetId: string | null
  animaticOutdated: boolean
  redoingShots: number
  redoingLines: number
  linesNeedingVoice: number
}

export interface FootageCandidate { nasaId: string; title: string; description: string; credit: string; date: string; thumbUrl: string; previewUrl: string }

export type ClipKind = 'draft' | 'final'

export interface ClipEstimate { kind: ClipKind; clips: number; gpuSeconds: number; inr: number; usd: number; wallMinutes: number; needsApproval: boolean; summary: string }

export interface ClipProgressRow { shotId: string; shotNumber?: number; kind: ClipKind; state: string; step?: number; steps?: number; etaSec?: number | null }

export interface ClipVersion { id: string; kind: ClipKind; created_at: string; model: string | null; active: boolean }

export type ImagePolicy = 'AUTO' | 'PREMIUM' | 'FREE'
export interface ShotVersion { id: string; created_at: string; provider: string; model: string; active: boolean }

export const assetUrl = (id: string) => `/api/assets/${id}/content`

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
  production: (id: string) => call<Production>('GET', `/videos/${id}/production`),
  budget: () => call<{ geminiCapInr: number; geminiSpentInr: number; geminiRemainingInr: number; gpuCapInr: number; gpuSpentInr: number;
    modal?: { meteredUsd: number; creditsUsedUsd: number; billedUsd: number; freeCreditUsd: number; creditLeftUsd: number; fxInrPerUsd: number; fetchedAt: string } }>('GET', '/budget'),
  createVideo: (prompt: string, defaultMode: Mode, modes: Record<string, Mode>, reviewMinutes: number) =>
    call<{ id: string }>('POST', '/videos', { prompt, defaultMode, modes, reviewMinutes }),
  setReviewMinutes: (id: string, minutes: number) => call<void>('PUT', `/videos/${id}/review-minutes`, { minutes }),
  approve: (id: string, stage: string, notes?: string) =>
    call<void>('POST', `/videos/${id}/stages/${stage}/approve`, { notes: notes ?? null }),
  reject: (id: string, stage: string, notes: string) =>
    call<void>('POST', `/videos/${id}/stages/${stage}/reject`, { notes }),
  setMode: (id: string, stage: string, mode: Mode) => call<void>('PUT', `/videos/${id}/modes/${stage}`, { mode }),
  setAllModes: (id: string, mode: Mode) => call<void>('PUT', `/videos/${id}/modes`, { mode }),
  clipEstimate: (id: string, kind: ClipKind, shotIds: string[]) =>
    call<ClipEstimate>('GET', `/videos/${id}/clips/estimate?kind=${kind}${shotIds.map((s) => `&shotIds=${s}`).join('')}`),
  redoClips: (id: string, shotIds: string[], kind: ClipKind) => call<{ workflowId: string }>('POST', `/videos/${id}/clips/redo`, { shotIds, kind }),
  stopClips: (id: string) => call<void>('POST', `/videos/${id}/clips/stop`),
  clipProgress: (id: string) => call<{ clips: ClipProgressRow[]; finished: boolean }>('GET', `/videos/${id}/clips/progress`),
  setFinalWanted: (shotId: string, wanted: boolean) => call<void>('PUT', `/shots/${shotId}/final-wanted?wanted=${wanted}`),
  clipVersions: (shotId: string) => call<ClipVersion[]>('GET', `/shots/${shotId}/clips`),
  selectClip: (shotId: string, assetId: string) => call<void>('PUT', `/shots/${shotId}/clips/${assetId}`),
  footage: (id: string, shotId: string, body: { query?: string; nasaId?: string; startSec?: number | null }) =>
    call<{ workflowId: string }>('POST', `/videos/${id}/shots/${shotId}/footage`, body),
  clearFootage: (shotId: string) => call<void>('DELETE', `/shots/${shotId}/footage`),
  reviewImages: (id: string) => call<{ workflowId: string }>('POST', `/videos/${id}/creative-review`),
  rerun: (id: string, stage: string) => call<{ stages: string[] }>('POST', `/videos/${id}/stages/${stage}/rerun`),
  redoShots: (id: string, shotIds: string[], notes: string, policy: ImagePolicy) =>
    call<{ workflowId: string }>('POST', `/videos/${id}/shots/redo`, { shotIds, notes: notes || null, policy }),
  editShotPrompt: (shotId: string, keyframePrompt: string) => call<void>('PUT', `/shots/${shotId}/prompt`, { keyframePrompt }),
  shotVersions: (shotId: string) => call<ShotVersion[]>('GET', `/shots/${shotId}/versions`),
  selectKeyframe: (shotId: string, assetId: string) => call<void>('PUT', `/shots/${shotId}/keyframe`, { assetId }),
  editLine: (id: string, index: number, text: string, spoken?: boolean) =>
    call<ScriptLine>('PUT', `/videos/${id}/script/lines/${index}`, { text, spoken: spoken ?? null }),
  redoLines: (id: string, lines: number[], style: string) =>
    call<{ workflowId: string }>('POST', `/videos/${id}/script/lines/redo`, { lines, style: style || null }),
  selectLineVoice: (id: string, index: number, assetId: string) =>
    call<void>('PUT', `/videos/${id}/script/lines/${index}/voice`, { assetId }),
  pause: (id: string) => call<void>('POST', `/videos/${id}/pause`),
  resume: (id: string) => call<void>('POST', `/videos/${id}/resume`),
}
