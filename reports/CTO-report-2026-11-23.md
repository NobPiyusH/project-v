# CTO report: Project V / Cinestack, 23 Nov 2026

Written for the owner (Gendu). Plain English. Risk labels: LOW / MEDIUM / HIGH / CRITICAL. Numbers come from the repo today; anything I did not measure is marked "not checked".

## 1. Where the project stands
- **Age and size:** 314 commits in 8 days (16 to 23 Nov). Java control plane 22,197 lines (+8,926 test lines), Python workers 12,667 lines (32 test files), React dashboard 4,585 lines of TypeScript.
- **First product shipped:** the television explainer is public on the channel "Strange If True" (paid ElevenLabs voice, audio fitted and checked against the real mix).
- **Channel identity done:** name, handle, banner, profile picture, thumbnail rule.
- **Direction decided (23 Nov):** Python only, serverless GPU, plan-then-make pipeline. The Java to Python migration is running as a staged job: stages S0 to S4 (skeleton, ledger, config/providers, routing/agents, GPU watchdog and broker with all four provider adapters) are done with 253 passing tests, all against fakes. Java is still the live version, nothing is pushed.

## 2. What was measured this week (video models, GPU, Blender)
- **Wan 2.2 vs LTX 2.5 on an L40S, per 5 s clip:** LTX 112 to 123 s, Wan 181 to 191 s. LTX adds generated sound and 24 fps. Prompt match was mixed: LTX better on 2 shots, Wan better on 1 (and on the long-prompt test), 1 tie. The owner's eye decides quality; I only supplied numbers.
- **Rented GPUs vanish.** Every RunPod and Vast machine I started this session disappeared about 10 to 20 minutes after boot, with no deadline hit and no memory or disk alarm in the logs I captured. Cause unknown. Vast was also slow to download (19 MB/s vs 60 to 230 on RunPod). (HIGH, see section 4.)
- **Blender at 4K on an L40S:** EEVEE Vulkan renders a 4K frame in about 3 s once loaded (laptop RTX 3050: about 7.6 s for a 1080p frame). Full 4K videos did not finish because the pod vanished.
- **Spend this session:** about $2.7 of the $5 approved. RunPod balance $5.08. No pod is running.

## 3. Health of the codebase
- Tests exist on both sides (Java 54 test files, Python 32 plus the migration's 253). The "quality gate" that checks every finished cut automatically is designed but not built.
- Known open bugs (fix list): swapped diagram label fields, diagram parse fallback, an encoding bug ("Â°"), a wrong AI-review flag on diagram shots.
- **Version control is messy.** 15+ unmerged feature branches, 5 worktrees (8.3 GB), 60 uncommitted files in the main checkout, 5 GB of reports. Nothing recent is pushed anywhere. (HIGH, see section 4.)
- Disk: C: 181 GB free (61% used), E: 739 GB free. Project data lives on E:. Local services (Postgres, Temporal, SeaweedFS) are healthy.

## 4. Risks, ranked
1. **HIGH: no off-machine backup.** Work lives on one laptop and one external-style drive. A disk failure loses the studio. Fix: with the owner's "push", push to a private GitHub repo, then merge or archive the old branches.
2. **HIGH: pasted keys.** ElevenLabs, Hugging Face, RunPod, Vast (key and two-factor seed), E2E and others were shown in chat. Rotate them (steps already given).
3. **HIGH: GPU reliability.** If rented pods really die after about 12 minutes, the "one pod per video" plan cannot work, and the cost per video is unpredictable. Next step: one test of RunPod Serverless on a single Short before more migration depends on pods.
4. **MEDIUM: personal documents on the work drive.** `Downloads` holds identity documents and certificates. They must never be used as test input or uploaded. Keep them out of the project and out of any cloud job.
5. **MEDIUM: migration scope.** The remaining stages (workflow, video pipeline, HTTP API) are the largest. Keep Java live until each Python stage passes its tests and the owner approves.
6. **MEDIUM: licences.** LTX-2.5 is free below $10M revenue (full text not read). MiniMax H3 bars showing outputs in the US, EU, UK and South Korea, so it stays excluded. Wan 2.2 is Apache 2.0.
7. **LOW: Claude credits.** The owner has few. Long multi-hour sessions are expensive; prefer short, scoped jobs.

## 5. Recommended order
1. Rotate the keys (owner, 15 minutes).
2. Push to a private GitHub repo, then clean up branches (owner says "push").
3. One serverless-GPU test on a single Short (about $1 to $2, owner approves first).
4. Owner watches the Wan vs LTX clips and picks a default video model.
5. Build the quality gate (1 to 2 days), then continue the migration stage by stage.
6. Open queue after that: Earshot and HyperFrames tests, FLUX vs Z-Image, upscale test, ElevenLabs-only voice lock, one pod per video (only if item 3 shows pods are viable).
