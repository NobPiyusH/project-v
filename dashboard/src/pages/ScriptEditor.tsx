import { useState } from 'react'
import { api, assetUrl, type ScriptLine } from '../api'

/**
 * Script lines with direct editing and per-line voice redo (owner request):
 * edit any line's text, re-voice one or several lines (optionally in a different style), switch takes for free.
 */
export function ScriptEditor({ videoId, lines, linesNeedingVoice, onChange }: {
  videoId: string; lines: ScriptLine[]; linesNeedingVoice: number; onChange: () => void
}) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [style, setStyle] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async (fn: () => Promise<unknown>) => {
    setError(null)
    setBusy(true)
    try {
      await fn()
      onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  const toggle = (i: number) => setSelected((cur) => {
    const next = new Set(cur)
    if (next.has(i)) next.delete(i)
    else next.add(i)
    return next
  })
  const needing = lines.filter((l) => l.needsVoice).map((l) => l.index)

  return (
    <>
      {linesNeedingVoice > 0 && (
        <div className="notice row">
          <span className="grow">{linesNeedingVoice} edited line{linesNeedingVoice > 1 ? 's' : ''} still {linesNeedingVoice > 1 ? 'have' : 'has'} the old voice.</span>
          <button className="primary" disabled={busy} onClick={() => run(() => api.redoLines(videoId, needing, ''))}>
            Re-voice edited lines (~₹0.5 each)
          </button>
        </div>
      )}
      {selected.size > 0 && (
        <div className="notice" style={{ display: 'grid', gap: 8 }}>
          <strong>Re-voice {selected.size} selected line{selected.size > 1 ? 's' : ''}</strong>
          <input type="text" value={style} onChange={(e) => setStyle(e.target.value)}
            placeholder="Optional reading style, e.g. 'slower and more hushed' (leave empty to keep the current style)" />
          <div className="row">
            <button className="primary" disabled={busy} onClick={() => run(async () => {
              await api.redoLines(videoId, [...selected], style)
              setSelected(new Set())
              setStyle('')
            })}>{busy ? 'Starting…' : 'Re-voice selected'}</button>
            <button onClick={() => setSelected(new Set())}>Clear selection</button>
          </div>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <ol className="script">
        {lines.map((l) => (
          <LineRow key={l.index} videoId={videoId} line={l} selected={selected.has(l.index)} busy={busy}
            onToggle={() => toggle(l.index)} run={run} />
        ))}
      </ol>
    </>
  )
}

function LineRow({ videoId, line, selected, busy, onToggle, run }: {
  videoId: string; line: ScriptLine; selected: boolean; busy: boolean; onToggle: () => void
  run: (fn: () => Promise<unknown>) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(line.text)
  const [spoken, setSpoken] = useState(line.spoken)
  const [showTakes, setShowTakes] = useState(false)
  const redoing = line.voiceStatus === 'REDOING'
  const versions = line.audioVersions ?? []
  const changed = text.trim() !== line.text || spoken !== line.spoken

  const save = (revoice: boolean) => run(async () => {
    await api.editLine(videoId, line.index, text, spoken)
    if (revoice && spoken) await api.redoLines(videoId, [line.index], '')
    setEditing(false)
  })

  return (
    <li className={line.spoken ? '' : 'visual'}>
      <div className="row" style={{ alignItems: 'flex-start' }}>
        {line.spoken && (
          <input type="checkbox" checked={selected} onChange={onToggle} disabled={redoing}
            aria-label={`Select line ${line.index + 1}`} style={{ marginTop: 4 }} />
        )}
        <div className="grow">
          {editing ? (
            <div style={{ display: 'grid', gap: 6 }}>
              <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label={`Edit line ${line.index + 1}`} />
              <label className="row small">
                <input type="checkbox" checked={spoken} onChange={(e) => setSpoken(e.target.checked)} /> Spoken (uncheck for a visual-only beat)
              </label>
              <div className="row">
                {spoken && <button className="primary" disabled={busy || !changed} onClick={() => save(true)}>Save & re-voice</button>}
                <button disabled={busy || !changed} onClick={() => save(false)}>Save only</button>
                <button onClick={() => { setEditing(false); setText(line.text); setSpoken(line.spoken) }}>Cancel</button>
              </div>
            </div>
          ) : (
            <div>{line.spoken ? line.text : <em>[visual] {line.text}</em>}</div>
          )}
          <div className="row small" style={{ marginTop: 4 }}>
            {redoing && <span className="badge b-info">Re-voicing…</span>}
            {line.needsVoice && <span className="badge b-warn">Needs voice</span>}
            {line.voiceStatus === 'IMPERFECT' && <span className="badge b-warn" title={line.check ?? ''}>Voice check: imperfect</span>}
            {line.voiceStatus === 'FAILED' && <span className="badge b-bad" title={line.check ?? ''}>Re-voice failed</span>}
            {line.edited && <span className="muted" title={line.originalText ? `Original: ${line.originalText}` : ''}>edited by you</span>}
            {!editing && <button className="small" style={{ padding: '2px 8px' }} onClick={() => setEditing(true)}>Edit</button>}
            {line.spoken && !editing && (
              <button className="small" style={{ padding: '2px 8px' }} disabled={busy || redoing}
                onClick={() => run(() => api.redoLines(videoId, [line.index], ''))}>Re-voice</button>
            )}
            {versions.length > 1 && (
              <button className="small" style={{ padding: '2px 8px' }} onClick={() => setShowTakes((s) => !s)}>
                {showTakes ? 'Hide takes' : `${versions.length} takes`}
              </button>
            )}
          </div>
          {line.audioAssetId && (
            <div className="row small muted">
              <audio key={line.audioAssetId} src={assetUrl(line.audioAssetId)} controls preload="none" />
              {line.durationSec != null && <span>{line.durationSec.toFixed(1)}s</span>}
            </div>
          )}
          {showTakes && (
            <div className="takes">
              {versions.map((v) => (
                <div key={v.assetId} className="row small">
                  <audio src={assetUrl(v.assetId)} controls preload="none" />
                  <span className="muted">{v.durationSec.toFixed(1)}s{v.style ? ` · ${v.style}` : ''}</span>
                  {v.assetId === line.audioAssetId
                    ? <span className="badge b-ok">in use</span>
                    : <button className="small" style={{ padding: '2px 8px' }} disabled={busy}
                        onClick={() => run(() => api.selectLineVoice(videoId, line.index, v.assetId))}>Use this take</button>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </li>
  )
}
