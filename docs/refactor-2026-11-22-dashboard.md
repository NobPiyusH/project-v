# Dashboard refactor, 22 Nov 2026

Branch `claude/refactor-dashboard-1452d7`. Scope: `dashboard/` only (no Java, no workers). Nothing the owner sees was meant to change: same pages, same layout, same texts. Options that are switched off on purpose (first-last frame, SVI scenes, hook title, Choose the path, Lyria, Z-Image, AI Producer mode) are all still in the forms.

## What was split

- **`pages/DreamShorts.tsx` (661 lines) → `pages/dream/`**, one component per file:
  `DreamShorts` (router, list page), `NewDreamForm`, `DreamList`, `DreamView` (one Short), `GpuCard`, `HookCard`, `PathCard`, `IdeaPicker`, `ShotsGrid`, `ShotReview`, `StepTimeline`, `QualityCheck`, `ScoutCard`, `Evidence`, `Lessons`, `RoutingAndCosts`, `DreamStatusBadge`, plus `shared.ts` (editing switches, hook colours, `parseJson`, `errorText`).
  `GpuCard`'s three copies of the same try/catch became one `run` helper.
- **`App.tsx`**: the budget meters (`BudgetMeters.tsx`, the INR and USD meters now share one `Meter`), the backend Start/Restart control (`BackendPower.tsx`) and `useTheme` (into `hooks.ts`) moved out. The `/budget` reply has a named `Budget` type.
- **One fetch helper**: `http.ts` `request()` replaces the three copies (`api.ts` `call`, `dreamApi.ts` `call` + `gpuCall`, `Autopilot.tsx` `call`).
- **Autopilot client** moved from the page into `autopilotApi.ts`; its own copy of the step-mode type now reuses `DreamMode`, and the step labels (`STEP_LABEL`) live once in `dreamApi.ts` instead of twice.

## What was removed

- `ProductionView` and its `ShotGrid` component: never rendered anywhere (the Storyboard replaced them). The file is now `pages/ProductionCards.tsx` with the cards that are still used (Animatic, Story, Research, ShotEditor, PolicySelect).
- Client methods `api.system()` and `api.ledger()`: nothing called them (the endpoints still exist on the control plane).
- CSS `tr.clickable`: no element used it.
- Every other client call was checked against the control-plane controllers (grep of `@*Mapping`): all endpoints the dashboard calls still exist. No unused npm dependencies (only react, react-dom and the build tools).

## Checks

- `npm --prefix dashboard run build`: passes (tsc with `noUnusedLocals` + Vite).
- `npm --prefix dashboard run lint` (oxlint): 0 errors; 4 warnings, all already there before (fast-refresh export hints in `components.tsx`, `ClipsCard.tsx`, and a `Date.now()` purity hint in `Routing.tsx`).
- No test suite exists in the dashboard.
- Browser, read-only against the live backend (GET only, a separate dev server on 5174): Dream Shorts list, a running Short's detail with the GPU card (today's spend against the $10 cap, power and pick options), the Steps timeline, a studio video's Storyboard and Story tabs. No console errors.

## Risks

- **LOW**: the shared `request()` reads an empty reply as `undefined`. The old GPU helper always parsed JSON, so an empty GPU reply would have thrown before and now does not. The GPU endpoints always return JSON, so nothing should change in practice.
- **LOW**: the buttons (approve, redo, GPU power, and so on) were not clicked, because a paid test was running. Their handlers were moved without changes, and the compiler checks the wiring.
- **LOW**: `VideoDetail.tsx` (470 lines) and `Storyboard.tsx` were left as they are. They are already split into tabs and cards, and splitting them further was not worth the risk while the owner is testing. They can be the next step.
