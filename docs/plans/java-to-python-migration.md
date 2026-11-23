# Java to Python migration plan (staged, never big-bang)

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
| S4 | gpu broker + watchdog | RunPod/Vast/Jarvislabs providers, leases, pod logs, self-stop, watchdog scheduler | Largest risk: a forgotten pod costs money (rule 8). Must be tested with fakes first, one real pod only with the owner's OK | 4-5 days. Split: **S4a done** (limits, RunPod API, leases, events, watchdog + loop, in-pod self-stop, pod logs); **S4b open** (GpuBroker, wallet, records, boot timings, worker auth, TOTP); **S4c open** (RunPod/Vast/Jarvislabs/E2E providers) |
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
