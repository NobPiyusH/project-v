# Java to Python migration plan (staged, never big-bang)

## Status, 23 Nov 2026 (S7 part 1: Dream, Dream GPU and Autopilot routes; branch claude/python-migration-88b813)

**The Python app can now serve the Dream, Dream GPU and Autopilot routes on its own port, beside Java. Java stays live and stays the default; nothing is cut over. S8 / any cut-over is not started.**

Done:
- `studio/api/dream_routes.py`: every route of DreamController, DreamGpuController and AutopilotController (about 50), same paths and JSON field names; jsonb columns Java sent as `PGobject` objects are sent the same way; timeline and log in camelCase with `Instant` times.
- Contract check on fakes: `studio/tests/fixtures/java_dream_shapes.json` holds the key names and JSON types the LIVE Java API returned (read-only GETs on the local Java, no data stored); `test_dream_api.py` compares our responses key by key (list, detail with timeline/log/costs/decisions/metrics/producer, chain, reviews, gpu, autopilot status/settings/ideas/runs, idea packs). No difference left.
- Switches, all OFF by default: `STUDIO_DREAM_ROUTES` (mount the routes), `STUDIO_JOBS` (periodic jobs), `STUDIO_TEMPORAL_WORKER` (`python -m studio.worker` refuses without it), `DREAM_GPU_PROVIDER` (`modal` = no pod can be rented; `broker` = RunPod only), and in the dashboard `PROJECTV_PY_AREAS=/api/dream,/api/autopilot` (with `PROJECTV_PY_API_URL`, default :8081) in `dashboard/vite.config.ts`: unset, every `/api` call goes to Java exactly as before.
- Periodic jobs (`studio/api/jobs.py`): pod "nobody waits" check and Manual-hold idle release every 30 s (only when the broker is on), autopilot tick every minute.
- Real `ProducerService` behind `DreamProducer` (wiring.py); a thread-local database connection per thread (`ThreadLocalPgDb`) so requests and jobs never share a transaction.
- Worker start-up for the Dream and post workflows on `projectv-control-py` (never Java's queue). Pods still only come from `GpuBroker.acquire()`: self-stop wrapper and watchdog lease; watchdog settings untouched.

Left (not done): the video pipeline's routes (`/api/videos/*`, production, stock, etc.) and the video worker's wiring (writer, director, inspector, voices: `build_worker` needs that graph), the remaining controllers of the ~130 routes, a contract replay of the routes that need real data (we compared shapes, not values), and the cut-over itself (needs your yes, and a week of normal use per the stop rule).

Could not verify without spend (each needs the owner's yes): everything in the S6 list (real pod rental, paid LLM calls, Gemini watching Shorts, real YouTube upload, a recorded Dream Short compared with Java), the Temporal gateway against a real Temporal server with the Python worker (tests use the local test server and fakes), and running the Python app with `DREAM_GPU_PROVIDER=broker` against a real RunPod account.

## Status, 23 Nov 2026 (end of S6 port; branch claude/python-migration-88b813)

**S6 (dream, autopilot) is ported and green on fakes. Java stays live; nothing is switched, pushed or merged. S7 (routes, dashboard switch) is not started.**

Done (all in `studio/`, each with ported tests; scratch Postgres with the real schema, Temporal's local time-skipping test server, no network, no spend):
- `dream/`: settings and rental estimate; idea packs (same `idea-packs.json`); lessons; "choose the path" helpers and `PathPick`; SVI scene rules; looks; step timeline and live log; evidence (events, metrics, GPU timings, recalibration); `DreamService` (writer, pictures, clips, music, finish, quality checks, pod rhythm, branch memory); Scout; music bed; `DreamStudio` (create with every option, rerun, approve/redo/mode/retry/cancel/stop, continue-this-dream, chain, hook, paths); `DreamProducer` (review, stop rule, spend decision, daily cap); `DreamGpu` (the Dream door to the GPU broker).
- `workflow/`: `dream_workflow.py` (DreamShortWorkflowPy: gates, cost gate, batch order, Producer mode, retry, cancel), `dream_activities.py` (`dream.*` and `dreamProducer.*`), `autopilot_workflow.py` (PostWorkflowPy, one upload attempt) and `runtime.py` (`query`, NOT_FOUND on a signal to an ended workflow, `build_dream_worker`).
- `autopilot/service.py`: settings, ideas (owner first, then unused Scout angles), the daily tick rules, runs, post kit, posting, dismiss, retry.
- Ported Java tests: DreamServicePersistenceTest, DreamChoosePathTest (the service part), DreamHookChainIdeasTest, DreamBrokerRouteTest (all cases: one pod booked and ended, batch order, shared pod ends at empty, SVI beats on the pod, FLUX provenance), DreamStudioCancelTest, DreamGpuPowerTest, WarmImageTest, DreamBranchesTest, DreamIdeaPacksTest, DreamTimelineTest, DreamSviTest, RentalEstimateTest, DreamProducerTest, AutopilotDailyCapTest, DreamShortWorkflowTest, AutopilotRulesTest, AutopilotPersistenceTest, the post workflow.
- Every pod rented from Dream goes through `GpuBroker.acquire()`, so it still gets the self-stop wrapper and the watchdog lease (a test checks the lease row and the `bash` wrapper). Watchdog settings are untouched.

Left (not done):
- HTTP: the Dream, DreamGpu and Autopilot controllers (all routes) and the dashboard switch are S7. The `DreamController` JSON shapes are therefore not replayed yet.
- Wiring for S7: the periodic calls (`DreamService.release_gpu_if_nobody_waits_scheduled` every 30 s, `DreamGpu.release_idle_manual` every 30 s, `AutopilotService.tick` every minute), the worker start-up, and a real `ProducerService` behind `DreamProducer` in the app (tests use a stub for the trust switch).

Could not verify without spend (each needs the owner's yes): a real pod rental with picture and clip batches (FLUX/Wan/SVI), real paid LLM calls (Writer, Scout brief, Producer, post kit), Gemini watching Shorts (Scout, quality check), the real YouTube upload, a recorded Dream Short compared with Java, and the contract replay of the HTTP routes (no routes yet).

Decisions to note: the writer's and Scout's LLM calls run before the step's database transaction (their cost entries are kept even if the step then fails; Java rolled them back). A signal to an ended workflow raises `WorkflowNotFoundError`, which `cancel` treats as "already ended" like Java's NOT_FOUND handling. `getVersion` gates are dropped (new workflow types).

## Status, 23 Nov 2026 (end of S5 port; branch claude/python-migration-88b813)

**S5 (video, production, workflow) is ported and green on fakes. Java stays live; nothing is switched, pushed or merged.**

Done (all in `studio/`, each with ported tests; scratch Postgres with the real schema, Temporal's local time-skipping test server, no network, no spend):
- `video/service.py` VideoService: `REDO_WITH`, guards, decisions as taste signals, behind a `WorkflowGateway`.
- `production/`: research, story, music, voice, animatic (`stages_m2a.py`); shot plan, keyframes, per-shot redo, stock lane (`shot_stage.py`); clips (`video_stage.py`); `video_gpu.py`; footage, assembly, smooth, sharpen (`finishing_stages.py`); AI Producer control side (`producer.py`); read model; plus the earlier Inspector, Creative Director, timing, diagram specs, stop rule.
- `workflow/`: `video_production.py` (main workflow + shot/clip/voice redo, footage, creative review workflows, new type names `*Py` on queue `projectv-control-py`), `activities.py` (`control.*` and `producer.*` activities), `codec.py`, `runtime.py` (TemporalGateway, worker builder).
- Ported Java tests: VideoProductionWorkflowTest (all cases plus pause/status), ProductionPersistenceTest (voice, footage, master, smooth, sharpen, clips, producer), StockLaneTest, VideoGpuTest, VideoLanesTest, MoneyCapsTest, StoredLine/redo rules via earlier commits.

Left (not done):
- `VideoGpu` talks to a `PodGpu` protocol; the real one is `DreamGpu`, which is S6. Until S6 the pod route (`provider-*=broker`) has only a fake behind it.
- Dream-short workflows and the autopilot (S6), the HTTP routes and dashboard switch (S7). The `ProductionService` front-door class was not ported (it only delegated); the dashboard routes will call the stages directly.
- The `ProducerHooks` wiring into VideoService, the periodic `VideoGpu.release_idle` call and the worker start-up are wiring for S7.
- A PRODUCER-mode workflow path is ported but only the Producer activities (not the workflow branch) are tested.

Could not verify without spend (each needs the owner's yes): a real clip batch or keyframe batch on a pod or Modal, paid LLM calls (Writer, Director, Creative Director, Producer), a recorded run compared with Java (stop rule 4c), and the contract replay of the HTTP routes (no routes yet).

Decisions to note: LLM calls in the shot plan, concept and script run before the stage's database transaction (so their cost entry is kept even if the stage then fails; Java rolled it back). Provider health is reported even when a keyframe or clip stage fails.

Status: DRAFT for the owner. Written 23 Nov 2026. Direction decided 23 Nov (post-tv-queue items 10-13): Python-only studio on serverless GPU.
Risk label: **HIGH** if done in one go; **MEDIUM** done stage by stage as below.

## 1. What exists today (inventory)

Java control plane: `control-plane/src/main/java/com/projectv`, 138 files, ~22k lines. Tests: 54 Java test files. Plus 44 Flyway migrations (`V1`-`V43`) that are the database schema.

| Package | Lines | What it does | Java tests |
|---|---|---|---|
| config | 105 | Temporal client, LLM on/off toggle | none |
| ledger | 453 | Prices (`config/providers.yaml`), cost ledger, monthly caps | PricingTest |
| assets | 134 | Asset store (S3), media URLs | none |
| provider | 74 | Provider registry | none |
| routing | 302 | Router (quality vs rupees per job) | RouterTest |
| media | 442 | Shared media record types | StockResultJsonTest |
| planner | 319 | Gemini planner, rule-based fallback | PlannerTest |
| agents | 1241 | Gemini/Claude/OpenAI-compatible LLM calls, fallback to AICredits | 6 tests |
| autopilot | 743 | Daily autopilot rules | 2 tests |
| gpu | 5097 | Pod broker (RunPod/Vast/Jarvislabs), leases, watchdog, logs, self-stop | 15 tests |
| video | 388 | VideoService, redo with `REDO_WITH` dependencies | none direct |
| workflow | 2120 | Temporal workflows and activities for the long video | VideoProductionWorkflowTest |
| production | 4794 | Stages: script, shots, diagrams, stock, inspector, creative director, money caps | 12 tests |
| dream | 5355 | Dream Shorts: ideas, scout, producer, SVI scenes, choose-the-path, GPU pick | 15 tests |
| api | 609 | 5 controllers; with autopilot and dream 3 more: about 130 routes, all under `/api` | none (dashboard is the client) |

Already Python: `workers/` (Temporal activities, ComfyUI/GPU pod code, diagram, YouTube, 40+ tests). The dashboard (`dashboard/`, React) talks to the Java API on :8080.

## 2. Target design

One Python program family, same repo, new package `studio/` (the workers stay in `workers/`; later they can move under `studio/`).

- **HTTP**: FastAPI on :8080, **same paths, same JSON field names and shapes** as Java, so the dashboard needs no change. Money is sent as JSON numbers.
- **Workflows**: Temporal Python SDK (already a dependency). Same rules as Java: deterministic workflow code, all I/O in activities, asset IDs not bytes. Temporal keeps running workflows; we switch task queues per workflow type, one at a time.
- **Database**: the same Postgres 5433 and the same schema. Flyway files stay the single source for now; later move them to a Python runner (plain SQL files, applied in order, same `flyway_schema_history` table so nothing re-runs). No schema change is part of this migration.
- **Storage**: SeaweedFS S3 via boto3 (already used by workers).
- **Config**: `config/providers.yaml` read by Python exactly as Java reads it. `.env` for secrets.
- **Code style**: sync psycopg 3 for the DB (simple, durable), dataclasses, `Decimal` for money (Java `BigDecimal` parity), type hints, ruff.

Both control planes can run side by side on the same database. Only one is "live" for the dashboard at a time (Java stays on :8080, Python on :8081 until cutover).

## 3. Stage order (smallest and least risky first)

| # | Stage | Contains | Why here | Estimate |
|---|---|---|---|---|
| S0 | Skeleton + contract harness | `studio/` package, settings, DB helper, record/replay tool | Needed by everything | 0.5 day (**done in this branch**) |
| S1 | ledger | Pricing, CostLedger, BudgetGuard, `GET /api/ledger` | Pure maths + 3 SQL statements, has a Java test, money safety | 0.5 day (**done in this branch**) |
| S2 | config, assets, provider, media | Settings, S3 asset store, provider registry, record types | Small, leaf modules | 1 day (**done in this branch**; `LlmToggle` is Spring-only so not needed; `TemporalConfig` moves with S5) |
| S3 | routing, planner, agents | Router, planner, LLM clients and fallback | Depends on ledger; pure logic, well tested; LLM calls need live keys (owner, approve any spend) | 2-3 days (**S3a and S3b done**: routing, planner, style presets, model fallback, Claude/OpenAI-compatible/AICredits clients, `Llm`, ClaudeResearcher, Writer/Director/CreativeDirector agents, AgentTypes) |
| S4 | gpu broker + watchdog | RunPod/Vast/Jarvislabs providers, leases, pod logs, self-stop, watchdog scheduler | Largest risk: a forgotten pod costs money (rule 8). Must be tested with fakes first, one real pod only with the owner's OK | 4-5 days. Split: **S4a done** (limits, RunPod API, leases, events, watchdog + loop, in-pod self-stop, pod logs); **S4b done** (GpuBroker, wallet, records, provider states, boot timings, worker auth, TOTP, preferences; the `/api/gpu/broker/*` routes come with the Dream controller in S6); **S4c done: RunPod, Jarvislabs, Vast.ai and E2E adapters** (so S4 is complete; the `/api/gpu/broker/*` routes come with the Dream controller in S6) |
| S5 | video, production, workflow | VideoService, stages, Temporal workflows | Biggest logic; must keep REDO_WITH and the "failed stage waits for owner" rules | 5-6 days |
| S6 | dream, autopilot | Dream Shorts, producer, autopilot | Largest package, newest, changing fast; port last so it is ported once | 5-6 days |
| S7 | api (all ~130 routes) + cutover | Remaining controllers, dashboard switched to Python | Routes are added with each stage; S7 is the final gap check and the switch | 2 days |

Total about 20-25 working days of Claude time; stages S2-S6 can be shortened by stopping at any stage (the rest stays on Java, see "mixed mode").

**Mixed mode**: each stage ships its routes in the Python app only when it passes; until the cutover a thin proxy (or the dashboard base URL per area) sends un-ported routes to Java. So the studio keeps working after every stage.

## 4. Tests that must pass per stage

For every stage, all of these:

1. **Ported unit tests**: each Java test for the package is rewritten in pytest with the same inputs and expected numbers (not "close enough": same asserts).
2. **Contract tests**: the harness (`studio/contract`) records real requests and responses from the Java API (GET only by default; writes only against a throwaway database), then replays them against the Python API and compares status and JSON (numbers by value, key order ignored, volatile fields such as ids and timestamps masked by a list). A stage with routes is not done while any recorded exchange differs.
3. **Database tests**: against the real local Postgres (5433) inside a rolled-back transaction, so no data is changed.
4. **Recorded run**: one real production run (a Short, later a long video) replayed: costs, stage states and output records must match the Java run within the same rounding.
5. `uv run ruff check` clean.

Test map: S1 PricingTest; S2 StockResultJsonTest; S3 RouterTest, PlannerTest, 6 agents tests; S4 the 15 gpu tests; S5 VideoProductionWorkflowTest + 12 production tests; S6 the 15 dream + 2 autopilot tests.

## 5. Stop rule

A stage is **done** only when: (a) its ported tests pass, (b) the contract replay shows no difference on the recorded requests, (c) for money and GPU stages, a recorded run matches the Java version, and (d) the owner has seen the result and said go. The old Java code for that stage **stays in the repo and keeps working** until the owner approves its removal, after the full cutover and at least one week of normal use. No stage starts before the previous one is approved. Anything that spends money (a real pod, paid API calls) needs the owner's yes first.

## 6. Rollback

- Per stage: the Python route or workflow for that stage is switched off (one setting per area), the Java one takes over again. The database is shared and unchanged, so there is nothing to restore.
- Workflows: a Python workflow type is started only for new videos; videos already running finish on the Java worker. A bad Python workflow is rolled back by starting new videos on the Java type.
- The branch is never merged without the owner's go; `git revert` of the merge is the last resort.

## 7. Risks (labelled)

- **HIGH** Money and GPU code (S4) ported wrongly could overspend or leave a pod running. Mitigation: ported tests first, fake providers, the watchdog and the cap tests ported before anything else in S4.
- **MEDIUM** JSON shape drift breaks the dashboard silently. Mitigation: contract replay on every route.
- **MEDIUM** Temporal determinism and in-flight workflows. Mitigation: new workflow types, not edits to running ones.
- **MEDIUM** Rounding differences (`BigDecimal` vs float). Mitigation: `Decimal` everywhere money is stored; same rounding mode (HALF_UP).
- **LOW** New libraries (fastapi, uvicorn, psycopg, pyyaml) in a separate `studio` dependency group, so the heavy worker group (torch) is untouched.

## 8. Open question for the owner

After S1 is shown: go ahead with S2 and S3 (small, no spending, about 3-4 days)? S4 (GPU broker) will be a separate go.
