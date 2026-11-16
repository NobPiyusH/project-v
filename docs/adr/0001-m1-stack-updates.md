# ADR 0001: Stack updates found while building M1 (16 Nov 2026)

Status: accepted (technical choices; none change product scope).

| # | Handover said | Built instead | Why |
|---|---|---|---|
| 1 | Spring Boot 3.5 + Spring AI 1.1 + Embabel 1.0 | **Spring Boot 4.1.2 + Spring AI 2.0.2**; Embabel to be added at M2 as **1.5.x** | Spring Initializr no longer offers Boot 3.x. Embabel 1.5.4 itself is built on Boot 4.1 / Spring AI 2.0, so the "stay on 3.5 for Embabel" reason no longer holds. Java 21 toolchain unchanged. |
| 2 | Temporal Spring Boot starter (implied) | **Plain `temporal-sdk` 1.40.0**, wired in `TemporalConfig` | The starter is compiled against Boot 2.7; Boot 4 moved its auto-config modules. Manual wiring is ~40 lines and has no compatibility risk. |
| 3 | Temporal via docker compose (full server) | **Temporal dev server** (`temporalio/temporal:1.10.1 server start-dev`) with SQLite file persistence | One light container instead of four; persistence survives restarts (tested: control plane and Temporal both restarted mid-workflow, video resumed). Swap to a full cluster later without code changes. |
| 4 | MinIO for hot media | **SeaweedFS 4.54** (S3-compatible, Apache-2.0) | MinIO's public Docker images are gone (`minio/minio` returns "object not found" on Docker Hub). Workers talk S3, so the choice is swappable. |
| 5 | Langfuse in M1 | **Deferred to M2** | Langfuse v3 needs several more containers; M1 makes almost no LLM calls. Add it when the agents arrive. |
| 6 | WebSocket live updates | **Polling (1.5–3 s)** in the M1 dashboard | Simplest correct option for a single local user. Replace with SSE/WebSocket when the shot grid needs it. |
| 7 | — | **Server runs in UTC** | Windows reports India as the legacy zone id `Asia/Calcutta`, which PostgreSQL 17 rejects at connect. UTC also keeps stored timestamps unambiguous; the dashboard shows local time. |
| 8 | Postgres on 5432 | **Host port 5433** | The owner's laptop already runs its own PostgreSQL on 5432. It was not touched. |
| 9 | — | **`LlmToggle`**: when `GEMINI_API_KEY` is empty, Spring AI's chat model is switched off | Spring AI's Gemini auto-config refuses to start without a key. With the toggle the app always starts and the Planner uses its rule-based fallback. |

Resolved 16 Nov 2026: `gemini-2.5-flash` is no longer available to new keys; `gemini-3.7-flash` and `gemini-3.8-flash` hung (>75 s) on a tiny request; `gemini-3.6-flash` answered in ~6 s and is the default. The Planner has a 90 s limit and falls back to rules on timeout.
