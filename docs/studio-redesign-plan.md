# Studio redesign plan (design only, 22 Nov 2026)

Status: **proposal, waiting for owner approval.** No product code changed. Mock: `reports/studio-redesign-mock.html`.

Goal: the owner opens one simple dashboard with three areas (**Create, Studio, Settings**), sees every project in one list,
and every project page looks the same whatever the product (Short, Video, Story). Phase 2 builds the production engine
once so Videos and Dream Shorts stop having two copies of modes, Producer, GPU, music, edit and cost.

---

## 1. Audit

### 1.1 Dashboard today (hash routes in `dashboard/src/App.tsx`)

| Route / place | File(s) | Lines | API calls used |
|---|---|---|---|
| Top bar | `App.tsx`, `BudgetMeters.tsx`, `BackendPower.tsx`, `GpuPanel.tsx` | 61+35+31+109 | `api.stages`, `api.budget`, `api.videos`, `autopilotApi.runs`, `api.gpuPods`, `api.gpuLogs` |
| `#/` Studio (videos) | `pages/Home.tsx`, `pages/VideoList.tsx` | 70+72 | `api.videos`, `api.createVideo` |
| `#/video/{id}` | `pages/VideoDetail.tsx` | 470 | `api.video`, `api.production`, `approve/reject/rerun`, `pause/resume`, `setMode/setAllModes`, `setReviewMinutes`, `reviewImages` |
| ↳ cards | `Storyboard.tsx` (266), `ProductionCards.tsx` (211), `ClipsCard.tsx` (177), `ScriptEditor.tsx` (154), `ProducerCard.tsx` (118), `CreativeCard.tsx` (109), `Routing.tsx` (122) | | clips estimate/progress/redo/stop/select/final-wanted; shots redo, prompt, keyframe, footage; script lines edit/redo/voice; producer + rules + trust; routing providers/budgets/reset/on-off |
| `#/providers` | `pages/Providers.tsx` | 45 | `api.providers`, `api.styles` |
| `#/dream`, `#/dream/{id}` | `pages/dream/DreamShorts.tsx`, `DreamList`, `NewDreamForm`, `DreamView` + 14 cards (`StepTimeline`, `ShotsGrid`, `ShotReview`, `GpuCard`, `HookCard`, `PathCard`, `ScoutCard`, `QualityCheck`, `Evidence`, `Lessons`, `IdeaPicker`, `RoutingAndCosts`, `DreamStatusBadge`) | ~700 | `dreamApi.list/get/create/ideaPacks`, `approve/redo/rerun/retry/continue/cancel/stop`, `setMode`, `editing`, `setHook`, `setPaths`, `pickAngle`, `review`, `lessons`, `gpuOptions/gpuPick/gpuOff/setGpuHold` |
| `#/autopilot` | `pages/Autopilot.tsx` | 177 | `autopilotApi.status/settings/save/ideas/addIdea/deleteIdea/runs/start/post/dismiss/retryKit` |

Problems the owner feels: four top-level tabs that are really two products + admin; two different "new" forms; two
different project pages with different words (Video "stages", Dream "steps"); GPU shows up in three places (top-bar
logs, Dream GpuCard, Providers/Routing); cost in three places (top bar, VideoDetail, Dream RoutingAndCosts).

### 1.2 Backend features: Video vs Dream (duplicates marked ⚠)

