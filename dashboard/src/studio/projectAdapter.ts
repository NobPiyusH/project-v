// The one place that knows how a Video and a Dream Short differ. The new Studio screens only see `ProjectRow`
// (list) and `ProjectView` (one project page); every action goes through the functions at the bottom, which call
// today's per-product endpoints. No backend change (docs/studio-redesign-plan.md, Phase 1).
import { api, type CostRow, type Mode, type ProducerDecision, type Production, type StageInfo, type VideoDetail, type VideoSummary } from '../api'
import type { Run } from '../autopilotApi'
import { DREAM_STEPS, STEP_LABEL, dreamApi, type DreamDetail, type DreamMode, type DreamStep, type DreamSummary } from '../dreamApi'
import { latestRuns } from '../pages/stageRuns'

export type Kind = 'short' | 'video'

/** The shared stage vocabulary (plan 3.2). Fine-grained Video stages are sub-steps of one of these. */
export const SHARED_STAGES = ['IDEA', 'SCRIPT', 'VOICE', 'MUSIC', 'PICTURES', 'CLIPS', 'EDIT', 'PACKAGE', 'PUBLISH'] as const
export type SharedStage = (typeof SHARED_STAGES)[number]
export const STAGE_LABEL: Record<SharedStage, string> = {
  IDEA: 'Idea', SCRIPT: 'Script', VOICE: 'Voice', MUSIC: 'Music', PICTURES: 'Pictures', CLIPS: 'Clips', EDIT: 'Edit', PACKAGE: 'Package', PUBLISH: 'Publish',
}

const VIDEO_STAGE_MAP: Record<string, SharedStage> = {
  PLAN: 'IDEA', RESEARCH: 'IDEA', CONCEPT: 'IDEA', SCRIPT: 'SCRIPT', NARRATION: 'VOICE', MUSIC: 'MUSIC',
  SHOT_PLAN: 'PICTURES', KEYFRAMES: 'PICTURES', ANIMATIC: 'PICTURES',
  LIBRARY_MATCH: 'CLIPS', DRAFT_GEN: 'CLIPS', FINAL_GEN: 'CLIPS', CRITIC: 'CLIPS', TAKE_SELECTION: 'CLIPS',
  UPSCALE: 'EDIT', AUDIO: 'EDIT', ASSEMBLY: 'EDIT', ROUGH_CUT_REVIEW: 'EDIT', PACKAGING: 'PACKAGE', FINAL_APPROVAL: 'PUBLISH', PUBLISH: 'PUBLISH',
}
const DREAM_STEP_MAP: Record<DreamStep, SharedStage> = {
  SCOUT: 'IDEA', WRITE: 'SCRIPT', PICTURES: 'PICTURES', CLIPS: 'CLIPS', MUSIC: 'MUSIC', FINISH: 'EDIT',
}

export type StageState = 'done' | 'running' | 'waiting' | 'failed' | 'skipped' | 'pending'
export type RowStatus = 'needs' | 'running' | 'done' | 'failed' | 'stopped'

/** One line of the Studio list. */
export interface ProjectRow {
  kind: Kind; id: string; title: string; idea: string; status: RowStatus; statusText: string
  costInr: number; createdAt: string; thumbAssetId: string | null; href: string
  /** Autopilot run waiting to be posted (Shorts only). */
  readyToPost: boolean
}

const projectHref = (kind: Kind, id: string) => `#/p/${kind}/${id}`
const num = (v: unknown) => Number(v) || 0

// ---------------------------------------------------------------- list

export function videoRow(v: VideoSummary, stages: StageInfo[]): ProjectRow {
  const label = stages.find((s) => s.id === v.current_stage)?.label ?? 'a stage'
  const [status, statusText]: [RowStatus, string] =
    v.status === 'WAITING' ? ['needs', `Waiting for you: ${label}`]
      : v.status === 'RUNNING' ? ['running', `Working on ${label.toLowerCase()}`]
        : v.status === 'PAUSED' ? ['needs', `Paused at ${label.toLowerCase()}`]
          : v.status === 'COMPLETED' ? ['done', v.master_asset_id ? `Done${v.shots ? ` · ${v.shots} shots` : ''}` : 'Done']
            : v.status === 'CANCELLED' ? ['stopped', 'Cancelled']
              : v.status === 'FAILED' ? ['failed', 'Failed'] : ['running', v.status.toLowerCase()]
  return {
    kind: 'video', id: v.id, title: v.title ?? v.prompt, idea: v.prompt, status, statusText, costInr: num(v.spent_inr),
    createdAt: v.created_at, thumbAssetId: v.thumb_asset_id ?? null, href: projectHref('video', v.id), readyToPost: false,
  }
}

