# Ant video run: things to improve (live notes, 25 Nov 2026)

Run: "What an ant hears at night", video a013569d, 90 s, 18 clips, owner-approved total Rs 180 (Rs 193 allowed if the L40S fallback is used). Notes are added as problems show up. Each item says how bad it is and the fix.

## Found so far
1. **Pod rental failed with an unexplained 400 (HIGH).** The Secure 4090 create call was rejected and the studio logged only "400 Bad Request", not RunPod's reason. Fix: log the response body; retry with backoff; show "no pod yet, retrying" on the stage card.
2. **Budget cap blocked the fallback GPU (MEDIUM).** The L40S rental (about Rs 149) exceeded the video's remaining cap, so the broker had one choice only. Fix: the hold message should offer "raise cap to X" as a button; the estimate should be shown before the stage starts.
3. **Reaper limit shorter than the job (HIGH).** The external reaper's 1.5 h limit would have killed a pod doing a 1 h 30 min job. Fix: the reaper should read each pod's lease expiry/expected duration instead of one global number; start it as a scheduled task, not a hand-run loop.
4. **Two reaper loops ran at once; the old one kept the old limit (MEDIUM).** Fix: one scheduled task, with a lock so a second copy cannot start; the log line should print the script version.
5. **Abandoned first attempt stays on screen (MEDIUM).** Two videos share a title; the old one sits in "Needs you". Fix: Archive/Cancel button and route for videos (queued, owner chose it).
6. **Stage status looks stuck (LOW).** DRAFT_GEN shows RUNNING with no progress. Fix: show "clip 8 of 18" and a time left on the card, read from the media worker.
7. **First shot plan came out as 45 fast shots (MEDIUM).** The short-form pacing was applied to a 90 s calm film. Fix: imported plan files are now the route for this; also let the idea board choose the pacing.
8. **No studio-built sound bed (HIGH for this concept).** AUDIO is a placeholder, yet the idea relies on sound. Fix: build the synthesised sound layer (soil thumps, air pulses) and a loudness pass.
9. **Cost views disagree (LOW).** The video list `spent_inr` leaves out pod rental while the cap counts it. Fix: one number everywhere.
10. **Shot-plan inspector gives a false alarm on plan files (LOW).** It says FAIL on the old 45-shot plan and flags a Short length rule on a 90 s film. Fix: check against the plan's own duration.
11. **Slow first clip (LOW).** Pod setup plus model load took about 18 min before clip 1; later clips take 3.5 min. Fix: warm image or network volume for the model.

## Clip review (contact sheets, 25 Nov, 22:55)
12. **The look is consistent (good).** All 18 clips share the cool blue moonlit meadow floor, same height, same colour. Camera moves (push, drift, pan) are visible across frames; no still-image slideshow in the sheets.
13. **The "sound made visible" effects are weak (HIGH).** Ripples, trembling dew, the raindrop shockwave and the giant-step shake barely show in the 4-frame sheets; most clips read as a slow camera move over still grass. The rain drop is a small blur in clips 7-8. Fix: judge by eye at full speed; if still weak, add the effect in compositing (ring ripple, dust, dew jitter) or use stronger prompts and a second pass on those beats.
14. **Some clips are dark and soft (MEDIUM).** Clips 11, 12 (tunnel mouth, mostly black hole) and 15 (quiet beat) are dim and out of focus. Fix: brighten and sharpen in grade; or rerun just those beats.
15. **CRITIC wants its own approval now (LOW).** It is a placeholder stage that asks Rs 5. Fix: placeholder stages should not hold for money.

## To check when the clips finish
- Does each beat really move (no panned still)? Is the colour and light the same across all 18?
- Do the two beats in each scene match?
- Does the narration line fit its beat?
