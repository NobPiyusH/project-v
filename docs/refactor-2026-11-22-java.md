> **Java-era document.** The Java control plane was removed on 24 Nov 2026 (tag `java-final`); `CLAUDE.md` and `docs/DEVELOPER-TAKEOVER.md` win. Class and path names below refer to the removed `control-plane/`; see `docs/java-removal.md`.

# Control-plane refactor, 22 Nov 2026 (Java)

Branch `claude/refactor-java-300f79`. Scope: `control-plane/` only. No behaviour change intended; no workflow code
(`*WorkflowImpl`) touched, so Temporal determinism and replay are unaffected. `./gradlew :control-plane:test`: 331 tests,
0 failures, 3 skipped (same skips as before).

## Removed (with proof)
| What | Proof it was unused |
|---|---|
| `DreamService.usesGpuBroker()` | Name appears only at its declaration across `control-plane/src`, `dashboard/src`, `workers/` (grep, 22 Nov). |
| `DreamService.gpuHeartbeat(UUID)` | Same: no caller. The worker's pod heartbeat goes through `DreamGpuController` → `DreamGpu.heartbeat` directly. |
| `projectv.planner.timeout-seconds` (application.properties) | No `@Value`/settings binding reads it (`grep -r planner.timeout` in `src/main/java`: none); not in `.env.example`. |

Script used for the scan: every class name and every method name in `src/main` grepped across control-plane, dashboard
and workers; names seen only once were checked by hand. Everything else seen once is a Spring entry point (REST
endpoints called by the dashboard/workers, `@Bean` methods, `@Scheduled` jobs, enum constants used via `valueOf`) and
was kept. Owner-off features (first-last bridge, ACE-Step, Z-Image, modes, Unfiltered) were not touched.

## Split / extracted
- **`DreamLooks`** (new, ~230 lines): the look catalogue moved out of `DreamService`: `Look` record, `LOOKS`,
  `LOOK_ORDER`, `SELECTABLE_LOOKS`, `UNFILTERED`, the feel/camera/structure prompt texts, `look()`, `frames()`,
  `clipSize()`, `unfiltered()`. Pure data, no I/O. References in `DreamController`, `DreamStudio`, `DreamMusic`,
  `AutopilotService` and tests now say `DreamLooks.x`.
- **`DreamScout`** (new, ~240 lines): the Scout step (watch similar Shorts, clichés/angles brief, `scout_watch` memory,
  too-similar check). `DreamService` keeps thin delegates (`scoutWanted`, `scoutRequest`, `saveScout` with its
  `@Transactional`, `scouted`, `tooSimilar`, `rememberWatched`) so `DreamActivitiesImpl` and tests are unchanged.
  Built the same way as the existing `DreamMusic` helper (constructed by `DreamService`, holds a back-reference).
- **`gpu/broker/ProviderHttp`** (new): the RestClient recipe, the safe-env check and `abbreviate`/`str` that were
  copy-pasted in `RunPodProvider`, `VastProvider`, `E2eProvider`. Per-provider differences kept exactly (RunPod 30 s read,
  no User-Agent; Vast 30 s + UA; E2E 60 s + UA). `num()` and `enc()` stay per provider because they differ (E2E parses
  numeric strings; RunPod encodes `+` as `%20`).

`DreamService`: 1611 → 1229 lines.

## Risks
- `DreamScout` calls back into `DreamService` (`dream`, `cost`, `tag`, LLM-cost linking); those became package-private
  (`linkLlmCosts`, `llmCostsSince` were private). Transactions are unchanged because the `@Transactional` entry point is
  still on the `DreamService` proxy.
- The running backend uses its own checkout; nothing here takes effect until this branch is merged and the backend is
  restarted by the owner.

## Left alone (unsure or out of time)
- Further `DreamService` splits (pictures / clips / finish / paths sections): each is tightly coupled to shot rows and
  heavily tested through package-private methods; worth doing next, one section per commit.
- Audio-mixing and record-builder duplication between production stages and Dream: not extracted in this pass.
- `GpuBroker` (682 lines) and `VastProvider` (676) are large but cohesive; not split.
- `BrokerStubs` (stub providers JarvisLabs/CloudRift/Shadeform/Modal) look unused at runtime but are `@Bean`s the broker
  may list; kept pending an owner decision on those providers.
