# Pod tests, 24 Nov 2026: SeedVR2, Wan 2.2 Fun Camera, Wan 2.2 Fun VACE

Owner approved in chat: "yes do both tests, $1 cap, also wan vace". One RunPod pod (second attempt), spend **$0.60 of $1.00**
(first Community pod never finished starting: $0.14; Secure 4090 $0.89/h: $0.46). Pod terminated and verified (no pods left).
Scripts, clips and comparison files: `E:\project-data\outputs\pod-tests\` (not in git). All clips are 2 s at 480p: these prove the mechanism, not final quality.

| Test | Result | Cost / time | Verdict |
|---|---|---|---|
| SeedVR2 3B fp8 vs Real-ESRGAN (33 frames to 4K) | A little crisper (fire, rubble edges, dragon); not dramatic | SeedVR2 4.3 min per 33 frames, about 10 min and $0.15 per 5 s beat; ESRGAN 3-5.5 min, $0.04-0.08 | Hero shots only; ESRGAN stays for bulk |
| Wan 2.2 Fun Camera (core ComfyUI WanCameraEmbedding) | Zoom In = real forward move; Pan Left = believable; Anti-Clockwise = strong roll, last frame warps | about 50 s per 2 s clip on a 4090 | Best result: fixes the static-camera problem of SVI |
| Wan 2.2 Fun VACE with an edge control video | Followed a push-in path closely; keyframe and dragon stayed faithful | about 51 s per clip | Keep for custom camera paths (Blender camera later) |

Not tested: 720p, chaining with SVI, longer clips (720p cost is an estimate). Next step proposed: one real scene with Fun Camera push-in at 720p (about $0.30, needs the owner's yes).
Lessons: Community pod hosts can hang on image pull (14 min, $0.14): keep the wait limit and the kill-timer; a Secure pod booted in about 6 min.