export function shortRow(d: DreamSummary, runs: Run[]): ProjectRow {
  const step = d.step ? STEP_LABEL[d.step as DreamStep]?.toLowerCase() ?? d.step.toLowerCase() : 'next step'
  const ready = runs.some((r) => r.dream_short_id === d.id && r.status === 'READY')
  const [status, statusText]: [RowStatus, string] =
    d.status === 'WAITING' ? ['needs', `Waiting for you: approve ${step}`]
      : d.status === 'WAITING_REVIEW' ? ['needs', `In review: ${step}`]
        : d.status === 'WAITING_COST' ? ['needs', 'Waiting for you: approve the clip cost']
          : d.status === 'WAITING_BATCH' ? ['running', 'In the batch queue']
            : d.status === 'FAILED_WAITING' ? ['failed', `Failed at ${step}: retry`]
              : d.status === 'RUNNING' ? ['running', `Working on ${step}`]
                : d.status === 'COMPLETED' ? ['done', ready ? 'Done · ready to post' : 'Done']
                  : d.status === 'CANCELLED' ? ['stopped', 'Cancelled'] : ['running', d.status.toLowerCase()]
  return {
    kind: 'short', id: d.id, title: d.title ?? d.idea, idea: d.idea, status, statusText, costInr: num(d.spent_inr),
    createdAt: d.created_at, thumbAssetId: d.thumb_asset_id, href: projectHref('short', d.id), readyToPost: ready,
  }
}

// ---------------------------------------------------------------- one project

export interface StageView {
  key: SharedStage; label: string; state: StageState; summary: string | null
  /** The mode of this stage; null when the product has no such stage. */
  mode: Mode | null
  /** The product's own stage ids behind it (Video: several; Short: one). Mode changes apply to all of them. */
  nativeIds: string[]
}

export type NextKind = 'approve' | 'retry' | 'cost' | 'batch'
/** The single thing that needs the owner. */
export interface NextAction {
  kind: NextKind; stage: SharedStage; nativeStage: string; title: string; detail: string | null; deadline: string | null
  /** AI Producer's advice for this stage, when there is one. */
  producer: { verdict: string; summary: string | null } | null
  canRedo: boolean; canContinueWithout: boolean
  /** Video's "Reject & redo" needs a note (the stage learns from it); a Short's redo does not. */
  redoNeedsNote?: boolean
}

export interface CostLine { label: string; inr: number; estimated: boolean }
export interface Output { label: string; assetId: string; type: 'image' | 'video' | 'audio' }

export interface ProjectView {
  kind: Kind; id: string; title: string; subtitle: string; statusText: string; live: boolean; vertical: boolean
  costInr: number; costLines: CostLine[]; stages: StageView[]; next: NextAction | null
  /** The player: the final cut, else the latest preview. */
  playerAssetId: string | null; playerIsFinal: boolean; downloads: { label: string; assetId: string; file: string }[]
  outputs: Output[]; reviewMinutes: number; allMode: Mode | null
}

const DONE_VIDEO = new Set(['AUTO_PASSED', 'APPROVED', 'AUTO_APPROVED'])

function videoStageState(group: string[], runs: Record<string, ReturnType<typeof latestRuns>[string]>, v: VideoDetail): StageState {
  const rs = group.map((id) => runs[id]).filter(Boolean)
  if (rs.length === 0) return 'pending'
  if (rs.some((r) => r.status === 'FAILED' || r.status === 'INSPECTION_FAILED')) return 'failed'
  if (rs.some((r) => r.status === 'WAITING_APPROVAL')) return 'waiting'
  if (rs.some((r) => r.status === 'RUNNING' || r.status === 'REJECTED')) return 'running'
  if (rs.every((r) => r.status === 'SKIPPED')) return 'skipped'
  if (rs.length === group.length || v.status === 'COMPLETED') return rs.every((r) => DONE_VIDEO.has(r.status) || r.status === 'SKIPPED') ? 'done' : 'running'
  return group.includes(v.current_stage ?? '') ? 'running' : 'pending'
}

