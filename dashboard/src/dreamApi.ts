// Client for the Dream Shorts API (/api/dream). Kept apart from api.ts so the main studio's client is untouched.

export type DreamMode = 'AUTO' | 'REVIEW' | 'MANUAL' | 'PRODUCER'
export type DreamStep = 'SCOUT' | 'WRITE' | 'PICTURES' | 'CLIPS' | 'MUSIC' | 'FINISH'
export const DREAM_STEPS: DreamStep[] = ['SCOUT', 'WRITE', 'PICTURES', 'CLIPS', 'MUSIC', 'FINISH']

export interface DreamSummary {
  id: string; idea: string; title: string | null; look: string; status: string; step: string | null
  message: string | null; final_asset_id: string | null; thumb_asset_id: string | null; spent_inr: number
  created_at: string; updated_at: string
}

export interface DreamShot {
  id: string; ord: number; scene: string; picture_prompt: string; motion_prompt: string
  picture_asset_id: string | null; clip_asset_id: string | null; picture_takes: number; clip_takes: number
  error: string | null
}

export interface DreamDetail extends DreamSummary {
  shot_count: number; quality: string; review_minutes: number; music_asset_id: string | null; music_source: 'bed' | 'lyria'; scout: boolean; scout_json: string | null; scout_angle: number;
  edit_music: boolean; edit_ambience: boolean; edit_pacing: boolean; check_json: string | null; picture_check_json: string | null; modes_json: string | Record<string, DreamMode>
  shots: DreamShot[]
  costs: { provider_id: string; amount_inr: number; units: number | null; unit_type: string | null; estimated: boolean; note: string; ts: string }[]
  decisions: { stage: string; capability: string; chosen_provider_id: string | null; reason_text: string; created_at: string }[]
  clipProgress: { shotId: string; state: string }[] | null
  view: { step: string | null; status: string; message: string | null; modes: Record<DreamStep, DreamMode>; deadline: string | null } | null
  events: { step: string | null; kind: string; shot_ids: string | null; notes: string | null; data: string | null; created_at: string }[]
  metrics: { asset_id: string; look: string | null; metrics: string; created_at: string }[]
}

export interface NewDream {
  idea: string; look: string; shots: number; quality: string; defaultMode: DreamMode; reviewMinutes: number
  music: 'bed' | 'lyria'
  scout: boolean
  musicEdit: boolean; ambience: boolean; pacing: boolean
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/dream${path}`, {
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

export const dreamApi = {
  list: () => call<DreamSummary[]>('GET', ''),
  get: (id: string) => call<DreamDetail>('GET', `/${id}`),
  create: (d: NewDream) => call<{ id: string }>('POST', '', d),
  approve: (id: string, step: DreamStep) => call<void>('POST', `/${id}/steps/${step}/approve`),
  redo: (id: string, step: DreamStep, shotIds: string[], notes: string) =>
    call<void>('POST', `/${id}/steps/${step}/redo`, { shotIds, notes }),
  setMode: (id: string, step: DreamStep, mode: DreamMode) => call<void>('POST', `/${id}/steps/${step}/mode`, { mode }),
  retry: (id: string) => call<void>('POST', `/${id}/retry`),
  editing: (id: string, e: { musicEdit?: boolean; ambience?: boolean; pacing?: boolean }) => call<void>('POST', `/${id}/editing`, e),
  pickAngle: (id: string, n: number) => call<void>('POST', `/${id}/scout/angle/${n}`),
  rerun: (id: string, from: DreamStep) => call<void>('POST', `/${id}/rerun/${from}`),
  stop: (id: string) => call<void>('POST', `/${id}/stop`),
  cancel: (id: string) => call<void>('POST', `/${id}/cancel`),
  review: (id: string, shotId: string | null, rating: number, note: string) =>
    call<{ id: string }>('POST', `/${id}/reviews`, { shotId, rating, note }),
  lessons: () => call<DreamLesson[]>('GET', '/lessons'),
  lesson: (lessonId: string, l: { rule?: string; target?: LessonTarget; status?: LessonStatus }) =>
    call<void>('POST', `/lessons/${lessonId}`, l),
}

export type LessonTarget = 'WRITER' | 'PICTURE' | 'ALL'
export type LessonStatus = 'PROPOSED' | 'ACTIVE' | 'RETIRED'
export interface DreamLesson {
  id: string; rule: string; target: LessonTarget; status: LessonStatus; created_at: string
  rating: number | null; dream_short_id: string | null; shot_ord: number | null
}
