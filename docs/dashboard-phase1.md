# Dashboard Phase 1: the new Studio shell (22 Nov 2026)

Frontend only, as `docs/studio-redesign-plan.md` section 2 describes. No `control-plane/`, `workers/` or `.env` change.
Screenshots (desktop 1024 px and phone 375 px, dark; one light) are in `reports/dashboard-phase1/`.

## How to open it
- Dev server as usual (`npm --prefix dashboard run dev`), then `#/studio`, `#/create`, `#/settings`.
- The classic dashboard is still the default at `#/` (plan 2.7: default flips only after owner approval). The classic top bar has a
  **Try new studio** link; the new top bar has a **Classic view** link. To make the new Studio the front door, set
  `NEW_STUDIO_DEFAULT = true` at the top of `dashboard/src/App.tsx`.

## What changed
| Area | Route | Notes |
|---|---|---|
| Create | `#/create` | One idea box, **Short** or **Video** chips (Story hidden until Phase 2), channel chips (prefill a Short's look only), cost line from what finished projects actually cost, **Make it** and "Add to Autopilot queue instead". New projects start in **Review, 10-minute window**. Advanced: mode and review window, and for Shorts look, shots, clip quality, picture model, music, clip method (separate / last-frame / SVI), GPU power, Scout, edit switches. |
| Studio | `#/studio` | One list of every Short and Video, newest first; filters All / Needs you / Running / Done / Shorts / Videos; status in plain words, cost, age, thumbnail; "Ready to post" badge for Shorts with a READY Autopilot run. Top bar: needs-you counter and month spend. |
| Project page | `#/p/short/{id}`, `#/p/video/{id}` | Shared stage timeline (Idea, Script, Voice, Music, Pictures, Clips, Edit, Package, Publish), one "what's next" card (Approve, Redo with a note, Retry, Continue without it, cost approval, producer advice for Videos), player, cost summary, "what was made" thumbnails, all-stages mode switch. **Simple** by default; **Advanced** (remembered per browser) adds a mode switch per stage and today's cards: Video: Stages (the classic stage rows with approve/reject/redo), Storyboard, Story/Animatic/Research, Creative review, Inspector, Plan, Time and cost, Cost ledger, AI Producer, Routing. Short: GPU card, Hook, Paths, Steps and log, edit switches, Shots grid (pick shots to redo), Quality checks, Review, Scout, Evidence, Routing and costs. |
| Settings | `#/settings/{tab}` | Tabs GPU, Providers, Routing, Budget, Autopilot, Producer rules, Lessons, Backend. GPU: today's spend vs cap, pod leases (watchdog), broker providers (preflight quote, canary proof, caps), live prices, live pod logs (only polled while open). Backend power lives under Backend and also shows in the top bar when the backend is down. |

Code: `dashboard/src/studio/` (`projectAdapter.ts` is the one adapter that turns a Video or a Dream into the shared `ProjectRow` /
`ProjectView` and routes actions to today's per-product endpoints). Existing files only got small edits: `App.tsx` (routing and link),
`GpuPanel.tsx` (log body split out as `GpuLogs`), `Providers.tsx` (`withRouter` prop), `ProducerCard.tsx` and `VideoDetail.tsx` (exports),
`pages/stageRuns.ts` (moved `latestRuns`), `api.ts` (two read calls: broker providers, watchdog).

## Still Classic (nothing deleted)
All old routes work unchanged: `#/`, `#/video/{id}`, `#/dream`, `#/dream/{id}`, `#/providers`, `#/autopilot`. Specifically still only there:
- Dream form extras: hook title, idea packs, "choose the path" at creation (Create links to the classic form).
- Posting a Short: the Studio shows "Ready to post"; the Post form (title, description, hashtags, privacy) stays on `#/autopilot` (also under Settings → Autopilot), because posting publishes outward.
- Autopilot "ready to post" runs are a badge on the Short's row, not separate rows.
- GPU preflight / canary buttons: they are not in the dashboard today (a canary rents a real pod), so Settings shows their results read-only.

## Known gaps (data the current APIs do not give)
1. **Stage order**: the timeline follows the approved mock (Music before Pictures). A Short really runs Pictures, Clips, then Music.
2. **Short producer advice**: the Dream detail has a `producer` field but its shape is undocumented, so the "what's next" card shows AI Producer advice for Videos only.
3. **Short: "Continue without it"** has no endpoint (only Retry); the button is hidden for Shorts. Video has it.
4. **Short review minutes** cannot be changed after creation (Video can, in Advanced).
5. **Channels** are not stored by the backend; the chip only prefills a Short's look (Dreamscapes: dreamcore; Science: lonely, a placeholder: owner to confirm) and does nothing for a Video.
6. **Estimate**: no estimator API; Create shows the median cost of finished projects of that product (and no time estimate).
7. **Cost "today"**: there is no total-today endpoint. The top bar shows the month (Gemini + GPU + Claude from `/api/budget`); today's GPU spend is in Settings → GPU.
8. **Cost lines** are grouped from the ledger notes (first word of the note + provider); a per-shared-stage cost split needs a backend stage tag.
9. **Video stage status** is derived on the client from the latest attempt of each sub-stage; a shared stage with some sub-stages not yet started reads "working" only if the current stage is inside it.
10. **Thumbnails** come from `thumb_asset_id`; Videos with none show a placeholder.
11. **Prices now** (Settings → GPU) was empty against the live backend when tested (no quotes returned by `/api/gpu/broker/options`).
12. **Story** (third product) is hidden per owner decision; `parent_id`/paths remain classic.

## Verification
- `npm --prefix dashboard run build` passes (tsc + vite); `npm --prefix dashboard run lint` shows only the 4 pre-existing warnings. The dashboard has no test runner.
- Checked in the browser pane against the live backend with GET requests only (list, a Short, a Video, GPU/budget/settings). No POST/PUT/DELETE was sent: Approve, Redo, Make it and the mode switches were rendered but **not clicked**, so the write paths are covered by type checking and by being the same calls the classic pages use, not by a live run.
- Phone width (375 px) and light theme checked; no console errors.
