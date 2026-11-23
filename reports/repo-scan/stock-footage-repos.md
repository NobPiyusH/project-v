# Stock-footage lane: repo and source scan (22 Nov 2026)

Method: `gh api` / `gh search repos` for metadata (licence = GitHub's SPDX detection, stars, pushed_at), READMEs read for behaviour, WebFetch for source terms. I did NOT clone, install or run anything. gh hit the shared rate limit near the end, so some rows are marked "not checked". Source-terms pages were summarised by a fetch model, not read in full; confirm legal wording on the original pages before relying on them.

## Recommended picks (max 6)

1. **Reimplement the idea of `edigeh/broll-mcp` (MIT, Internet Archive).** Closest thing to our whole lane done well: brief -> ranked IA search -> PySceneDetect keyframes -> frame-accurate re-encoded cut -> JSON provenance written BEFORE the clip (licence verbatim, commercial_use flag, MD5, attribution text), polite rate limiting (1 req/s, max 4 media connections), SQLite cache. 55 stars, young (Oct 2026), Python 3.12, uv, ffmpeg. Take its provenance schema, politeness rules and re-encode-to-exact-length cut. We do not need its MCP wrapper; vendoring its archive.org module is possible (MIT) but it is a young single-maintainer project.
2. **Reimplement ideas from `harry0703/MoneyPrinterTurbo` `app/services/material.py` (MIT, ~145k stars, pushed today).** Proven Pexels/Pixabay/Coverr search with aspect and duration filtering, key redaction, Cloudflare-challenge and rate-limit handling, per-material source records (creator, public URL). It matches by keyword only (no semantic rerank) and is a whole app (540 MB repo), so do not depend on it. Copy the provider-handling patterns.
3. **USE AS DEPENDENCY: PySceneDetect (`Breakthrough/PySceneDetect`, BSD-3-Clause, v0.7.3 on 8 Nov 2026, pushed today).** Shot boundaries so we trim to clean shots and never cut mid-shot. CPU, OpenCV, fine on Windows, small.
4. **USE AS DEPENDENCY: `mlfoundations/open_clip` (MIT-style licence; GitHub shows NOASSERTION), or SigLIP via Hugging Face `transformers`.** For our own text-to-keyframe reranking: embed 3-5 thumbnails per candidate (stock APIs return thumbnails/preview pictures), cosine against the shot description, keep the best. No existing repo does this against stock APIs, so the reranker is ours (~100 lines). CPU is fine for a few hundred thumbnails; weights roughly 0.4-4 GB depending on model; check each model card's licence.
5. **REFERENCE ONLY: `skyzhao1223/free-for-creators` (CC0, 97 stars, pushed 16 Nov 2026).** JSON directory of 221 free-asset sources with licence, attribution and monetisation columns and weekly link checks. Use it to seed our own sources config; data is CC0. Do not trust it over a source's own licence page.
6. **REFERENCE ONLY: `shanawazvibecode/AutoBroll` (MIT, 4 stars) and `neutraltone/awesome-stock-resources` (CC0, 14.9k stars).** The first shows the minimal transcript -> <=8 s segments -> NLTK keywords -> Pexels -> ffmpeg trim loop (Windows-only DaVinci glue, ignore that part). The second is a link list for discovering more sources (no API or licence analysis).

Overall verdict: no existing open-source repo covers "shot description -> semantically ranked REAL clip across several free APIs with provenance". Build a small stock-footage worker module: provider adapters (Pixabay, Pexels, Coverr, Wikimedia, Internet Archive, NASA), thumbnail CLIP/SigLIP rerank, PySceneDetect-aware trim, one Provenance row per clip. Borrow patterns from picks 1 and 2.

## Verification of the names you were given

| Name | Real? | Notes |
|---|---|---|
| harry0703/MoneyPrinterTurbo | Yes | MIT, ~145k stars, active today. Topic -> script -> stock footage (Pexels, Pixabay, Coverr) + TTS + subtitles + render. Not semantic matching. |
| e1four15f/ClipSeek | Yes | MIT, 9 stars, last push 22 Jan 2025. Text-to-clip retrieval over YOUR own indexed videos: Docker + Milvus + FastAPI + web UI. Heavy; demo-grade. |
| bwconrad/video-content-search | Yes | MIT, 16 stars, last push Apr 2023. Gradio app: ConvNeXt OpenCLIP frames vs text/image query. Trivial to reimplement; stale. |
| kingyiusuen/clip-image-search | Yes | MIT, 268 stars, last push Jan 2022. Image (not video) search with CLIP + Streamlit. Stale, wrong modality. |
| mcp-pexels | Yes, but several same-named repos | npm `mcp-pexels` = developer-ishan/mcp-pexels (MIT, 3 stars, TS, May 2026). Others: garylab/pexels-mcp-server (31 stars, NO licence), pipeworx-io/mcp-pexels (MIT, 0 stars). All thin API wrappers. |
| neutraltone/awesome-stock-resources | Yes | CC0-1.0, 14.9k stars, last push 11 Feb 2026. Link list. |
| "Reelary" | Real, but not a stock fetcher | `reelary/cli` (MIT, 1 star, TS, Nov 2026) and `reelary/skills` (no licence): a signed desktop app (tray icon, Mac + Windows, paid "Pro" tier) that indexes YOUR OWN local footage and exposes an MCP. Unrelated `XicuM/Reelary` is an Instagram-saves tool. SKIP. |

## Full table

Licence flags: **AGPL**, **none** called out in bold. "Stale" = no push for over 12 months.

| Repo / source | URL | What it does | Lang | Licence | Stars | Last push | Maintained | Windows/CPU | Size | Verdict (for us) |
|---|---|---|---|---|---|---|---|---|---|---|
| MoneyPrinterTurbo | github.com/harry0703/MoneyPrinterTurbo | Full topic-to-short pipeline; Pexels/Pixabay/Coverr fetchers, source records | Python | MIT | 145k | 2026-11-22 | Yes | Yes (Windows package exists; CPU ok for stock path) | 540 MB repo | REIMPLEMENT THE IDEA (material.py patterns) |
| edigeh/broll-mcp | github.com/edigeh/broll-mcp | IA public-domain b-roll search, scene keyframes, exact cuts, JSON provenance | Python | MIT | 55 | 2026-10-23 | Yes (young) | Yes (needs ffmpeg with HTTPS, uv, py3.12+) | ~1 MB | REIMPLEMENT THE IDEA (or vendor); best IA reference |
| PySceneDetect | github.com/Breakthrough/PySceneDetect | Shot/scene cut detection | Python | BSD-3-Clause | 5.6k | 2026-11-22 | Yes (v0.7.3) | Yes / CPU | small (+OpenCV) | USE AS DEPENDENCY |
| open_clip | github.com/mlfoundations/open_clip | CLIP/SigLIP-family embedding library | Python | MIT-style (GitHub: NOASSERTION); weights vary | 14.3k | 2026-11-21 | Yes | Yes (CPU ok for thumbnails) | weights 0.4-4 GB | USE AS DEPENDENCY (reranker) |
| HF transformers SigLIP | huggingface.co/google/siglip-base-patch16-224 | Alternative reranker | Python | Apache-2.0 (from memory, not re-verified) | n/a | n/a | n/a | CPU ok | ~0.8 GB | USE AS DEPENDENCY (alternative to open_clip) |
| ClipSeek | github.com/e1four15f/ClipSeek | Text-to-clip search over own videos, Milvus | Python/Svelte | MIT | 9 | 2025-01-22 | Stale | Docker + Milvus; heavy | 119 MB + models | SKIP |
| video-content-search | github.com/bwconrad/video-content-search | CLIP frame search in one video | Python | MIT | 16 | 2023-04-20 | Stale | CPU ok | tiny | REFERENCE ONLY |
| clip-image-search | github.com/kingyiusuen/clip-image-search | CLIP image search | Python | MIT | 268 | 2022-01-15 | Stale | CPU ok | tiny | SKIP |
| mcp-pexels (developer-ishan) | github.com/developer-ishan/mcp-pexels | Pexels MCP server | TS | MIT | 3 | 2026-05-18 | Low | Node 20 | tiny | SKIP (call Pexels REST directly) |
| pexels-mcp-server (garylab) | github.com/garylab/pexels-mcp-server | Pexels MCP | Python | **none** (all rights reserved) | 31 | 2025-11-02 | Stale | n/a | tiny | SKIP |
| pixabay-mcp-server / pixabay-mcp | github.com/Unlock-MCP/pixabay-mcp-server, github.com/zym9863/pixabay-mcp | Pixabay MCP wrappers | JS | MIT | 13 / 10 | 2025-06 / 2026-03 | Low | n/a | tiny | SKIP |
| awesome-stock-resources | github.com/neutraltone/awesome-stock-resources | Link list of free stock sites | Ruby (list) | CC0-1.0 | 14.9k | 2026-02-11 | Slow | n/a | tiny | REFERENCE ONLY |
| free-for-creators | github.com/skyzhao1223/free-for-creators | Licence-annotated directory (221 sources, monetisation column), JSON data | Python | CC0-1.0 | 97 | 2026-11-16 | Yes | n/a | 22 MB | REFERENCE ONLY (seed sources list) |
| AutoBroll | github.com/shanawazvibecode/AutoBroll | Transcript -> Pexels clips -> DaVinci Resolve | Python | MIT | 4 | 2026-06-24 | Low | Windows-only glue (ctypes.windll) | tiny | REFERENCE ONLY |
| good-broll | github.com/hongfamonvAI/good-broll | Agent skill writing image-gen B-roll prompts (not stock) | Python | MIT | 50 | 2026-06-14 | Low | n/a | tiny | SKIP (wrong problem) |
| FootageFlow | github.com/xcslys99/FootageFlow | Desktop app: multi-source footage search + link downloader | Swift | MIT | 134 | 2026-09-17 | Yes | Windows 11 x64 binary; GUI only | 23 MB | REFERENCE ONLY (app, not a library; downloading from arbitrary pasted links is a licence risk) |
| purffle-shorts | github.com/Chamanrajragu/purffle-shorts | Faceless Shorts generator, stock-footage option | Python | MIT | 103 | 2026-11-13 | Yes | Yes | 14 MB | REFERENCE ONLY (overlaps MPT) |
| jjjake/internetarchive | github.com/jjjake/internetarchive | Python/CLI for archive.org (search, metadata, download) | Python | **AGPL-3.0** | 1.9k | 2026-11-17 | Yes | Yes | small | REFERENCE ONLY: AGPL; prefer plain `requests` against advancedsearch + metadata JSON |
| internetarchive-downloader | github.com/john-corcoran/internetarchive-downloader | Resumable hash-verified IA downloader | Python | MIT | 184 | 2024-01-08 | Stale | Yes | small | REFERENCE ONLY |
| internet-archive-video-downloader | github.com/disappointingsupernova/internet-archive-video-downloader | Bash IA video downloader | Shell | MIT | 0 | 2026-11-20 | n/a | bash only | tiny | SKIP |
| VideoSeek | github.com/6v17/VideoSeek | Offline semantic search over local video library, agent API | Python | **AGPL-3.0** | 131 | 2026-11-20 | Yes | not checked | 56 MB | SKIP (AGPL, local-library oriented) |
| pireel | github.com/pireel/pireel | AI video editor with b-roll, Premiere export | TS | **AGPL-3.0** | 1.5k | 2026-11-18 | Yes | not checked | not checked | SKIP (AGPL; overlaps our editor) |
| hypit | github.com/hypit-ai/hypit | "Clone viral video" agent workflow | TS | custom ("other") | 31k | 2026-11-20 | Yes | not checked | not checked | SKIP (custom licence, wrong goal; not examined deeply) |
| video-cut-tool | github.com/gopavasanth/video-cut-tool | Wikimedia Commons video trim web tool | JS | NOASSERTION | 54 | 2023-03-15 | Stale | web service | 11 MB | REFERENCE ONLY (we just ffmpeg-trim) |
| X-CLIP, ClipBERT, DRL and similar | e.g. github.com/xuguohai/X-CLIP | Academic text-video retrieval | Python | MIT/Apache | 16-731 | 2022-2025 | Stale | training/GPU oriented | large | SKIP (open_clip on thumbnails is enough) |
| Reelary (cli, skills) | github.com/reelary/cli | Local footage indexer app + MCP | TS | MIT (cli) / **none** (skills) | 1 | 2026-11-07 | Young | Win+Mac closed binary | cli tiny, app separate | SKIP |

Not examined in depth: gbro/eian collage-b-roll skills (generate AI collage B-roll; irrelevant to real-footage retrieval).

## Free commercial-use video sources with APIs

Terms from source pages fetched 22 Nov 2026 unless marked "from knowledge".

| Source | API | Key / limits | Commercial (monetised YT/IG) | Attribution | Gotchas |
|---|---|---|---|---|---|
| Pixabay | Yes, `GET pixabay.com/api/videos/` (tags, duration, user, pageURL, renditions tiny/small/medium/large) | Free key, 100 req/60 s per key | Yes under Pixabay Content License; no standalone resale; no commercial use of recognisable trademarks/logos | Not required by the licence; API docs ask that you show source where results are displayed. Store `user` + `pageURL` anyway | "Systematic mass downloads not allowed"; cache searches 24 h; full-HD URLs may need approved access; Cloudflare challenges seen by MPT code; licence page silent on AI use. |
| Pexels | Yes, `api.pexels.com/v1/videos/search` (per_page up to 80, orientation, size) | Free key, docs say issued immediately; 200 req/h, 20k/month, more on request | Yes; attribution optional per licence | API terms: show a Pexels link when displaying results; credit creator when possible | No replicating Pexels; no selling unaltered; no implied endorsement; silent on AI use. The owner's note says keys "reopened": re-test signup. |
| Coverr | Yes (REST + MCP), key in `x-api-key` | Free 50 calls/h; production needs Pro/Ultimate | Licence is commercial and non-commercial, irrevocable | FAQ says video attribution required; details not seen | Free key is for "development and small products": a monetised pipeline probably needs a paid plan. Read coverr.co/license first. |
| Mixkit | No API found | n/a | "Stock Video Free License" exists; terms not retrievable | not verified | Manual terms check needed before use. |
| Wikimedia Commons | Yes, MediaWiki API (generator=search, namespace 6, `imageinfo` with `extmetadata`) | No key; descriptive User-Agent (from knowledge) | Per file: PD, CC0, CC BY fine; CC BY-SA creates share-alike risk on edits (from knowledge) | CC BY/BY-SA need credit + licence link; fields Artist, LicenseShortName, LicenseUrl | Filter to PD/CC0/CC BY. Mostly WebM/OGV, often low-res. Copyright licence does not clear people/brands/panorama rights. |
| Internet Archive | Yes, `advancedsearch.php`, `/metadata/{id}`, direct downloads | No key; descriptive User-Agent (include model name for agents), honour 429/Retry-After (bots.html) | Item by item: Prelinger, NASA and many newsreels are PD; many items state NO licence (reject) | Record identifier + licence verbatim | Only accept explicit PD/CC0/CC BY; footage is old and low-res (suits retro look). |
| NASA Image and Video Library | Yes, `images-api.nasa.gov/search?media_type=video` (from knowledge) | No key for search (from knowledge) | Generally not copyrighted; commercial ok but no implied endorsement; NASA logos and identifiable current astronauts restricted in promotional use; some items are third-party copyrighted | Credit NASA as courtesy | Reject items marked with third-party copyright; NASA's page also forbids attributing AI outputs to NASA. |
| Videvo, Dareful, Videezy, Vidsplay | Mostly no public API | n/a | Mixed per-clip licences | varies | Not verified; do not add without reading terms. |

## Honest gaps
- Nothing was cloned, run or benchmarked: "Windows/CPU friendly" is inferred from READMEs.
- Last-commit dates are `pushed_at` and may include non-code pushes.
- Wikimedia and NASA API details are from background knowledge, not fetched this session. Mixkit and Coverr's full licence text were not read.
- Pixabay/Pexels/Coverr/NASA/IA summaries came from a fetch-and-summarise model, not full legal text. For a monetised channel, read Pixabay's Terms of Service and Coverr's licence directly.
- Free licences do not clear recognisable people, brands or trademarks, and any library can trigger YouTube Content ID claims. Keep source URL, creator, licence name/URL and download date in Provenance for every clip so claims can be disputed, and add a logo/face check.
- I found no sign that Pexels or Pixabay terms prohibit use in AI-narrated videos, but both pages are silent on AI use rather than permitting it explicitly.