| Feature | Video (`video/`, `production/`, `workflow/`) | Dream (`dream/`) | Dup? |
|---|---|---|---|
| Stage list | `workflow.Stage` (21 stages, PLAN…PUBLISH) | `DreamTypes.DreamStep` (SCOUT, WRITE, PICTURES, CLIPS, MUSIC, FINISH) | ⚠ two vocabularies |
| Modes | `StageMode` AUTO/PRODUCER/REVIEW/MANUAL, `PUT /videos/{id}/modes/{stage}`, review-minutes | same idea in `modes_json` + `review_minutes`, `POST /dream/{id}/steps/{step}/mode` | ⚠ |
| Approve / redo / retry | `stages/{stage}/approve·reject·rerun`, `REDO_WITH` deps in `VideoService` | `steps/{step}/approve·redo`, `rerun/{step}`, `retry`, `continue` | ⚠ |
| Producer review | `ProducerService`, `ProducerActivities`, producer rules/trust | `DreamProducer`, `DreamProducerActivities`, `producer_decides` | ⚠ two implementations |
| Script / narration | `StoryStage`, `VoiceStage`, `NarrationTiming`, script line edit/redo/voice | WRITE step (prompts only, no narration) | Video-only today |
| Pictures (keyframes) | `ShotStage` + keyframe select | PICTURES step, FLUX.2 klein (`picture_model`) | ⚠ |
| Clips (motion) | `FootageStage`, `VideoStage`, `ClipRedoWorkflow`, clip progress/estimate | CLIPS step, `DreamSvi`, chaining, GPU broker via `DreamGpu` | ⚠; Dream's path (Wan+SVI on broker GPU) is the newer, better one |
| GPU | (none of its own; uses providers) | `DreamGpu`, `DreamGpuController`, `RentalEstimate`, hold/idle | Dream-only, should be shared |
| Music | `MusicStage` (Lyria) | `DreamMusic` (bed / own track) | ⚠ |
| Edit / sync | `AnimaticStage`, `AssemblyStage`, `FinishStage`, `SharpenStage` | FINISH step + `editing` (music/ambience/pacing), RIFE smoothing | ⚠ |
| Quality checks | `Inspector`, `CreativeDirector`, critic | `check_json`, `picture_check_json`, `DreamEvidence` | ⚠ |
| Captions / hook | Packaging (Video) | `HookCard` hook title/text | partial ⚠ |
| Publishing kit | Packaging + Autopilot `PostWorkflow`, `retry-kit` | Autopilot uses Dream output | shared via Autopilot already |
| Cost | `CostLedger`, `BudgetGuard`, `spent_inr` | `spent_inr`, `RoutingAndCosts` | ledger shared ✓, display duplicated |
| Learning | — | `DreamLessons`, idea packs, scout | Dream-only |
| Temporal | `VideoProductionWorkflow` (+4 redo workflows), 18 `getVersion` calls repo-wide | `DreamShortWorkflow` | two workflows |

---

## 2. Phase 1: simple dashboard (frontend only)

Rule: **no backend change.** Phase 1 is a new shell that calls today's APIs through one adapter
(`projectAdapter.ts`) that turns a Video or a Dream into one `Project` shape for the screen.

### 2.1 Navigation
Top bar: **Create · Studio · Settings**, plus one "needs you" counter and one cost meter (today/month). Theme
toggle and Start backend stay. GPU logs move to Settings → GPU.

### 2.2 Create (new, merges `VideoList` form + `NewDreamForm` + Autopilot idea add)
- One idea box. Three product chips: **Short** (Dream pipeline), **Video** (documentary pipeline), **Story** (series
  episode = Dream with `parent_id`/paths; shown, marked "uses Short engine").
- Channel chip (Science / Dreamscapes) prefills look and style.
- Simple view: idea + product + Make it. An estimate line ("about ₹25, 20-40 min").
- Advanced (collapsed): look, shot count, quality, picture model, GPU mode, scout, hook, chaining, path choice
  (from `NewDreamForm`); style preset, length, aspect (Video).
- "Add to Autopilot queue instead" link = `autopilotApi.addIdea`.

### 2.3 Studio list (merges `Home`/`VideoList`, `DreamList`, Autopilot runs)
- One list, newest first, filter chips: All · Needs you · Running · Done · Shorts · Videos.
- Each row: thumbnail, title, product badge, status in plain words ("Waiting for you: approve pictures"), cost, age.
- Autopilot "ready to post" runs show as rows with a **Post** button.

### 2.4 Shared project page (replaces `VideoDetail` + `DreamView` for the owner)
**Simple view (default):**
1. Player (final or latest preview) + title + cost so far.
2. **Stage timeline**: same shared stage names for both products (see §3.2). Each dot: done / running / waiting /
   failed / skipped.
3. **One "what's next" card**: the single thing that needs the owner, with **Approve**, **Redo (with a note)**,
   **Retry** / **Continue without it** (rule 2b). Producer recommendation shown inside it when present.
4. Stage outputs as thumbnails (pictures, clips, music, captions), tap to open.

