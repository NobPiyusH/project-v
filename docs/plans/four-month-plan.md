# Project V: four-month plan, 26 Nov 2026 to 31 Mar 2027

Written by the CTO on 25 Nov 2026. Costs are estimates (Rs 94 per dollar) from measured pod tests and the research report; every spend still needs the owner's own "yes" with a figure. Dates after January will move: re-plan at each month end.

## North star
Ship real videos on the channel (Strange If True) with the quality bar: real camera motion, consistent characters, clean audio, no slideshow. Engineering exists to make shipping cheaper, safer and more repeatable. Rule for the whole plan: **no new research or tooling work until the pipeline has made and published real videos**; a gate at the end of each month decides what comes next.

## Budget frame (owner to set; suggestion)
- GPU: plan on about $50 a month (Rs 4,700); hard daily cap stays $3.50. Month 1 is lower (about $25) because most work is code.
- API: scripts and judge calls are cents; ElevenLabs paid plan only when the first monetised or client work needs it (owner decision).
- Fixed owner jobs (no cost to the plan): rotate pasted keys, pay the Google billing, watch the camera-scene clip.

## Owner priority (25 Nov 2026): motion and animation first, then a real video through the Python studio
Month 1 therefore runs a **motion block** before the smoke test: Blender camera path into VACE (running, $1 cap), the untested camera presets in one pod run (about $1), a 4-5 beat chained-drift test (about $1), Wan 2.2 Animate for character and wing motion after a licence check (about $1.5), then interpolation and upscale on the best result (about $1). Suggested cap for the block after the Blender test: $4.5, owner's yes and figure required. The smoke-test Short ($2) follows, using the best motion setup. The items below keep their numbers but the motion block goes first.

## Month 1: 26 Nov to 31 Dec. "Prove it end to end and make it safe"
Goal: one real video made by the Python studio start to finish, with no forgotten pods and no leaked keys.
1. Free cleanup (week 1): banner on `docs/HANDOVER.md`; secret scanning (gitleaks pre-commit); commit the three new docs; clear the 15 stuck Java workflows and remove the JDK (needs the owner's OK).
2. Blender camera path into VACE test (running, $1 cap): decides whether the camera presets get a 3D path option.
3. Smoke-test Short (week 1-2, about $2 / Rs 190): one 30-second Short through live Temporal, `run_shard.py` on a pod, quality gate hooked in. Fix whatever breaks. This is the highest-risk item in the project.
4. Pod reaper (week 2): dry run on the first real pod to confirm API field names, then schedule it every 10 minutes (needs the owner's OK to register the task). Add a balance, spend and lease widget to the dashboard.
5. Repair and quality (weeks 2-3): blue-wing repair with VACE (about $0.5); tune the gate thresholds on more clips; judge off.
6. Script-aligned captions (WhisperX alignment) and publishing set-up (upload Private first, AI-disclosure flag, scheduled `publishAt`).
7. Publish 2 to 3 Shorts using the proven path (about $3-6).
Gate at 31 Dec: smoke-test Short passed; 2-3 Shorts published; no unplanned pod spend; reaper scheduled. If the smoke test failed twice, month 2 becomes stabilisation only.

## Month 2: January 2027. "Control and a repeatable Short"
Goal: make a Short or two per week with little manual rescue.
1. Shot-level review: backend (approve, reject, request fix per beat, rerender only that beat and its dependents) then the dashboard screen; cost estimate before a run and live spend with a stop control.
2. Storyboard and beat editor on top of the character and location bank (already built); show resolution honestly (generated 720p, delivered 4K).
3. Continuity checker as a review aid that flags drift, never decides.
4. Tests that need the owner's yes and a figure, one at a time (about $1-3 each): SeedVR2 vs Real-ESRGAN verdict; interpolation order and GIMM-VFI; Wan 2.2 Animate for recurring characters.
5. Publish 6 to 8 Shorts and the first long film (Act 1 and Act 2 of the game film; Act 2 needs a new figure, about $6 estimated).
6. ElevenLabs commercial-rights check before any monetised upload.
Gate at 31 Jan: median Short needs at most one targeted fix; cost per Short under the owner's target (earlier target: under Rs 20 per Short for the cheap lane, premium film lane separate).

## Month 3: February 2027. "Quality and the channel bar"
Goal: raise the ceiling and learn what the audience watches.
1. Hero-shot tier A/B (hosted models vs self-hosted Wan, about $10-15, owner's yes): decide if any scene type is worth paying for.
2. LTX-2.5 or LongCat-Video test (about $3-8 each) only after reading their licences; adopt only on a clear win.
3. Narration blind A/B (under Rs 100) and music: owner listens blind; fix the voice question that has been open since 18 Nov.
4. Audience loop: comments choose the next Short's path (growth pick of 20 Nov); measure retention and let it steer topics. Re-verify YouTube monetisation thresholds (the research report cites a 1 Feb 2027 bar of 8,000 hours or 20 million Shorts views; confirm on the official page, do not rely on it as a promise).
5. Publish at a steady cadence (for example 2 Shorts a week and 1 film every 2-3 weeks).
Gate at 28 Feb: at least 10 published videos; owner's verdict on quality vs the current best; a decision on voice and music provider.

## Month 4: March 2027. "Scale or productise (owner decides)"
Goal: either run more channels' worth of output cheaply, or open the studio to paid client work.
- Path A, personal: Dreamscapes channel (second channel in the plan), a bigger cadence, automation of Review/Auto modes, faster warm-start pods.
- Path B, services: finish the client overview terms (price, turnaround, revisions, rights, contact), confirm commercial licences for every model and voice, add an SLA buffer, then a pilot with one client.
- Both: resilience pass (backups with `scripts/backup.ps1`, restore drill, dependency updates, key rotation schedule), documentation refresh, full test run and code analytics compared with the November baseline.
Gate at 31 Mar: decide A or B for the next quarter, with real numbers (cost per video, published count, watch time).

## Standing work every month
Rotate keys on a schedule; review pod spend against the cap; keep `docs/DEVELOPER-TAKEOVER.md` current; archive finished sessions; keep the vetting lab for any third-party tool; no unverified prices in code.

## Risks and how the plan handles them
- Python studio fails on a real run (HIGH): month 1 item 3 before anything else; stabilise before adding features.
- Pod cost leaks (HIGH): lease, killer, reaper scheduled, daily cap; never leave a pod unattended.
- Licence trap (MEDIUM): read the licence before adopting any model (MiniMax H3 and Hunyuan-family models may exclude the EU, UK, South Korea and the US; verify).
- Quality drift in generated shots (MEDIUM): character bank, camera presets, repair step, human review.
- Owner time (MEDIUM): keep decisions short and batched: spend figures and publish approvals only.
- Usage limits on the engineering side (LOW): small sessions, medium effort, merge often.

## Decisions needed from the owner
1. Smoke-test Short: yes and a figure (suggested $2).
2. Monthly GPU budget frame (suggested $50).
3. OK to clear the stuck Java workflows and uninstall the JDK.
4. OK to register the pod reaper as a Windows scheduled task.
5. Month 4 direction (personal or services): decide at the end of February.
