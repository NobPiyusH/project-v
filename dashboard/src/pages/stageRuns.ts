import type { StageRun } from '../api'

/** The newest attempt of each stage. */
export function latestRuns(runs: StageRun[]): Record<string, StageRun> {
  const out: Record<string, StageRun> = {}
  for (const r of runs) {
    const cur = out[r.stage]
    if (!cur || r.attempt >= cur.attempt) out[r.stage] = r
  }
  return out
}