**Advanced view (toggle, remembered):** everything today's pages show, regrouped per stage:
mode picker per stage (Auto/Review/Manual/Producer) and review minutes; Script editor; Storyboard/shot prompts,
keyframe and clip versions; Producer card and rules; GPU card (pick, hold, off); hook/path/scout cards; quality
evidence; routing decisions and cost lines; Lessons.

### 2.5 Settings (moves admin out of the way)
Tabs: **GPU** (GpuPanel logs, broker providers, canary/preflight, watchdog), **Providers** (Providers page),
**Routing** (Routing card, budgets), **Budget** (BudgetMeters, ₹ caps), **Autopilot** (schedule + settings),
**Producer rules** (global trust/rules), **Lessons** (Dream lessons).

### 2.6 Keep / merge / move / remove

| Today | Phase 1 |
|---|---|
| Home, VideoList, DreamList, DreamShorts list | **Merged** → Studio list |
| VideoList form, NewDreamForm, IdeaPicker, Autopilot idea add | **Merged** → Create |
| VideoDetail, DreamView | **Merged** → Project page (Simple) |
| StepTimeline, stage list in VideoDetail | **Merged** → shared timeline |
| ScriptEditor, Storyboard, ProductionCards, ClipsCard, ShotsGrid, ShotReview | **Kept**, inside Advanced per stage |
| ProducerCard, CreativeCard, QualityCheck, Evidence | **Kept**; recommendation surfaced in Simple "what's next" |
| GpuCard, HookCard, PathCard, ScoutCard | **Kept** in Advanced |
| RoutingAndCosts, Routing card on VideoDetail | **Merged** → one Cost & routing section (Advanced) |
| GpuPanel, Providers, Routing admin, BudgetMeters detail, Autopilot settings, Lessons | **Moved** → Settings |
| Top-bar GPU logs button, four nav tabs | **Removed** from top bar (reachable in Settings) |
| Anything deleted | **Nothing** until the owner approves the new pages |

### 2.7 Migration path (today's pages stay reachable)
1. Build new shell under new routes `#/create`, `#/studio`, `#/p/{kind}/{id}`, `#/settings`; old routes unchanged.
2. A "Classic view" link in the new top bar and a "Try new studio" link in the old one. Default stays **old**.
3. Owner uses new shell for a week of real Shorts/Videos; every missing control found goes into Advanced.
4. Owner approves → default flips to new; old routes still work (bookmarks).
5. After one more approval, old page components that are fully wrapped are deleted.

Effort: shell + adapter + Create + Studio list ≈ 2 days; project page Simple ≈ 2 days; Advanced regrouping ≈ 2 days;
Settings ≈ 1 day; polish/phone ≈ 1 day. **≈ 8 working days**, no Java/worker change, no spend.

---

## 3. Phase 2: one shared production engine

### 3.1 Domain model
```
Project (id, kind: SHORT|VIDEO|STORY, title, idea, channel, status, parent_id, settings jsonb, spent_inr)
 └─ StageRun (project_id, stage, attempt, mode, status, output_asset_ids, producer_verdict, error, cost_inr,
              started_at, finished_at)   -- one row per attempt, redo = new attempt
 └─ Asset / Provenance / CostEntry / RoutingDecision  (unchanged, gain project_id)
Pipeline (kind → ordered list of StageDef, each with deps for REDO_WITH)
StageDef (key, label, activity, default mode, reviewable_by_producer, gpu_needed)
```
`Project` becomes the parent of today's `videos` and `dreams` rows (1:1 at first via `project_id` FK), so nothing is
dropped; new code reads `Project`, old code keeps its tables.

### 3.2 Shared stage vocabulary and mapping

| Shared stage | Video (`Stage`) | Short (`DreamStep`) |
|---|---|---|
| Idea | PLAN, RESEARCH, CONCEPT | SCOUT |
| Script | SCRIPT | WRITE (shot prompts) |
| Voice | NARRATION | — (skipped) |
| Music | MUSIC | MUSIC |
| Pictures | SHOT_PLAN, KEYFRAMES, ANIMATIC | PICTURES |
| Clips | LIBRARY_MATCH, DRAFT_GEN, FINAL_GEN, CRITIC, TAKE_SELECTION | CLIPS |
| Edit | UPSCALE, AUDIO, ASSEMBLY, ROUGH_CUT_REVIEW | FINISH |
| Package | PACKAGING (captions, hook, kit) | hook + Autopilot kit |
| Publish | FINAL_APPROVAL, PUBLISH | Autopilot post |

