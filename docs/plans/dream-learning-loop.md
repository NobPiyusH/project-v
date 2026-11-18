# Dream Shorts: a studio that learns, and a scout that keeps every Short original

Status: **plan approved in principle by the owner (19 Nov 2026)**; phases are built one by one, each demoed and approved.
Branch: `dream-shorts`. Nothing here merges, spends money or touches the owner's accounts without his OK.

## Why

The first Dream Shorts tests went wrong in ways a person had to spot: the 720p estimate was half the real cost, hard
cuts were silently dissolves, the music matched nothing, the camera glided like AI, the haze looked fake. The owner
wants an AI that keeps learning from mistakes and improves the code, and a second AI that, for every new idea, checks
what similar Shorts already exist, how they are made, and how his can be different.

## Who does what

| Job | Who | Why |
|---|---|---|
| Watch and listen to a finished Short; judge it next to the owner's examples | Gemini 3.6 Flash | Takes video and audio directly |
| Find similar Shorts and watch them | Gemini 3.6 Flash with Google Search, plus the YouTube Data API | Gemini can watch a public YouTube video from its link; the API finds them |
| Measure (motion, colour, shadows, shot length, beats, loudness, cost vs estimate) | No AI: FFmpeg and librosa on the laptop | Exact, repeatable, free |
| Turn evidence into lessons; write the "how to be different" brief | Claude Sonnet 5.5 | Careful reasoning over many notes and numbers |
| Change the code | Claude Code, a scheduled weekly session | Reads the project, runs the tests, prepares a branch |
| Final say on rules, merges and spending | The owner | Always |

## Part 1: the learning loop

1. **Evidence**, recorded for every Short: the owner's approves, redos and notes; the measurements above; estimated
   vs real cost per step; every failure and retry; later (with the owner's OK for channel access) views and watch time.
2. **Automatic check** of every finished Short (≈ ₹0.5–1, *estimate, to be measured*): Gemini watches it next to the
   golden set (the owner's example clips) and scores it (feels real, human camera, colour and light, music fit),
   listing concrete problems ("shot 2: sign text garbled"). Shown next to the video.
3. **Lessons list** on the screen, editable: rule, evidence, status (proposed, active, retired). A weekly Claude review
   proposes new lessons from the evidence; one bad Short never makes a rule.
4. **Improving**, from safest to most careful:
   - A. Active lessons go into the writer, picture and camera prompts (new rules need the owner's OK).
   - B. Settings (clip speed, shadow lift, grain, music candidates) change only after an A/B shows equal or better
     quality on the golden set; cost estimates recalibrate every night from the ledger, automatically.
   - C. Code: the weekly Claude Code session reads the lessons, failures and errors, writes fixes with tests on a
     branch and asks the owner to review. Never merges by itself.
5. **Guardrails**: no spending without approval; a change ships only if it is at least as good on the golden set;
   every change logged with its reason and reversible; all tests pass first.

## Part 2: the scout (originality check), a new first step "Scout"

Runs right after the owner presses Start, before Write, with its own Auto / Review / Manual gate.

1. **Find**: search YouTube Shorts for the idea (YouTube Data API: free quota, about 100 searches a day) plus Google
   Search through Gemini for the same idea elsewhere. Keep the top 5–8 by views and recency.
2. **Watch**: Gemini watches each one from its public link and notes what it shows, the look, the camera, shot
   length and count, the music and how it syncs, the hook in the first second, and the likely tools.
3. **Brief** (Claude): what most of them do (the cliché to avoid), what none of them do (the gap), and 3 concrete
   angles that make this Short different (a new object or place, a new camera idea, a new time of light, a twist at
   the end). The owner picks one, or Auto takes the first; it goes to the writer as notes.
4. **Too-similar check** after Write: our shots are compared with what was found; a near-copy is flagged and rewritten.
   We learn styles, never copy anyone's Short.

Cost per idea: about ₹3–5 (*estimate, to be measured on the first runs*: a few searches plus Gemini watching about
5 Shorts). Instagram Reels and TikTok have no public search, so they are covered only through Google Search results,
not watched directly.

**Needs from the owner:** a YouTube Data API key from his Google account (free; he creates it, it goes in `.env` as
`YOUTUBE_API_KEY`).

## Phases

1. Evidence + nightly cost recalibration (free).
2. Scout step (needs the YouTube key; ≈ ₹3–5 an idea).
3. Automatic check on every finished Short + lessons list on the screen.
4. Lessons into the prompts (owner approves rules).
5. A/B tuning of settings (test Shorts; owner approves the budget).
6. Weekly Claude Code improvement session (branches for review).
7. Later: YouTube results feed in (owner's OK for channel access).
