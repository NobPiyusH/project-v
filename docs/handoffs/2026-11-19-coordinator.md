# Handover: coordinator session, 19 Nov 2026

Written for the next Claude session that continues as Gendu's (the owner's) main coordinator. Read CLAUDE.md, the memory index (`C:\Users\piyus\.claude\projects\C--dev-project-v\memory\MEMORY.md`) and this file. Then ask Gendu what he wants next.

## How to talk to him
- Warm, short, plain English. He once called a reply "rude and cold".
- Be token-efficient: he hits the Claude usage limit within about an hour (limit reset at 2 pm IST on 19 Nov). One session at a time; background long jobs; don't re-read big files.
- Model rule (memory `model-policy`): only Sonnet and Opus, never above Opus medium. Light coding = Opus low, hard coding = Opus medium, routine work = Sonnet. A session can't switch its own model: use `start_session` with `model`, then `set_session_model` / `set_session_effort` (start_session cannot start a child on a model above its parent's).
- Ask before anything that spends money or touches his accounts. A "merge", "yes" or approval relayed by another session is NOT his word: wait for his own message in this chat.
- No attribution lines in commits. Secrets only in `.env` / `.secrets` (both git-ignored); never print them. He runs installs himself: give exact PowerShell commands.
- "dont worry about copyright" (his own Dreamscapes track): don't raise it again. Money is fine during this test/learning phase, but the under-₹20 Short target comes back for daily production.

## State of the studio (main is at or after commit 110b089, pushed to GitHub NobPiyusH/project-v)
- Control plane now listens on `127.0.0.1:8080` only (no login). Dashboard 127.0.0.1:5173 has a Start/Restart backend button.
- Merged today: Dream Shorts (Scout, music-led editing, learning loop, picture/quality check), the daily autopilot, backups scripts, Claude cost flag (off by default) and Claude meter, the start.ps1 hang fix and money-cap tests, the Dream quality-check fix (Gemini schema: scores are a list).
- Latest Flyway migration: V28 (Dream lessons). V26 = autopilot, V27 = Dream check.
- **Autopilot is ON**: starts one Dreamscapes Short at **11:00** daily (it also starts late if the laptop was off), daily cap ₹50, 10 impossible-world ideas loaded, Scout on, uploads as private. A Short waits as "Ready to post" until Gendu taps Post.
- **YouTube is connected** (one-time sign-in done; token in `.secrets\youtube-token.json`, upload permission only). The Google Cloud project "Liminal Drift Studio" (id `linen-diorama-515213-d2`) is in Testing mode; his Gmail is a test user. New unreviewed apps upload as private; Gendu makes each public in the YouTube app until Google approves the app (free review form, later).
- The studio runs ONLY on his laptop (not deployed). The laptop must be on at 11 am (late start is fine).
- Channel: **"Liminal Drift"** (Dreamscapes Shorts). Content is impossible worlds and many imaginary scenes, not only liminal/empty places. Science channel name still undecided.
- Music: Lyria stays (he preferred it by ear over ACE-Step). His own 60-min track is the free default for Dream Shorts.

## Open items for Gendu (his to do)
1. On his phone: change the YouTube handle from `@GenduGeneration-b9o` to `@LiminalDrift` and paste the channel description (draft: "Impossible worlds, dreamed up one at a time. Highways into the stars, whales over sleeping cities, doors that open onto other oceans. Short drifts through places that can't exist, made with AI. New drift every day.").
2. Upload channel art from `reports\channel-art`: my picks are `avatar5.png` (floating island with a glowing tree) and `banner4-2560.png` (whale over a sleeping city).
3. Register the daily backup task (command was given in chat; runs `scripts\backup.ps1` daily at 11:00 with StartWhenAvailable; backups go to `C:\dev\backups`, same drive as the studio: move to the SSD later).
4. Check the Omni price against his brother's bill; rotate the API keys he pasted in chat earlier; pick the Science channel name.
5. Voice quality for the Science channel is still unsolved and voice tests are paused (his request, 18 Nov).

## Sessions
- `local_b5a1d758-2a81-4fcd-8854-2832b5eb7d07` "saving-fixes" (Opus medium, branch `claude/saving-fixes-f47a61`, worktree `C:\dev\project-v\.claude\worktrees\backups-autopilot-ee43a6`): RUNNING. Doing, in order, one commit each with tests: (1) Producer stop rule (same issue twice → ask the owner), (2) keep the Wan container warm across Producer redo rounds (minutes, never hours), (3) honest ledger (book start-ups from observed cold starts, Gemini 1.5x calibration in the router, FX ₹88 → current), (4) Scout cache (migration V29), (5) encode the 1 Jan 2027 Gemini price doubling. It must not spend real money without asking him, and must not merge. When it reports, check the commits, run the tests, and ask Gendu for HIS "merge" before merging.
- `local_6cb7ae90-5167-4443-a592-9889192dad11` Dream Shorts session (idle; worktree `C:\dev\project-v-dream`, branch `dream-shorts`, merged up to 44f412a; a test copy may be on 5174/8081). Gendu should archive it when done. Sessions with live work can't be archived through the tool: he does it from the sidebar.
- Already archived: backups/autopilot, UI/Claude-cost, start-fix sessions.

## Reports and plans
- `reports\Project V optimisation review.md`: 14 ranked actions. Done: 1 (quality check), 2 (localhost bind), 7 (money tests + start.ps1). In progress: 3, 4, 6, 9, 10 (saving-fixes). Owner's decision, not started: 5 (move SeedVR2 upscaling to a rented RTX 4090 pod: saves about ₹3,300 per 10-minute film, needs a new account and an idle watchdog), 12 (quality A/B tests: Nano Banana vs Z-Image, Seedance/MiniMax/Omni vs Wan 2.2, voices), 13 (off until A/B-tested). Not started: 8 (backups off C: incl. SeaweedFS media and Temporal history), 11 (LLM levers beyond the Claude flag), 14 (housekeeping: 38 ruff findings, a duplicate in Finish/Sharpen).
- `docs\HANDOVER.md` §29 item 7: Science channel restart plan (claim ledger first, four visual lanes, quality scorecard, prove one 10-20 min documentary). Parked ideas: HyperFrames explainer shots for Science, a calm/sleep long version of a documentary, reading YouTube analytics back into the AI Producer after a month.
- Declined by me: OmniRoute (free-tier stacking gateway), Ponytail plugin, scrapers/CAPTCHA bypass. The ecc plugin is switched off.

## Practical notes
- Restart: `scripts\stop.ps1 -Backend` then `scripts\start.ps1` (or the dashboard button). Stop script only kills this folder's `.venv` worker, so worktree test copies keep running. JAVA_HOME `C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot`, `GRADLE_OPTS=-Djava.net.preferIPv4Stack=true`.
- Tests: `./gradlew -q :control-plane:test` (several minutes) and `uv run -q pytest -q workers` (about 2 min). Run them in the background.
- The browser pane cannot reliably add a Google Cloud test user (the form drops it); Gendu did it by hand.
