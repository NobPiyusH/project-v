import type { IdeaPack } from '../../dreamApi'

/** The idea pack picker: choose a curated concept seed instead of typing an idea (the Scout is then skipped). */
export function IdeaPicker({ packs, picked, onPick }: {
  packs: IdeaPack[] | null | undefined
  picked: { pack: string; id: string } | null
  onPick: (pack: IdeaPack | null, idea: IdeaPack['ideas'][number] | null) => void
}) {
  if (!packs || packs.length === 0) return null
  const pack = packs.find((p) => p.id === picked?.pack) ?? packs[0]
  const idea = pack.ideas.find((i) => i.id === picked?.id)
  return (
    <div className="small" style={{ marginTop: 8 }}>
      <label>Idea pack <select value={picked ? pack.id : ''} onChange={(e) => {
        const p = packs.find((x) => x.id === e.target.value)
        onPick(p ?? null, p ? p.ideas[0] : null)
      }}>
        <option value="">None (type your own)</option>
        {packs.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.ideas.length})</option>)}</select></label>{' '}
      {picked && <label>Idea <select value={picked.id} onChange={(e) => onPick(pack, pack.ideas.find((i) => i.id === e.target.value) ?? null)}>
        {pack.ideas.map((i) => <option key={i.id} value={i.id}>{i.title}</option>)}</select></label>}
      {picked && idea && <div className="muted">Hook: {idea.hook.replace(/\*/g, '')} · Camera: {idea.camera}</div>}
    </div>
  )
}
