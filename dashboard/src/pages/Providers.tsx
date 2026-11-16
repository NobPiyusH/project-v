import { api } from '../api'
import { usePoll } from '../hooks'

export function Providers() {
  const { data: providers, error } = usePoll(api.providers, 10000)
  const { data: styles } = usePoll(api.styles, 60000)

  return (
    <>
      <section className="card">
        <div className="card-head">
          <h2>Providers</h2>
          <span className="pill">From config/providers.yaml · routing arrives in M3</span>
        </div>
        {error && <p className="error">{error}</p>}
        {providers && (
          <table>
            <thead><tr><th>Provider</th><th>Kind</th><th className="hide-sm">Can do</th><th>Enabled</th></tr></thead>
            <tbody>
              {providers.map((p) => (
                <tr key={p.id}>
                  <td><strong>{p.name}</strong><div className="muted small"><code>{p.id}</code></div></td>
                  <td>{p.kind}</td>
                  <td className="hide-sm small">{(JSON.parse(p.capabilities) as string[]).join(', ')}</td>
                  <td>{p.enabled ? <span className="badge b-ok">On</span> : <span className="badge b-idle">Off</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section className="card">
        <div className="card-head"><h2>Style presets</h2><span className="pill">From config/style-presets.yaml</span></div>
        {styles?.map((s) => (
          <div key={s.id} style={{ marginBottom: 12 }}>
            <strong>{s.name}</strong> <code className="muted">{s.id}</code>
            <div className="muted small">{s.description}</div>
          </div>
        ))}
      </section>
    </>
  )
}
