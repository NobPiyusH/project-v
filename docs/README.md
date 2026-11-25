# Docs index

## Start here
- [DEVELOPER-TAKEOVER.md](DEVELOPER-TAKEOVER.md): takeover guide for a developer starting cold. Wins over the book.
- [HANDOVER.md](HANDOVER.md): the original requirements and design book (Java-era in places, see its banner).
- [handover-25nov-2026.md](handover-25nov-2026.md): latest status notes from the CTO session.
- [SESSION-HANDOFF.md](SESSION-HANDOFF.md): early session hand-off, 16 Nov 2026.
- [java-removal.md](java-removal.md): what was removed with the Java control plane and how to restore it.

## Architecture
- [adr/0001-m1-stack-updates.md](adr/0001-m1-stack-updates.md): stack changes found while building M1.
- [adr/0002-m2a-story-pipeline.md](adr/0002-m2a-story-pipeline.md): the M2a story pipeline.
- [video-lanes.md](video-lanes.md): diagram and GPU-broker video lanes (off by default).
- [film4k.md](film4k.md): the 4K film pipeline on rented pods.
- [quality-gate.md](quality-gate.md): the automatic quality gate.
- [svi-scenes.md](svi-scenes.md): SVI scene continuation.
- [stock-footage-lane.md](stock-footage-lane.md): stock footage lane (off by default).
- [autopilot.md](autopilot.md): daily autopilot for Dreamscapes Shorts.
- [pod-reaper.md](pod-reaper.md): external kill-timer for GPU pods.
- [warm-start-plan.md](warm-start-plan.md): warm start for rented GPU pods.
- [studio-redesign-plan.md](studio-redesign-plan.md): studio redesign (design only).
- [dashboard-phase1.md](dashboard-phase1.md): the new dashboard shell.

## Runbooks
- [runbooks/first-live-short.md](runbooks/first-live-short.md): first live Short.
- [runbooks/ssd-move.md](runbooks/ssd-move.md): moving heavy data to the media SSD.
- [m3-gpu-router-runbook.md](m3-gpu-router-runbook.md): GPU broker runbook (Java-era names).

## Plans
- [plans/four-month-plan.md](plans/four-month-plan.md): four-month plan.
- [plans/post-tv-queue.md](plans/post-tv-queue.md): queue after the television video.
- [plans/roadmap-formats.md](plans/roadmap-formats.md): one studio, many formats.
- [plans/ai-producer.md](plans/ai-producer.md): AI Producer mode.
- [plans/M3-router.md](plans/M3-router.md) and [plans/m3-gpu-router.md](plans/m3-gpu-router.md): M3 router and GPU broker design.
- [plans/camera-control-pipeline.md](plans/camera-control-pipeline.md): camera control in the pipeline.
- [plans/java-to-python-migration.md](plans/java-to-python-migration.md): the staged Java to Python migration.
- [plans/dream-shorts.md](plans/dream-shorts.md), [plans/dream-choose-the-path.md](plans/dream-choose-the-path.md), [plans/dream-learning-loop.md](plans/dream-learning-loop.md): Dream Shorts plans.
- [gpu-benchmark-plan.md](gpu-benchmark-plan.md): GPU benchmark plan.

## Reports and findings
- [pod-tests-24nov.md](pod-tests-24nov.md): SeedVR2, Fun Camera and Fun VACE pod tests.
- [vetting-lab.md](vetting-lab.md): tool vetting lab.
- [e2e-provider-notes.md](e2e-provider-notes.md), [jarvislabs-provider-notes.md](jarvislabs-provider-notes.md): GPU provider findings.
- [refactor-2026-11-22-java.md](refactor-2026-11-22-java.md), [refactor-2026-11-22-workers.md](refactor-2026-11-22-workers.md), [refactor-2026-11-22-dashboard.md](refactor-2026-11-22-dashboard.md): refactor notes.
- [handoffs/](handoffs/): session hand-offs, 19 to 23 Nov 2026.

## Shorts
- [shorts/two-shorts-plans-24nov.md](shorts/two-shorts-plans-24nov.md): two Short plans.
- [shorts/milky-seas-director-plan.md](shorts/milky-seas-director-plan.md): Milky Seas director plan.
- [shorts/lake-natron-script.md](shorts/lake-natron-script.md): Lake Natron script.
- [shorts/game-cinematic-2min-plan.md](shorts/game-cinematic-2min-plan.md): 2-minute game cinematic plan.
- `shorts/*.json`: machine-readable plan files for the Shorts above.

## Analytics
- [analytics/README.md](analytics/README.md): old vs new code metrics (Java vs Python), with the JSON snapshots.