const sum = (rows: CostRow[]) => rows.reduce((s, c) => s + num(c.amount_inr), 0)

/** Group the cost ledger into a short readable list: provider + the first word of the note. */
export function costLines(rows: { provider_id: string; amount_inr: number; estimated: boolean; note: string | null }[]): CostLine[] {
  const m = new Map<string, CostLine>()
  for (const c of rows) {
    const tag = (c.note ?? '').split(/[ (:]/)[0]
    const label = tag ? `${tag} · ${c.provider_id}` : c.provider_id
    const cur = m.get(label) ?? { label, inr: 0, estimated: false }
    cur.inr += num(c.amount_inr)
    cur.estimated ||= c.estimated
    m.set(label, cur)
  }
  return [...m.values()].sort((a, b) => b.inr - a.inr)
}

function parseModes(json: string): { default: Mode; stages: Record<string, Mode> } {
  try { return JSON.parse(json || '{}') as { default: Mode; stages: Record<string, Mode> } } catch { return { default: 'AUTO', stages: {} } }
}

export function videoProject(v: VideoDetail, p: Production | null, stages: StageInfo[], producer: ProducerDecision[]): ProjectView {
  const runs = latestRuns(v.stageRuns)
  const modes = parseModes(v.modes_json)
  const modeOf = (id: string): Mode => modes.stages?.[id] ?? modes.default ?? 'AUTO'
  const groups = new Map<SharedStage, string[]>()
  for (const s of stages) {
    const k = VIDEO_STAGE_MAP[s.id]
    if (k) groups.set(k, [...(groups.get(k) ?? []), s.id])
  }
  const view: StageView[] = SHARED_STAGES.map((key) => {
    const ids = groups.get(key) ?? []
    const state = ids.length ? videoStageState(ids, runs, v) : 'skipped'
    const active = ids.map((id) => runs[id]).find((r) => r && ['WAITING_APPROVAL', 'FAILED', 'RUNNING'].includes(r.status))
    return { key, label: STAGE_LABEL[key], state, summary: (active ?? runs[ids[0]])?.summary ?? null, mode: ids.length ? modeOf(ids[0]) : null, nativeIds: ids }
  })
  // the first stage, in pipeline order, that needs the owner
  const waiting = stages.find((s) => ['WAITING_APPROVAL', 'FAILED'].includes(runs[s.id]?.status ?? ''))
  let next: NextAction | null = null
  if (waiting && v.status !== 'COMPLETED' && v.status !== 'CANCELLED') {
    const r = runs[waiting.id]
    const failed = r.status === 'FAILED'
    const rec = producer.filter((d) => d.stage === waiting.id).sort((a, b) => b.attempt - a.attempt)[0]
    next = {
      kind: failed ? 'retry' : 'approve', stage: VIDEO_STAGE_MAP[waiting.id] ?? 'IDEA', nativeStage: waiting.id,
      title: failed ? `${waiting.label} failed` : `${waiting.label} is ready for you`, detail: r.summary, deadline: r.review_deadline,
      producer: rec && !rec.owner_decision ? { verdict: rec.verdict, summary: rec.summary } : null, canRedo: !failed, canContinueWithout: failed, redoNeedsNote: true,
    }
  }
  const finished = v.status === 'COMPLETED' || v.status === 'CANCELLED'
  const animatic = p?.animaticAssetId ?? null
  const outputs: Output[] = []
  for (const s of p?.shots ?? []) {
    if (s.keyframe_asset_id) outputs.push({ label: `Shot ${s.ord + 1}`, assetId: s.keyframe_asset_id, type: 'image' })
  }
  for (const s of p?.shots ?? []) {
    const c = s.final_clip_asset_id ?? s.draft_clip_asset_id
    if (c) outputs.push({ label: `Clip ${s.ord + 1}`, assetId: c, type: 'video' })
  }
  if (p?.music?.asset_id) outputs.push({ label: 'Music', assetId: p.music.asset_id, type: 'audio' })
  const downloads: ProjectView['downloads'] = []
  if (v.master_asset_id) downloads.push({ label: 'Download MP4', assetId: v.master_asset_id, file: 'master.mp4' })
  if (v.master_package_asset_id) downloads.push({ label: 'Premiere', assetId: v.master_package_asset_id, file: 'premiere_package.zip' })
  if (v.master_captions_asset_id) downloads.push({ label: 'SRT', assetId: v.master_captions_asset_id, file: 'captions.srt' })
  const plan = v.plan_json ? (JSON.parse(v.plan_json) as { plan?: { durationSec?: number; aspectRatio?: string; stylePreset?: string } }).plan : undefined
  return {
    kind: 'video', id: v.id, title: v.title ?? 'Untitled video', subtitle: [
      'Video', plan?.durationSec ? `${plan.durationSec} s` : null, plan?.aspectRatio, plan?.stylePreset, p?.shots.length ? `${p.shots.length} shots` : null,
    ].filter(Boolean).join(' · '),
    statusText: v.status === 'COMPLETED' ? 'Done' : v.status === 'CANCELLED' ? 'Cancelled' : v.status === 'PAUSED' ? 'Paused' : next ? 'Waiting for you' : 'Working',
    live: !finished, vertical: (v.plan_json ?? '').includes('"9:16"'), costInr: num(v.spent_inr), costLines: costLines(v.costs), stages: view, next,
    playerAssetId: v.master_asset_id ?? animatic, playerIsFinal: !!v.master_asset_id, downloads, outputs, reviewMinutes: v.review_minutes, allMode: modes.default ?? null,
  }
}

function dreamModes(d: DreamDetail): Record<DreamStep, DreamMode> | undefined {
  if (d.view?.modes) return d.view.modes
  return typeof d.modes_json === 'object' && d.modes_json ? (d.modes_json as Record<DreamStep, DreamMode>) : undefined
}

export function shortProject(d: DreamDetail, runs: Run[]): ProjectView {
  const status = d.view?.status ?? d.status
  const step = (d.view?.step ?? d.step) as DreamStep | null
  const modes = dreamModes(d)
  const idx = step ? DREAM_STEPS.indexOf(step) : -1
  const completed = status === 'COMPLETED'
  const run = runs.find((r) => r.dream_short_id === d.id)
  const stateOf = (s: DreamStep): StageState => {
    const t = d.timeline?.find((x) => x.step === s)
    const i = DREAM_STEPS.indexOf(s)
    if (s === step && !completed) {
      return status === 'RUNNING' || status === 'WAITING_BATCH' ? 'running' : status === 'FAILED_WAITING' ? 'failed' : status === 'CANCELLED' ? 'pending' : 'waiting'
    }
    if (t?.status === 'DONE' || (completed && t?.status !== 'PENDING')) return 'done'
    if (t?.status === 'PENDING') return completed || i < idx ? 'skipped' : 'pending' // a step that never ran (e.g. Scout off)
    return i < idx || completed ? 'done' : 'pending'
  }
  const view: StageView[] = SHARED_STAGES.map((key) => {
    const native = DREAM_STEPS.find((s) => DREAM_STEP_MAP[s] === key)
    if (native) {
      return { key, label: STAGE_LABEL[key], state: stateOf(native), mode: (modes?.[native] ?? 'AUTO') as Mode, nativeIds: [native],
        summary: native === step ? (d.view?.message ?? d.message) : null }
    }
    if (key === 'VOICE') return { key, label: 'Voice', state: 'skipped', summary: 'Shorts have no narration yet', mode: null, nativeIds: [] }
    if (key === 'PACKAGE') return { key, label: 'Package', state: completed ? 'done' : 'pending', summary: null, mode: null, nativeIds: [] }
    const st: StageState = run?.status === 'POSTED' ? 'done' : run?.status === 'READY' ? 'waiting' : 'pending'
    return { key, label: 'Publish', state: st, summary: run?.status === 'READY' ? 'Ready to post (Autopilot)' : null, mode: null, nativeIds: [] }
  })
  let next: NextAction | null = null
  const label = step ? STEP_LABEL[step] : 'step'
  if (step && (status === 'WAITING' || status === 'WAITING_REVIEW')) {
    next = { kind: 'approve', stage: DREAM_STEP_MAP[step], nativeStage: step, title: `${label} is ready for you`, detail: d.view?.message ?? d.message,
      deadline: status === 'WAITING_REVIEW' ? d.view?.deadline ?? null : null, producer: null, canRedo: true, canContinueWithout: false }
  } else if (status === 'WAITING_COST') {
    next = { kind: 'cost', stage: 'CLIPS', nativeStage: 'CLIPS', title: 'Clip cost needs your OK', detail: d.view?.message ?? d.message, deadline: null, producer: null, canRedo: false, canContinueWithout: false }
  } else if (status === 'WAITING_BATCH' && step) {
    next = { kind: 'batch', stage: DREAM_STEP_MAP[step], nativeStage: step, title: `${label} is in the batch queue`, detail: d.view?.message ?? d.message, deadline: null, producer: null, canRedo: false, canContinueWithout: false }
  } else if (status === 'FAILED_WAITING' && step) {
    next = { kind: 'retry', stage: DREAM_STEP_MAP[step], nativeStage: step, title: `${label} failed`, detail: d.view?.message ?? d.message, deadline: null, producer: null, canRedo: false, canContinueWithout: false }
  }
  const outputs: Output[] = []
  for (const s of d.shots) if (s.picture_asset_id) outputs.push({ label: `Shot ${s.ord + 1}`, assetId: s.picture_asset_id, type: 'image' })
  for (const s of d.shots) if (s.clip_asset_id) outputs.push({ label: `Clip ${s.ord + 1}`, assetId: s.clip_asset_id, type: 'video' })
  if (d.music_asset_id) outputs.push({ label: d.music_source === 'lyria' ? 'Music (Lyria)' : 'Music (your track)', assetId: d.music_asset_id, type: 'audio' })
  const first = d.shots.find((s) => s.clip_asset_id)?.clip_asset_id ?? null
  const live = status !== 'COMPLETED' && status !== 'CANCELLED'
  return {
    kind: 'short', id: d.id, title: d.title ?? d.idea, subtitle: ['Short', d.look, `${d.shot_count} shots`, d.quality].join(' · '),
    statusText: ({ RUNNING: 'Working', WAITING: 'Waiting for you', WAITING_REVIEW: 'In review', WAITING_COST: 'Cost to approve', WAITING_BATCH: 'Batch queue',
      FAILED_WAITING: 'Failed: waiting for you', COMPLETED: 'Done', CANCELLED: 'Cancelled' } as Record<string, string>)[status] ?? status,
    live, vertical: true, costInr: num(d.spent_inr) || sum(d.costs as unknown as CostRow[]), costLines: costLines(d.costs), stages: view, next,
    playerAssetId: d.final_asset_id ?? first, playerIsFinal: !!d.final_asset_id,
    downloads: d.final_asset_id ? [{ label: 'Download MP4', assetId: d.final_asset_id, file: 'short.mp4' }] : [], outputs, reviewMinutes: d.review_minutes,
    allMode: modes ? (Object.values(modes).every((m) => m === Object.values(modes)[0]) ? (Object.values(modes)[0] as Mode) : null) : null,
  }
}

// ---------------------------------------------------------------- actions (one interface, per-product calls behind it)

export interface Actions {
  approve: (next: NextAction, note: string) => Promise<void>
  redo: (next: NextAction, note: string, shotIds: string[]) => Promise<void>
  retry: (next: NextAction, note: string) => Promise<void>
  continueWithout: (next: NextAction, note: string) => Promise<void>
  setStageMode: (stage: StageView, mode: Mode) => Promise<void>
  setAllModes: (mode: Mode) => Promise<void>
}

export const videoActions = (id: string): Actions => ({
  approve: (n, note) => api.approve(id, n.nativeStage, note || undefined),
  redo: (n, note) => api.reject(id, n.nativeStage, note),
  retry: (n, note) => api.reject(id, n.nativeStage, note || 'Retry'),
  continueWithout: (n, note) => api.approve(id, n.nativeStage, note || undefined),
  setStageMode: async (s, m) => { for (const nid of s.nativeIds) await api.setMode(id, nid, m) },
  setAllModes: (m) => api.setAllModes(id, m),
})

export const shortActions = (id: string): Actions => ({
  approve: (n) => dreamApi.approve(id, n.kind === 'cost' ? 'CLIPS' : (n.nativeStage as DreamStep)),
  redo: (n, note, shotIds) => dreamApi.redo(id, n.nativeStage as DreamStep, shotIds, note),
  retry: () => dreamApi.retry(id),
  continueWithout: () => Promise.reject(new Error('A Short cannot skip a failed step yet.')),
  setStageMode: async (s, m) => { for (const nid of s.nativeIds) await dreamApi.setMode(id, nid as DreamStep, m as DreamMode) },
  setAllModes: async (m) => { for (const s of DREAM_STEPS) await dreamApi.setMode(id, s, m as DreamMode) },
})
