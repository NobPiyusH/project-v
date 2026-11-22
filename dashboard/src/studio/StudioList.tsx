import { useState } from 'react'
import { assetUrl } from '../api'
import type { ProjectRow } from './projectAdapter'
import { KIND_LABEL, age, inr, statusClass } from './format'

type Filter = 'all' | 'needs' | 'running' | 'done' | 'short' | 'video'
const PAGE = 30

const match = (f: Filter, r: ProjectRow) =>
  f === 'all' ? true : f === 'needs' ? r.status === 'needs' || r.status === 'failed' : f === 'running' ? r.status === 'running'
    : f === 'done' ? r.status === 'done' : r.kind === f

/** One list of every Short and Video: status in plain words, cost, age, and what needs the owner. */
export function StudioList({ rows }: { rows: ProjectRow[] | null }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [shown, setShown] = useState(PAGE)
  if (!rows) return <p className="muted">Loading…</p>
  const needs = rows.filter((r) => match('needs', r)).length
  const list = rows.filter((r) => match(filter, r))
  const chips: [Filter, string][] = [['all', 'All'], ['needs', 'Needs you'], ['running', 'Running'], ['done', 'Done'], ['short', 'Shorts'], ['video', 'Videos']]
  return (
    <>
      <h1>Studio</h1>
      <div className="sv-chips" role="group" aria-label="Filter">
        {chips.map(([k, label]) => (
          <button key={k} type="button" className={`sv-chip ${filter === k ? 'on' : ''}`} aria-pressed={filter === k}
            onClick={() => { setFilter(k); setShown(PAGE) }}>{label}{k === 'needs' && needs > 0 && <span className="count">{needs}</span>}</button>
        ))}
      </div>
      <section className="card sv-list">
        {list.length === 0 && <p className="empty">{rows.length === 0 ? 'Nothing here yet. Start one in Create.' : 'Nothing matches this filter.'}</p>}
        {list.slice(0, shown).map((r) => (
          <a key={`${r.kind}-${r.id}`} className="sv-row" href={r.href}>
            {r.thumbAssetId ? <img className={`thumb ${r.kind === 'video' ? 'wide' : ''}`} src={assetUrl(r.thumbAssetId)} alt="" loading="lazy" />
              : <div className={`thumb ${r.kind === 'video' ? 'wide' : ''}`} aria-hidden="true" />}
            <div className="body">
              <div className="title">{r.title}</div>
              <div className="small muted"><span className="sv-kind">{KIND_LABEL[r.kind]}</span><span className={statusClass(r.status)}>● {r.statusText}</span></div>
            </div>
            <div className="side">
              <div>{inr(r.costInr)}</div><div>{age(r.createdAt)}</div>
              {r.readyToPost && <span className="badge b-ok">Ready to post</span>}
            </div>
          </a>
        ))}
        {list.length > shown && <div style={{ padding: 8, textAlign: 'center' }}><button type="button" onClick={() => setShown((n) => n + PAGE)}>Show more ({list.length - shown})</button></div>}
      </section>
    </>
  )
}
