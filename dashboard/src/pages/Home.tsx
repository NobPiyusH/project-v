import { assetUrl, type StageInfo, type VideoSummary } from '../api'
import { StatusBadge } from '../components'
import { fmtTime } from '../hooks'
import { NewVideo } from './VideoList'

/**
 * Studio home: what needs the owner first (approvals, GPU estimates, failed checks), then a new video, then every
 * video as a card with its first frame.
 */
export function Home({ stages, videos }: { stages: StageInfo[]; videos: VideoSummary[] | null }) {
  const label = (id: string | null) => stages.find((s) => s.id === id)?.label ?? ''
  const waiting = (videos ?? []).filter((v) => v.status === 'WAITING')

  return (
    <>
      {waiting.length > 0 && (
        <section className="card">
          <div className="card-head">
            <h2>Needs you</h2>
            <span className="pill">{waiting.length} waiting</span>
          </div>
          <div className="inbox">
            {waiting.map((v) => (
              <div key={v.id} className="inbox-item">
                {v.thumb_asset_id ? <img src={assetUrl(v.thumb_asset_id)} alt="" loading="lazy" /> : <div className="ph" />}
                <div>
                  <div><strong>{v.title ?? v.prompt}</strong></div>
                  <div className="small">
                    <span className="badge b-warn">{label(v.current_stage) || 'A stage'}</span>{' '}
                    <span className="muted clamp2">{v.current_summary ?? 'Waiting for your decision.'}</span>
                  </div>
                </div>
                <a className="button primary" href={`#/video/${v.id}`}>Open</a>
              </div>
            ))}
          </div>
        </section>
      )}

      <NewVideo stages={stages} />

      <div className="section-title">
        <h2>Videos</h2>
        <span className="muted small">{videos?.length ?? 0}</span>
      </div>
      {videos && videos.length === 0 && <p className="empty card">No videos yet. Write a prompt above to start one.</p>}
      <div className="vgrid">
        {(videos ?? []).map((v) => (
          <a key={v.id} className="vcard" href={`#/video/${v.id}`}>
            <div className="thumb">
              {v.thumb_asset_id && <img src={assetUrl(v.thumb_asset_id)} alt="" loading="lazy" />}
              <StatusBadge status={v.status} />
              {v.master_asset_id ? <span className="tag">Master ready</span>
                : v.current_stage ? <span className="tag">{label(v.current_stage)}</span> : null}
            </div>
            <div className="body">
              <div className="title clamp2">{v.title ?? v.prompt}</div>
              {v.title && v.title !== v.prompt && <div className="muted small clamp2">{v.prompt}</div>}
              <div className="row small muted">
                <span>{fmtTime(v.created_at)}</span>
                {!!v.shots && <span>· {v.shots} shots</span>}
                <span>· ₹{Number(v.spent_inr).toFixed(0)}</span>
              </div>
            </div>
          </a>
        ))}
      </div>
    </>
  )
}
