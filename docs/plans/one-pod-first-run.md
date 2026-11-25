# One-pod mode (`VIDEO_ONE_POD`): what to check on the first live run

Built 25 Nov 2026, tested with fakes only (studio/tests/test_one_pod_workflow.py, test_one_pod_gpu.py). Never run on a real pod. Needs the owner's go and the usual spend approval (rule 2). Settings and behaviour: docs/runbooks/first-live-short.md, section 5.

## Before starting

- `VIDEO_PROVIDER_KEYFRAMES=broker`, `VIDEO_PROVIDER_DRAFT=broker`, `VIDEO_ONE_POD=true`, `PROJECTV_VIDEO_AUTO_APPROVE_INR` above the draft estimate (otherwise the clip approval prompt releases the pod: legal, but then there is no saving).
- All stages AUTO. Run `tools/gpu_lease/lease.py` as for any pod; the studio watchdog stays on.

## Watch for, in this order

1. **One rental.** Exactly one `GPU broker: rented ...` line for the video before DRAFT_GEN, and one RoutingDecision for KEYFRAMES; DRAFT_GEN logs `Video ... keeps its GPU pod` then reuses it (no second `rented`).
2. **Prefetch.** The pod's `/health` timing shows the `fun-camera` downloads after `ready`; the keyframes were not delayed by them. If the FLUX download and the 38 GB Fun Camera prefetch fight for bandwidth and the keyframes get much slower than a plain boot, say so: the fix is to start the prefetch after the last keyframe.
3. **Idle gap.** Time from `keeps its GPU pod` to DRAFT_GEN's first clip job (inspection, creative image review, library match, animatic run while the pod is held). If it is near 5 min the timer will end the pod and DRAFT_GEN reboots: raise `VIDEO_ONE_POD_IDLE_MINUTES`.
4. **Cost.** One CostEntry for the pod with the whole span; today's total under $3.50. The reserve checked at rent time is an upper estimate (every clip counted as a camera clip), so a nearly-spent day can refuse the span and fall back to the pictures-only rental (log: `one-pod rental refused ... trying a single-stage rental`): that is intended, not a bug.
5. **Release.** After the draft clips are saved the pod is terminated (provider console shows no `projectv-*` pod, lease released). Also try once, on a cheap run: set a stage between to REVIEW and check the pod is terminated before the window and a second one boots after.
6. **Restart of the control plane while a pod is held** is not covered by the hold timer (it lives in memory). The workflow keeps going and DRAFT_GEN joins the recovered pod; if the workflow was lost instead, the watchdog ends the pod after 15 min without a heartbeat (or at the 3 h limit). Do not restart the control plane while a pod is held unless the video is being abandoned.
