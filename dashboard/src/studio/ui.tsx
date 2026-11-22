import { STATE_TEXT } from './format'
import { SHARED_STAGES, type StageView } from './projectAdapter'

/** The shared stage timeline: one dot per stage; tap a stage to jump to it (Advanced) via `onPick`. */
export function Timeline({ stages, onPick }: { stages: StageView[]; onPick?: (s: StageView) => void }) {
  return (
    <div className="sv-tl" role="list" aria-label="Stage timeline">
      {SHARED_STAGES.map((k) => {
        const s = stages.find((x) => x.key === k)
        if (!s) return null
        return (
          <button key={k} type="button" role="listitem" className={`sv-stg ${s.state}`} title={s.summary ?? undefined}
            onClick={() => onPick?.(s)}><i />{s.label}<small>{STATE_TEXT[s.state] || ' '}</small></button>
        )
      })}
    </div>
  )
}