Fine-grained Video stages remain sub-steps inside a shared stage (shown in Advanced).

### 3.3 Temporal versioning strategy
- **Never mutate running workflows' history.** New workflow type `ProjectWorkflow` (one per project) driven by the
  Pipeline definition; activities are thin wrappers around today's stage classes.
- Existing `VideoProductionWorkflow` / `DreamShortWorkflow` keep running for in-flight projects; new projects go to
  `ProjectWorkflow` behind a flag per kind (`projectv.engine.shared.short=false` etc., default OFF).
- Inside `ProjectWorkflow`, every behaviour change uses `Workflow.getVersion(changeId, DEFAULT, n)` (the repo already
  uses this 18 times); signals for approve/redo/mode are one shared set (`approve(stage)`, `redo(stage, note)`,
  `setMode(stage, mode)`, `retry(stage)`, `skip(stage)`).
- Use Temporal Worker Versioning (build IDs) when moving task queues; drain old workers only after open
  executions finish. Replay tests from recorded histories of a real Short and a real Video run in CI.

### 3.4 Order of migration (each step separately demoed and approved)

| # | Step | Why this order | Effort | Risk |
|---|---|---|---|---|
| 1 | `Project` table + backfill + read-only `/api/projects` list/detail | Phase 1 adapter moves server-side; zero behaviour change | 2 d | LOW |
| 2 | Shared mode/approve/redo/retry API (`/api/projects/{id}/stages/{s}/…`) routing to old endpoints | one control surface | 2 d | LOW |
| 3 | Shared **cost ledger view** per project/stage | ledger already shared | 1 d | LOW |
| 4 | Shared **Producer** (merge `DreamProducer` into `ProducerService`, rules per kind) | biggest logic duplicate | 4 d | MEDIUM |
| 5 | Shared **Clips stage** = Dream's FLUX→Wan+SVI on GPU broker, offered to Videos | best quality path reused | 5 d | HIGH (GPU, spend, pods) |
| 6 | Shared **Music + Edit/sync** (Lyria/bed, beat sync, RIFE, assembly) | depends on clips | 4 d | MEDIUM |
| 7 | Shared **Script/Voice/Captions/Publishing kit** (gives Shorts narration + captions) | voice still open with owner | 4 d | MEDIUM |
| 8 | `ProjectWorkflow` for Shorts (flag ON for new Shorts only) | smaller pipeline first | 5 d | HIGH |
| 9 | `ProjectWorkflow` for Videos; retire old workflows after drain | largest pipeline last | 6 d | HIGH |

Total ≈ 33 working days; steps 1-3 can follow Phase 1 right away.

### 3.5 Risks
- **HIGH** Temporal non-determinism if old workflows are edited instead of replaced. Mitigation: new type + replay tests.
- **HIGH** GPU spend / forgotten pods while the clips stage is moved (rule 8). Mitigation: reuse broker watchdog
  unchanged; first runs need owner approval for spend.
- **MEDIUM** Quality regression for Videos when switching to the Dream clips path. Mitigation: A/B by eye, off by
  default (memory: quality first).
- **MEDIUM** Merge conflicts with sessions editing broker/workers now. Mitigation: Phase 2 starts after those land.
- **LOW** Phase 1 hides a control the owner needs. Mitigation: Advanced view keeps every control; old pages stay.

## 4. Decisions for the owner
1. Approve the three-area layout (Create / Studio / Settings) and Simple-by-default with an Advanced toggle.
2. Is **Story** a third product now, or hidden until Phase 2 (today it would just be a Short episode with paths)?
3. Default mode for new projects in Simple view: Auto, Review (10 min) or Producer?
4. Phase 2 order: start with shared Producer (step 4) or with shared Clips on GPU (step 5, needs spend approval)?
5. When to retire the classic pages: after one week of use, or only on explicit approval per page?
