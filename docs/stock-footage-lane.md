# Stock footage lane (OFF by default)

Real video clips (never panned stills) from Pexels, Pixabay and Wikimedia Commons, chosen per shot, trimmed to the shot and
mixed into the master with AI clips and diagrams. Researched 22 Nov 2026 from the providers' own pages. **Terms change: re-read
them before relying on this for a monetised channel.**

## Licence summary (what matters for monetised YouTube / Instagram)

| Point | Pexels | Pixabay | Wikimedia Commons |
|---|---|---|---|
| Commercial use | Yes, free | Yes, free ("Content License"; only the full Terms are legally binding) | Yes, per file licence |
| Attribution for the clip | Not required (appreciated) | Not required (appreciated) | **PD/CC0: no. CC BY: yes** (author, licence + link, title, source URL, note edits) |
| Accepted by this lane | all | all | only public domain, CC0, CC BY. **CC BY-SA, NC and ND are skipped** (a cut/composited clip is likely a derivative, share-alike would bind the video) |
| No redistribution of the raw clip | Do not sell or redistribute unaltered, or on other stock/wallpaper sites; do not use as part of a trademark | Do not sell/distribute "standalone" (substantially unchanged); no use as trademark/business name | n/a (licence-dependent) |
| Identifiable people | Must not be shown "in a bad light"/offensively; no implied endorsement. No model-release statement found | Recognisable people in immoral/illegal/misleading use are barred; check third-party and privacy rights yourself | Personality rights are the re-user's job; commercial use of people "may require" consent |
| Trademarks / logos | No implied endorsement by brands shown | **Commercial use of recognisable trademarks/logos/brands is barred** | Re-user's responsibility (trademark law) |
| API | `GET https://api.pexels.com/v1/videos/search`, header `Authorization: <key>`, `query, orientation, size, per_page<=80`; no duration filter on search; files in `video_files[]` (`width,height,fps,link,file_type`), `duration`, `user.name/url`, `url` | `GET https://pixabay.com/api/videos/?key=...`, `q<=100 chars, video_type, min_width, min_height, per_page 3-200`; `videos.{large,medium,small,tiny}.{url,width,height,size}`, `duration`, `user`, `pageURL`; large may be an empty url | `api.php` `generator=search` (`gsrnamespace=6`, `filetype:video`), `imageinfo` `url|size|mime|extmetadata|metadata`; `extmetadata` holds `LicenseShortName, Artist, Credit, LicenseUrl` |
| Rate limits | 200 req/hour, 20,000/month by default (free unlimited if API terms are met; ask Pexels) | 100 req/60 s per key; responses must be cached 24 h | Unidentified clients 10 req/min; with a descriptive User-Agent 200 req/min; max 3 concurrent; honour `Retry-After` |
| Required API credit | A **prominent link to Pexels** wherever Pexels content is used via the API ("Photos provided by Pexels"), and credit the creator when possible | Show where videos come from whenever **search results** are displayed (we show none to viewers) | Descriptive `User-Agent` (no browser UA) |

### Top risks (ranked)
1. **Pexels API link rule.** The licence page says no attribution, the API guidelines ask for a prominent Pexels link. The lane therefore
   always writes a credit line for Pexels clips into `credits.txt` and recommends pasting it into the video description. Not verified that this satisfies Pexels for video.
2. **Identifiable people and brands** in generic stock (the TV explainer shows people watching TV and, possibly, TV logos). Pixabay bars commercial use of recognisable
   brands/logos; neither site gives a model release. The lane prefers queries without brands, but **the owner should eyeball every stock shot** (Review mode is honoured).
3. **Content ID.** Stock clips used by many creators can trigger claims. Not verified in the terms read; keep the provenance row/URL to dispute a claim.
4. **Commons per-file variance.** Licence, author and "restrictions" differ per file; CC BY needs the credit line (written automatically); BY-SA/NC/ND are filtered out.
5. **Terms drift.** Pexels/Pixabay terms can change; the stored provenance has the fetch date and the licence URL.

### Not verified (no real keys, no live calls)
- Everything about the live APIs is from documentation, not from running requests. Fakes only in tests.
- Pexels search `fps` field presence; Pexels/Pixabay caching and hotlinking rules for video (none found; we download once and keep our own copy).
- Pixabay full Terms (Content ID, endorsement) were not read, only the summary page.
- Commons video duration field and derivative (transcode) names: the lane reads `metadata.length` if present and otherwise probes the downloaded file with ffprobe.
- Whether a Pexels prominent link in the description alone satisfies their API rule for a video product.

## How it works
`workers/activities/stock.py` (`stock_fetch` Temporal activity on the media worker):
1. Queries: 2-3 short search queries from the shot description via the studio text model (Gemini, `req.queryModel`); on any failure or no model, deterministic keyword extraction.
2. Search enabled sources that have what they need (Pexels/Pixabay need a key; **missing key = source skipped, no error**). Commons needs none.
3. Rank: orientation match, resolution (>=1080 short side preferred), duration >= shot length, then a cheap text relevance (query words in tags/title). Optional vision check of a preview frame is **not built** (left off; the studio's Gemini reviewer exists in `activities/review.py` if wanted later).
4. Download only the winner (size cap `STOCK_MAX_MB`, default 400), trim to the shot length with FFmpeg (stream copy is not used: re-encoded at CRF 14, original resolution kept so assembly does the Lanczos work), upload to the store.
5. Result carries provenance: source, id, page URL, author, licence, attribution text, fetched-at. **No match, no key, or any error -> `found: false`; the Java side reverts the shot to the AI lane. A shot never fails because of stock.**

Java: the Director may mark `lane: "stock"` (only when `projectv.video.stock-lane` is true). Right after Shot plan, each stock shot is searched/fetched
(inside the Shot plan stage, so Review/Manual gating applies and unmatched shots get normal keyframes afterwards). A match is stored like picked NASA footage
(`shot.footage_asset_id`), so Keyframes, clip generation, upscale and smoothing all skip it, and assembly uses it. Provenance lives in the `provenance` row
(new columns `source_url, source_id, author, attribution_text, fetched_at`, migration V43) and appears in the asset describe output.
At assembly, if any clip needs credit, `credits.txt` is written next to the master (every Pexels clip and every CC BY clip, one line each).

## Switches (all default OFF; read at startup)
| Env | Property | Meaning |
|---|---|---|
| `VIDEO_STOCK_LANE` | `projectv.video.stock-lane` | master switch (backend) |
| `VIDEO_STOCK_PEXELS` / `VIDEO_STOCK_PIXABAY` / `VIDEO_STOCK_COMMONS` | `projectv.video.stock-pexels` etc. | per source (default false) |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | | worker environment (`.env`, never logged) |
| `STOCK_MAX_MB` | | download cap, worker (default 400) |

Enable: put switches + keys in `.env`, restart the control plane (`scripts\start.ps1`) and the Python worker (`uv run python -m workers.main`), re-run Shot plan.
