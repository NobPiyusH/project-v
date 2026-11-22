import { useMemo } from 'react'
import { api, type StageInfo } from '../api'
import { autopilotApi } from '../autopilotApi'
import { dreamApi } from '../dreamApi'
import { usePoll } from '../hooks'
import { shortRow, videoRow, type ProjectRow } from './projectAdapter'

/** Every Video and Short as one list of rows, newest first (polled; shared by the top bar and the Studio list). */
export function useProjects() {
  const videos = usePoll(api.videos, 5000)
  const shorts = usePoll(dreamApi.list, 5000)
  const runs = usePoll(autopilotApi.runs, 30000)
  const stages = usePoll(api.stages, 60000)
  const rows = useMemo<ProjectRow[] | null>(() => {
    if (!videos.data && !shorts.data) return null
    const all = [
      ...(videos.data ?? []).map((v) => videoRow(v, stages.data ?? [])),
      ...(shorts.data ?? []).map((d) => shortRow(d, runs.data ?? [])),
    ]
    return all.sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  }, [videos.data, shorts.data, runs.data, stages.data])
  const error = videos.error && shorts.error ? videos.error : null
  return { rows, error, stages: stages.data as StageInfo[] | null }
}
