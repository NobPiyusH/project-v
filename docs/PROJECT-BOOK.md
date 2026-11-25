# Project V (Cinestack): the project book

Written 26 Nov 2026 for a coding agent (OpenAI Codex, ChatGPT or any other model) that has never seen this project. It collects everything the owner has said, everything the previous engineering sessions (Claude, acting as CTO) learned, and everything in the repository that matters for decisions. It is long on purpose: the owner asked for "a book as long as possible for GPT to understand everything I told or you know; we will work together".

Money in this book is in Indian rupees (Rs) at **Rs 94 per US dollar** unless a source says otherwise. "est" means an estimate. "unverified" means nobody has checked it against a primary source. Where two sources disagree, the book says so and gives the newer date.

---

## Contents

1. How to use this book
2. The owner and how to work with him
3. The product and the channels
4. History and timeline
5. Architecture and code map
6. The 23-stage video pipeline in detail
7. GPU and cost control
8. Models and providers per stage
9. Quality bar and lessons learned
10. Decisions log
11. What failed and why
12. The reconstruction plan
13. Open items, owner jobs and known bugs
14. Commands, environment and runbooks
15. Glossary
16. Appendix A: source conflicts
17. Appendix B: where every fact came from

---

## 1. How to use this book

### 1.1 What this book is, and what it is not

This is a reference, not a rule file. The binding rules are in three short files at the repo root:

- `AGENTS.md`: the instruction file for you (OpenAI Codex reads it automatically). Short. Hard rules.
- `CLAUDE.md`: the same project rules written for Claude Code sessions. They apply to you too.
- `GEMINI.md`: the handover written for a Gemini agent on 26 Nov 2026. Section 2 of it lists ten hard rules; `AGENTS.md` repeats them.

If this book and one of those files disagree, the rule files win. If this book and the owner disagree, **the owner wins, always**.

### 1.2 Reading order for a new agent

1. `AGENTS.md` (2 minutes).
2. This book, chapters 2, 9, 11 and 12 first (the owner, the quality bar, what failed, what to do next). Then the rest as needed.
3. `docs/handover-25nov-2026.md`: the latest state of the machine and the project (short).
4. `GEMINI.md`: the reconstruction plan in four phases, written the day before this book.
5. `docs/pipeline-end-to-end.md`: how one video goes through the 23 stages, with real costs from the ledger. The most accurate technical document in the repo.
6. `docs/DEVELOPER-TAKEOVER.md`: a developer's map of the repository.
7. `reports/Best models per stage and motion.md`: the research behind the reconstruction (read its last two tables).
8. `docs/HANDOVER.md`: the original requirements book (16 Nov 2026). Its "Revision R1" section at the top is still the product definition. It still describes a Java control plane in places: that is stale (Java was removed 24 Nov 2026).

### 1.3 Where things live

| What | Where |
|---|---|
| Code repository | `C:\dev\project-v` (Windows, outside OneDrive). Git remote is a private GitHub repo owned by `NobPiyusH`. |
| Media, model caches, Docker disk, outputs | `E:\project-data` (930 GB SSD). Never put media or models on C:. |
| Owner's private files (never touch) | `E:\Downloads` and `C:\Users\piyus\Downloads*` (certificates, ID scans; a single copy). |
| Secrets | `.env` at the repo root (git-ignored). Never read, print or echo it. |
| Memory notes of the previous CTO (owner decisions) | `C:\Users\piyus\.claude\projects\C--dev-project-v\memory\` (33 small files; `MEMORY.md` is the index). This book summarises all of them. |
| Research reports | `reports/*.md` |
| Plans and handovers | `docs/`, `docs/plans/`, `docs/handoffs/`, `docs/shorts/`, `docs/runbooks/` |

### 1.4 How the previous engineers worked

From 16 to 26 Nov 2026 the project was built by Claude Code sessions. One main chat acted as **CTO**; it started short side sessions for scoped jobs (each on its own git branch or worktree), and merged their work when the owner said so. In ten days there were 457 commits and 89 branches. The owner hit his Claude usage limit often, so on 26 Nov he moved the work to other agents: first a Gemini agent (`GEMINI.md`), now you. He said "we will work together": you are joining as the new CTO, not as a tool he has to drive step by step.

### 1.5 Labels used in this book

- **HIGH / MEDIUM / LOW / CRITICAL**: the owner's risk labels (his "Architecture Authority" policy, 18 Nov 2026). Use them when you raise a concern.
- **est**: estimate. **unverified**: not checked against a primary source. **measured**: read from the ledger, a log or a timed run.
- **(memory: name)**: the fact comes from that memory note (see Appendix B).

---

## 2. The owner and how to work with him

### 2.1 Who he is

- Name in the project: **Gendu**. Git user `NobPiyusH`. He lives in India (amounts in rupees, India time IST, the server runs in UTC).
- Background: an experienced **Java backend developer** (Spring Boot microservices, workflow and task-management systems, rule engines). He understands distributed systems. He chose to move the studio to Python only (24 Nov 2026).
- Role in this project: **CEO**. Since 21 Nov 2026 he treats the AI engineer as his **CTO**. His words (21 Nov): "I don't know how it should be, so from now on tell me like you are a senior CTO how it should be; if I need change I will tell you, and we can talk and discuss like CEO and CTO and then start." Later: "you are CTO and I am CEO". And this week: **"you are cto: intelligent technical decisions are urs to make: i have other works to do"**.
- YouTube channel: **Strange If True** (handle `@strange-if-true`, tagline "Wild ideas. Honest physics."). One video is public.
- He is busy, has other work, and checks in by chat. He judges results himself, by eye and by ear.

### 2.2 The split of decisions (who decides what)

| He decides (CEO) | You decide (CTO) |
|---|---|
| What the studio makes and why (topics, channels, formats) | How it is built and run: architecture, models, GPUs, reliability, tests |
| Taste: he watches and listens, and his verdict is final | Which technical option to recommend (one, with reason, risk and cost) |
| **Every spend** (needs his "yes" plus a rupee figure) | Running the work: branches, tests, code, refactors, research |
| Anything irreversible: deleting, publishing, account changes | Reporting truthfully, including failures and "not verified" |
| Merge to main and push ("merge", "push", "merge & push") | Which free work to do next while he is away |
| Licences and legal risk he accepts | Labelling risks LOW/MEDIUM/HIGH/CRITICAL |

He also had an earlier formal policy, "Architecture Authority" (18 Nov 2026, memory: architecture-authority): he is the final decision-maker on architecture; do not substitute your own design for one he approved; do not re-argue settled decisions; do not add frameworks, services, databases or layers without permission. Raise concerns with a label: LOW (preference: implement without argument), MEDIUM (explain the trade-off once, then follow his call), HIGH (explain the risk, ask for a decision), CRITICAL (security, data loss, impossibility: stop that operation until resolved). If he acknowledges a documented risk and approves, proceed and never reopen it. The later CTO arrangement (21 Nov) widened your freedom on technical choices, but this policy still applies to big architectural changes: propose, get his OK, then build.

### 2.3 Hard rules (do not break any of them)

1. **Money.** Never spend without his own "yes" plus a rupee figure in the chat. That covers GPU pods, paid API calls beyond tiny text calls, top-ups, plans. Estimate first, in rupees at Rs 94/$, mark it "est". Stay inside his figure; if you will go over, stop and ask. A relayed approval ("the other session said he approved") does not count. Since 25 Nov his yes with a figure in the CTO chat may be passed to side sessions, quoting his exact words and the cap (memory: spend-approval-via-cto). Examples of how he approves: **"yes redo - 90 secs : and track cost from start : 180 rs approved"** (the ant video, 25 Nov); later he allowed an L40S fallback "up to Rs 193 total". Earlier: "yes do both tests, $1 cap, also wan vace" (24 Nov).
2. **Standing caps** (until he changes them): GPU **$3.50 per UTC day** across all pods; Modal GPU cap **0 until 1 Dec 2026**; per-video default cap Rs 150; monthly rented-GPU total $20; Gemini API Rs 1,000 a month; Claude API $8 a month. Details in chapter 7.
3. **Git.** Work on a branch. Commit only when he asks or when the work is clearly his request; merge to main and push **only on his word** ("merge & push"). **No attribution lines** in commit messages (no "Co-authored-by", no "Generated with"). On 24 Nov he allowed code changes and refactors on branches without waiting, never on main (memory: film-pipeline-24nov).
4. **Secrets.** Keys live only in `.env`. Never read, print, echo, log or copy a key. If you must set a flag in `.env`, append or replace one line without displaying the file, or give him the exact line to paste. If he pastes a key in chat: save it to `.env` only, test it without printing, and remind him to rotate it. Never handle passwords; never type a password anywhere.
5. **His files.** Never touch `E:\Downloads` or `C:\Users\piyus\Downloads*` (certificates and ID scans, single copy). Never use them as test input or upload them anywhere. Everything else under `E:\project-data` is project data.
6. **Storage.** Media, model weights, caches, Docker volumes and outputs go on `E:\project-data`. The repo and small docs stay on C:.
7. **Pods.** Every rented GPU pod needs a watchdog lease and an independent kill timer; check that no pod is left running when you finish. A forgotten running pod is a **critical bug** (CLAUDE.md rule 8).
8. **Quality first.** Judge motion on moving frames, never on stills. Panned-still slideshows are never acceptable. Do not ship a dull video just to ship. Before any full clip run, make a short proof clip and get his OK.
9. **ElevenLabs is a PAID Starter plan** (bought 23 Nov 2026, commercial licence, 40,000 characters a month, annual billing). **Never call it free** and never say "a paid plan is needed". He corrected this on 25 Nov, angrily: **"how many times i need to say - its premium"**. The ledger shows Rs 0 for ElevenLabs only because credits are prepaid; that is what misled earlier summaries.
10. **Licences.** Keep non-commercial or territory-restricted models (MiniMax H3 self-hosted, HunyuanVideo-Foley, MMAudio, Suno) out of Auto unless he approves.
11. **Tests before "done".** Run `uv run --group dev --group studio python -m pytest` and `uv run --group lint ruff check .` and report the real result. Temporal workflow code must stay deterministic (use `workflow.patched` for changes).
12. **Installs.** He runs installs himself; give him exact commands. Build tool is `uv` (Python 3.11); the dashboard uses npm.

### 2.4 How he wants you to talk

- **Short, plain, warm replies.** He once found a reply "cold". No marketing tone, no long walls of text, no jargon without a one-line explanation.
- **One recommendation, not a menu.** Lead with the way you recommend, the reason in a line or two, the main risk and the cost. Offer an alternative only if it is a real option. Do not make him choose between options he cannot judge.
- **Honest labels.** Say what is done, what is not, what is only an estimate, and what you did not verify. He reacts well to "not verified" written plainly. He reacts badly to finding out later.
- **Cost questions** get a small table in Rs and $, estimates marked.
- **Batch decisions.** He is busy; collect what needs him into one short list. When a decision is his, ask one short question; do not guess.
- **Give a verdict.** He does not want "only your ears can judge" as a dodge. Give your view (use a vision or audio model to look or listen when you cannot), then let him decide.
- **Discuss, then start.** For anything big: say how you would do it, he may say "change X", then start after his OK.

### 2.5 What pleases him and what angers him

He reacted well to:
- The CTO taking technical decisions without asking him about each one.
- Short plain warm replies.
- One recommendation instead of a menu.
- Honest "not verified" labels and plain reports of failures.
- Seeing something he can judge: a clip, a screenshot, a short report.

He reacted badly to:
- **Being told a wrong fact twice** (the ElevenLabs "free plan" error, repeated across sessions).
- **Long waits with no output.**
- **The AI approving stages with scripts** when he expected the studio to do it. His words: **"It should be done by studio right?"** He wants the studio and its dashboard to run the pipeline by itself (holds, approvals, modes, budgets), not an AI clicking approvals from outside.
- **Unwatchable output.** He called the ant video the "worst video".
- Spending he did not expect.
- Dodging a verdict.

### 2.6 His taste, in his own words

He is blunt and judges by eye and ear:
- First demo (16 Nov, panned stills with a voice): "This is just an image + voice; where is the video?"
- Wan fast-mode clips (16-17 Nov): "Needs better quality". Then: "keep note: quality is priority".
- Firefly Short voice (18 Nov): "not good", then "still not matching"; liked Gemini Aoede "kinda"; then "dont make more leave it .. its too much".
- Music A/B (19 Nov): "Lyria seems better for this vibe".
- First-last vs last-frame chaining (20 Nov): "B looks better" (last-frame chaining).
- Picture models (21 Nov): "flux 2 is good".
- SVI motion (21 Nov): "good, better than average; editing, sync, motion, continuity can be better, but for now good".
- fp8 Wan (20 Nov): difference "not noticeable", "it's fine".
- Edit polish A/B (22 Nov): the original edit was better on all three; he saw "disturbances when the clips merge".
- Scripts (23 Nov): "script open ai best".
- Cathedral Blender set (23 Nov): "dumbass video".
- 20 s game cinematic at 16 fps (23 Nov): "worst, not even looking real".
- Ant video (25 Nov): "worst video"; "where are ants?" (the brief had banned showing an ant; he wants an ant visible next time); camera "left straight left" (jerky direction changes); viewers would not watch "just grass". He rejected the after-fixes: a synthesised sound bed, a colour grade, a ripple/shake effect, and a gamma "lightening" that looked washed out.

What he likes, summed up:
- **Real motion** you can see: something happens in each shot. Not a camera gliding over a still scene.
- **Smooth, steady camera** that feels like a person (standing, slow walk, car passenger), never a drone or dolly glide, never jerky direction changes.
- **Real, not effects.** Effects that announce themselves (haze, heavy grain, vignette, ripple overlays, shake) are out.
- **Perfect sound**: clean narration, real recorded ambience (CC0 recordings, not synthesised tones), music that fits, no silent gaps.
- Sharp photo-real looks with vivid golden-hour, blue-hour and neon light (his Dream Shorts examples), slow push-ins, hard cuts.
- Premium, not hobby. His positioning (16 Nov): "a serious, premium, privately owned studio: not a hobby prototype and not a product for sale."

### 2.7 What he wants now (26 Nov 2026)

1. **Perfect sound.**
2. **Ripple effects that read**: when the story is about something visible (a ripple, a drop, a footstep), it must show clearly on screen.
3. **Better motion.**
4. **A full reconstruction of the studio for general videos**, not a fix for one video. The ant video is dropped.
5. **Both lanes kept** (the video lane and the Dream Shorts lane) but independent: "dont remove: make it unaffected to each other". He said **"no"** to a deeper shared-services split for now.
6. **The studio runs the pipeline by itself**; he approves through the dashboard, not through an AI's scripts.

### 2.8 Practical habits

- He runs the pod reaper loop himself in his own PowerShell window (`E:\project-data\logs\reaper_loop.ps1`, now `-Apply -MaxHours 2.5`). It is not a scheduled task; it stops when the window closes. Once two loops ran at the same time and the old one kept the old limit.
- He uses Claude Code with a weekly limit and hit it often. When usage neared 95% he wanted a handover document for a different account (memory: owner-working-rules). Now he is moving to other agents.
- Model policy for Claude sessions (memory: model-policy): only Sonnet and Opus; coding on Opus at low effort; never above medium. Not directly relevant to you, but it shows how cost-aware he is about his own tools.
- He set up auto-mode in Claude Code; auto-mode blocked attempts to dodge the watchdog or launch pods above his approved figure. Those blocks were correct.
- He likes the CTO to keep working through free items while he sleeps (22 Nov "take the lead and complete what u want"), but spend, accounts, keys and push stay his (memory: night-mandate-22nov).

---

## 3. The product and the channels

### 3.1 The product in one paragraph

A personal AI video studio: a text prompt (or a finished plan file) becomes a complete, fully moving video: research, script, narration, music, sound, keyframe pictures, moving clips, edit, captions and a publishing kit, plus Shorts. Target quality is premium; the original goal was a **4K master**. Every stage runs in **Auto** (default), **Review** (waits a window, default 10 minutes, then continues) or **Manual** (waits for him), switchable per video and mid-run; a fourth mode, **Producer**, has an AI producer advise. A failed stage never kills a video: it waits for the owner (Retry or Continue without it). MelodySheep-style cinematic documentary was the first reference, but since Revision R1 (16 Nov 2026) it is one style preset among several. Source: `docs/HANDOVER.md` Revision R1, `CLAUDE.md`.

### 3.2 What makes it different (the owner's original intent)

From `docs/HANDOVER.md` section 1:
- Credit-aware routing across free tiers, rented GPUs, paid APIs, local models and the human.
- A "manual provider": the human generates in a consumer app and a drop folder ingests it. (He later said: **ignore Google Flow**, 18 Nov; the Flow route is out.)
- An automatic critic that rejects bad clips.
- Learning the owner's taste (choices, edits, YouTube retention).
- An editable Premiere timeline (OpenTimelineIO to FCP7 XML).
- Durable, resumable workflows with approval gates (Temporal).
- A full publishing kit (titles, thumbnails, AI disclosure, Shorts).

The landscape research (24 Nov, `reports/AI video studio landscape.md`) found that nobody sells or publishes a complete prompt-to-4K long-form film with narration, music and publishing kit; open "idea to film" projects (ViMax is the most mature) glue paid APIs together with no durable job engine and no cost tracking. So the orchestration layer is the real differentiator, and the stack choices survived the review.

### 3.3 Not a product for sale (yet)

Positioning (16 Nov): privately owned, not for sale, built to production-grade standards. In the four-month plan (25 Nov) month 4 offers a choice he will make at the end of February 2027: Path A personal (a second channel, more output) or Path B services (client work). A draft `docs/CLIENT-OVERVIEW.md` exists for Path B; it is **not for sharing** and has many "[OWNER TO FILL]" gaps (price, turnaround, rights).

### 3.4 Video types

Revision R1 v1 types: cinematic documentary, explainer, ambient/visual film (little or no narration), Shorts/Reels. Out of v1: character dialogue with lip-sync, live action, avatars and talking heads. Style presets live in `config/style-presets.yaml`: `cinematic_awe`, `explainer_clean`, `documentary_story`, `ambient_visual`, `shorts_punchy`.

### 3.5 Channels: how the decision evolved

| Date | Decision | Source |
|---|---|---|
| 16 Nov | One channel; science, history, technology "big idea" documentaries, English first, dubs later | HANDOVER R1, section 3.4 |
| 18 Nov | **Two channels**: Science ("wonders of science": Wonders, Scale, How It Works, Tiny Worlds, Odyssey) and Dreamscapes (dream, nostalgia, liminal: Next Station, Liminal Places, Nostalgia, Dream Journal). Pace 2-3 videos a week per channel. Every video must be a new idea (YouTube's inauthentic-content policy). English first. | memory: channel-decisions |
| 19 Nov | Dreamscapes Shorts channel created as **"Liminal Drift"** (handle still auto-made). Content is not only liminal places: impossible worlds and many imaginary scenes. | memory: short-cost-target |
| 23 Nov | Owner delegated the science channel name; CTO picked "Under Every Surface"; then the owner chose **"Strange If True"** ("final for me"), tagline "Wild ideas. Honest physics.", handle **@strange-if-true** (hyphens; @strangeiftrue was refused by Studio). One handle change used in the 14-day window: do not change it again. | memory: channel-identity |
| 23 Nov | The owner's real topic list (corrects a "take things apart" assumption): what if we made a tiny black hole; how the universe was created; how X/WhatsApp work; de-mystifying concepts; what if ghosts are 4D aliens; entanglement across light years; how radio is made (build-level); Apophis asteroid impact animation; Blender for ancient aliens, Kardashev scale, Dyson sphere; the world if a physics hypothesis were wrong; deja vu; dreams and lucid dreaming. So: curiosity, what-if, demystify science, Blender-heavy for space. Needs a claim-check step and clear labelling of speculation. | memory: channel-identity |

Channel art: banner (tesseract and galaxy, text "STRANGE IF TRUE / Wild ideas. Honest physics.") and profile picture (galaxy eye) were published 23 Nov. Rule: no channel name on thumbnails. Files in `reports/channel-art/` (many untracked image files in git status; do not commit large media).

### 3.6 What is published

- **"How a TV Really Works (One Dot at a Time)"**, 180 s, 2560x1440, public at `https://www.youtube.com/watch?v=vxNfSyIgQfM` (23 Nov 2026). Made by the studio's video path (video id `d3e53cd1-520e-4dd3-8ecb-b6ed0714aa93`): 32 shots, 11 Manim diagrams, 21 Wan AI clips on rented pods, ElevenLabs voice George (re-voiced on the paid plan; lines 5, 8 and 11 refitted; minimum speech gap 0.72 s checked), Lyria music. AI disclosure on, altered content = Yes, not for kids. Custom thumbnail held: YouTube needs the owner's phone verification. Final files in `E:\project-data\outputs\tv-explainer\`. Two older private uploads were deleted.
- Nothing else is public. The Moon Short reached a finished master inside the studio (25 Nov) but was not published.

### 3.7 Cost targets

- Shorts: **under Rs 20 each** once in daily production (18 Nov). During the test and learning phase "money is fine" (19 Nov), but every spend still needs his yes and a figure. Research (25 Nov) says an all-Wan Short at realistic pod times costs about Rs 50 (est), above the target; the target needs re-checking against measured warm-pod times.
- Long-form: the original economics (HANDOVER Appendix A) estimated Rs 1,000 to 3,500 for a 12-minute 4K film on rented 4090s. Unproven.
- Measured so far: TV film Rs 318.52 for 180 s (Rs 1.77 per finished second); Moon Short Rs 46.10 for 14 s (Rs 3.34 per second, mostly the fixed pod boot); ant video about Rs 155 of Rs 180 (est) for an unwatchable result.
- Four-month budget frame (suggested, 25 Nov): GPU about $50 a month (Rs 4,700), month 1 about $25. Not approved as a standing budget: still per-spend approval.

### 3.8 YouTube facts that shape the product

From the landscape report (24 Nov, official pages read):
- From **1 Feb 2027**, new Partner Programme applicants need 1,000 subscribers plus **8,000 watch hours** in 365 days or **20 million Shorts views** in 90 days (up from 4,000 / 10 million). This favours long-form for a new channel.
- Inauthentic content (help page 1311392): image slideshows, templated storylines, same character same outcome are not monetisable; judged channel-wide. Near-static glides over similar frames sit close to the slideshow line, so the motion problem is also a monetisation risk.
- Realistic AI content must carry the altered-content disclosure; disclosure does not limit reach.
- Videos uploaded through an unaudited Google Cloud API project are locked Private until made public by hand (docs/autopilot.md).
- India-audience Shorts RPM is reported at about Rs 2 to 20 per 1,000 views (third-party, unverified).

---

## 4. History and timeline

All dates are 2026 unless stated. Sources: git log (457 commits from 16 Nov), the memory notes, `docs/handoffs/*`, `docs/handover-25nov-2026.md`.

### 4.1 Before the code (research phase, up to 16 Nov)

A long research conversation with Claude produced `docs/HANDOVER.md` ("the book", Revision R1 on 16 Nov). A quick demo was built during research: 7 scenes on "Rome's Self-Healing Concrete" with Kokoro TTS, PIL-drawn pictures panned with FFmpeg, synthesised music. The owner's verdict: **"This is just an image + voice; where is the video?"** That is why panned stills are banned forever (HANDOVER Appendix D).

Revision R1 decisions (16 Nov): local repo at `C:\dev\project-v`; v1 video types chosen by Claude; build the product first and discuss budget later (but every real spend needs his approval); English first; **M1 backbone before M0 GPU benchmarks**; the build tool was Gradle at the time (now uv); he runs installs; he would buy a 1 TB SSD (now the E: drive).

### 4.2 Day by day

**16 Nov (Sat): M1 and M2a in one day.**
- Initial commit; M1 backbone: Java 21 + Spring Boot 4.1 control plane, Temporal dev server, Postgres on 5433, SeaweedFS (MinIO images were gone), a dashboard with Auto/Manual stages, Gemini planner (ADR 0001). `gemini-3.6-flash` chosen as the default text model because newer Flash models hung.
- M2a: story pipeline (research, concept, script, voice with a Whisper check), Redo and Review mode, per-shot redo, script editing, per-line voice redo, the Inspector (independent second-level checks after each stage), free text-model fallbacks (NVIDIA, Groq, OpenRouter).
- A Wan 2.2 benchmark app on Modal (fast, hybrid, standard).

**17 Nov: M2b and M2c (real moving video, then finishing).**
- M2b: real moving video per shot with Wan 2.2 on Modal, drafts then finals, cost-gated; drafts in one GPU container; free NASA footage per shot; hero shots get two takes.
- Creative Director: story review of the shot plan and the pictures. No generated lettering in pictures.
- M2c: assembly (edit, -14 LUFS mix, word-timed captions, Premiere package), RIFE to a true 24 fps, SeedVR2 7B sharpening to 1080p on an H100.
- ElevenLabs narration (credit-careful), Gemini TTS fallback; Claude for research and concept after a blind A/B on the deep-ocean demo; Claude Sonnet 5.5 capped at the owner's $5 a month.
- **"Midnight Descent"** deep-ocean demo Short: 60 s, 1080x1920, **Rs 312.63**. The owner **approved M2c on 17 Nov** (memory: milestone-status).
- Owner: "keep note: quality is priority" (memory: quality-first-cost-cuts).

**18 Nov: router, Producer, Dream Shorts plan, channel strategy.**
- M3a router core (one `Router.choose` for paid jobs: capability, budget caps, cooldowns, quality floor), router dashboard.
- Gemini Omni Flash as a clip provider; hybrid clips (Wan drafts, Omni 720p finals); Omni finals only for hero shots by default.
- Gemini monthly cap raised to Rs 1,000; Gemini cost estimates calibrated x1.5 to Google's real bill.
- Freesound sound effects under narration and music; Wikimedia Commons real footage.
- Chatterbox made the main narration voice (later retired); voice A/B on the firefly Short; the owner said stop voice tests (memory: voice-quality-open).
- AI Producer mode P1 (advise-only) and P2 (decides when trusted).
- Owner pasted the "Architecture Authority" policy (memory: architecture-authority).
- Two channels decided; under Rs 20 per Short; ignore Google Flow; laptop-first Blender rule (memory: channel-decisions, short-cost-target).
- Dream Shorts D1 built on branch `dream-shorts` (own tables, workflow, screen); Lyria chosen for Dream music (about Rs 10); his own 60-minute track becomes the free default music bed. He said "dont worry about copyright" about his own track.
- `reports/Project V studio review.md` ("Ship the first video, then harden") written.

**19 Nov: Dream Shorts lessons, autopilot, Modal credit gone.**
- Dream Shorts looks ("Lonely object", "Walk-through"), camera like a person holding a phone, real and feel-good, music picked by ear, cuts on the beat. Learning loop and originality Scout. Daily autopilot (one Dream Short a day from an idea list, AI Producer deciding inside Rs 100 per Short and a daily cap of Rs 50).
- Dream Shorts lessons recorded after the first tests (memory: dream-shorts-lessons): measured prices only, test the 0 case of settings, judge taste by ear and eye, human camera, no "phone/person/hand" words in Wan prompts, one hero description and one seed per Short.
- ACE-Step vs Lyria music A/B: the owner preferred Lyria by ear (memory: acestep-music-ab).
- AICredits.in text fallback enabled with a Rs 150 cap (memory: aicredits-fallback).
- Honest ledger: Wan start-ups booked from observed cold starts; FX raised to Rs 94.
- Daily backups (pg_dump of every studio database, `.env` and `config/` to `C:\dev\backups`, 14 days).
- **Modal's $30 monthly credit used up**; Modal cap set to 0 until 1 Dec. The autopilot is paused.
- GPU pod watchdog (in-pod self-terminate wrapper and control-plane sweep), dry-run at first.
- `reports/Project V optimisation review.md` ("Stop paying for cold starts and redos").
- Owner: "as we are in test / learning phase: money is fine" (still needs his yes per spend).

**20 Nov: M3 GPU broker.**
- Owner said **"start m3"**. GPU broker merged and pushed (provider-neutral interface, wallet in the cost ledger, RunPod adapter, cheapest live price wins, Auto/Manual pick per Short, pod self-stop, watchdog). One shared pod for all Dream Shorts ("1 pod only to run as many Shorts as I want").
- **fp8 Wan 2.2 on a Secure RTX 4090** benchmark (about 76 s per clip, 98% VRAM, about Rs 16): owner could not reliably tell it from Modal H100 bf16; **fp8 accepted** (memory: gpu-plan-runpod).
- First-last frame chaining vs last-frame chaining A/B: **last-frame chaining wins** (memory: first-last-ab-result).
- Growth idea chosen: **viewers steer the dream** (comments pick the next Short's path) (memory: audience-steers-dream). "Choose the path" built, off by default.
- Salad dropped ("waste"). Handover written for a different Claude account.

**21 Nov: pictures and SVI; CTO mode.**
- Picture A/B (11 prompts x Z-Image / FLUX.2 klein 4B / Nano Banana 2.1): **"flux 2 is good"**: FLUX.2 klein 4B becomes the default picture model (memory: picture-model-test-plan).
- SVI 2.0 Pro (sailing Short): "good, better than average... for now good"; built into the studio path with resume and replace.
- Owner approved: watchdog **live** (no longer dry-run), **$3.50 per UTC day** GPU cap, a small paid SVI resume test.
- **CTO mode** begins: owner = CEO, AI = CTO (memory: cto-mode).
- Model policy for Claude sessions changed to Opus low (memory: model-policy).

**22 Nov: lanes, warm start, TV video.**
- Vast.ai provider (hands-free 2FA with a TOTP seed), E2E Networks adapter (later parked), Jarvislabs adapter (never run). Provider preflight, proven flag and owner-approved canary; unproven providers are out of Auto.
- Warm-start pod image `ghcr.io/nobpiyush/projectv-pod:comfy-v0.45.0` (ComfyUI plus nodes, no weights): pod start 8 min on RunPod, 18 on Vast (was 29) (memory: warm-start-result).
- Edit/music-sync polish A/B: original edit better on all three; all polish flags stay off (memory: edit-sync-verdict).
- Video path lanes: diagram lane (Manim), AI clips and FLUX pictures on the broker pod, 2K master, stock-footage lane (docs/video-lanes.md, docs/stock-footage-lane.md).
- Dashboard Phase 1 (Create / Studio / Settings shell); refactors.
- E2E parked by the owner (memory: e2e-parked).
- "How a television works" run started (cap $3, then $4, then $4.20 for both attempts).
- Night: owner asleep, "take the lead and complete what u want" (memory: night-mandate-22nov).
- The E: drive (WD_BLACK SN7100, 931 GB) installed.

**23 Nov: TV video published, channel identity, Python direction, quality gate.**
- Project data moved to `E:\project-data` with junctions (Docker disk, uv cache, Hugging Face cache) (memory: project-data-on-e).
- Channel named **Strange If True**, @strange-if-true; banner and avatar live; **TV video published public** (memory: channel-identity).
- **ElevenLabs paid Starter plan bought** (601 of 40,000 characters used, resets 23 Dec).
- Owner's direction (memory: architecture-direction-23nov): Python only; serverless GPU with models baked in; plan-then-make pipeline with input fingerprints; small on purpose. Java-to-Python migration S0-S4 done (253 tests) then S5-S7b (577 tests) the same night.
- Bake-off Wan 2.2 vs LTX 2.5 on an L40S: LTX about a third faster and adds audio; mixed prompt-following. **CTO decision (owner said "you tell"): Wan stays the main shot model**, LTX an optional lane.
- Why pods vanished after 10-13 minutes: **the studio's own watchdog killed unleased `projectv-*` test pods**. Fix: `tools/gpu_lease/lease.py` (memory: gpu-pods-need-watchdog-lease).
- Script A/B: **OpenAI gpt-5.4-mini beat Claude Sonnet 5.5** ("script open ai best") (memory: script-writer-openai). "make claude the director".
- OpenAI key for the quality gate; `E:\Downloads` rule; Wan vs LTX "you tell" (memory: quality-gate-openai-and-e-drive).
- Quality gate checks 1-7 merged; owner said "approved all".
- Evening: cathedral Blender set ("dumbass video", about $1); 20 s FLUX + Wan 16 fps game cinematic ("worst, not even looking real", $0.47) (memory: video-look-lessons-23nov).
- `reports/CTO-report-2026-11-23.md`.

**24 Nov: Java removed, film pipeline, pod tests, landscape research.**
- **Java control plane removed**; Python is the live control plane (tag `java-final`). CLAUDE.md updated (docs/java-removal.md).
- 3-minute 4K game film: Act 1 (8 scenes) made on 3 pods for about $3.15 of a $4 cap; Act 2 not started (needs his yes and about $6) (memory: film-pipeline-24nov, docs/film4k.md).
- Pod tests ($0.60 of a $1 cap): **Wan 2.2 Fun Camera makes a real camera move** (best result); Fun VACE follows a drawn camera path; SeedVR2 only a little crisper than Real-ESRGAN (memory: pod-tests-24nov).
- Camera presets, ComfyUI graphs, shot repair tools; tool vetting lab; CI; code metrics.
- `reports/AI video studio landscape.md`: Chatterbox retired, MiniMax H3 licence excludes US/EU/UK/Korea, YouTube bar doubles 1 Feb 2027, Veo left the Gemini API, Sora API shut down.
- Google billing returned 403 "outstanding balance" (owner must pay); Lyria blocked that day.
- Owner gave a mandate: code changes and refactors on branches without waiting for a yes, never on main.

**25 Nov: first live studio runs, hold-before-run, the ant video.**
- Motion block on one Secure 4090 (99 min, about $1.48 of a $4.5 cap): camera presets judged (good: pan_right, slow_push, drift_right; bad: pan_down, fast_push); **chain at most 2 beats** before colour drift; Wan 2.2 Animate usable only with a GPU pose step; RIFE and SeedVR2 both good (docs/film4k.md).
- First live Moon Short through the Python studio ("What if the Moon were 10 times closer?", video `c2200780-...`): reached a master, **Rs 46.10** ledger total, two save crashes fixed by hand.
- Big hardening day (many merges): studio entrypoints load `.env` (before this every key and flag was blank), RunPod `secure_only`, Moon layer stage, local RIFE INTERPOLATE stage, pinned model files (sha256) and pod images, golden tests, per-video cap (Rs 150 default), provider caps, retry/circuit breaker, paid automatic redos wait for the owner, resume a failed save, asset cache keyed by content, narration length check, load-plan dashboard, **hold before run** (paid stages wait for Approve/Skip before they spend), CI with gitleaks.
- **The ant video** ("What an ant hears at night", 90 s, 18 clips): approved at Rs 180 ("yes redo - 90 secs : and track cost from start : 180 rs approved"), L40S fallback allowed up to Rs 193. Result: **unwatchable**. Post-mortem in `docs/ant-run-notes-25nov.md` and chapter 11.
- Research: `reports/Best models per stage and motion.md`.
- Docs: `docs/pipeline-end-to-end.md`, `docs/DEVELOPER-TAKEOVER.md`, `docs/CLIENT-OVERVIEW.md` (draft), `docs/plans/four-month-plan.md`, `docs/handover-25nov-2026.md`.
- Code: archive route and button (migration V48), RunPod refusal reasons in errors, CRITIC no longer holds for money, camera-per-scene warning, **lane isolation** (`studio/tests/test_lane_isolation.py`).
- ElevenLabs correction: "how many times i need to say - its premium".
- Owner's message to CTO: "you are cto: intelligent technical decisions are urs to make: i have other works to do". And: "It should be done by studio right?"

**26 Nov: handover to other agents.**
- Tag **`pre-reconstruction`** pushed (the starting point of the rebuild).
- Handover merged and pushed (`6e743d4`). `GEMINI.md` committed locally (`d148e98`), **not pushed**.
- Uncommitted work in the main checkout at the time of writing: `studio/agents/types.py` (a new `VisibleEvent` model: subject, action, startState, endState; a `visibleEvent` field on `PlannedShot`), `studio/agents/director.py`, `studio/production/shot_stage.py`, `studio/tests/test_agents_s3b.py`. This looks like the start of reconstruction phase 1 (visible event per shot). Author and test status not recorded: check with `git diff` and run the tests before building on it.
- This book and `AGENTS.md` written.

### 4.3 Milestones (HANDOVER section 23) and where they stand

| Milestone | Original definition | Status (26 Nov) |
|---|---|---|
| M0 | GPU benchmarks on rented 4090s, upscaler test, Premiere import test | Done piecemeal (fp8 4090 benchmark 20 Nov, pod tests 24 Nov, motion block 25 Nov). Premiere import never confirmed by the owner (unverified). |
| M1 | Backbone: workflow skeleton, stages, ledger, provider registry, dashboard; restart resumes a workflow | Done 16 Nov (Java), ported to Python 23-24 Nov. |
| M2 | First film path: agents, keyframes, animatic, generation, critic, take selection, upscale, TTS, captions, OTIO, 4K master | M2a/M2b/M2c done 16-17 Nov; **M2c approved 17 Nov**. CRITIC and TAKE_SELECTION are still placeholders; 4K master not reached in the studio. |
| M3 | Router and multi-provider, pod session manager with auto-shutdown, router dashboard | Started 20 Nov ("start m3"); broker, watchdog, RunPod and Vast proven; E2E parked; Jarvislabs never run. Not formally demoed and approved as a milestone (unverified). |
| M4 | Quality layer: VLM critic calibrated (70% agreement), asset library reuse, provenance export | Partly: quality gate tiers 1-2 live after ASSEMBLY; VLM judge off (unreliable); asset cache by content (25 Nov). |
| M5 | Publishing and learning | Partly: YouTube uploader for the Dream/autopilot path; PUBLISH stage in the video workflow is a placeholder; analytics loop not built. |
| M6+ | Style LoRA, series and lip-sync, book-to-series, anime | Not started. |

Rule 1 of CLAUDE.md still holds: work milestone by milestone, demo each and get his approval. In practice the current "milestones" are the four reconstruction phases (chapter 12).

---

## 5. Architecture and code map

### 5.1 The stack as built (26 Nov 2026)

| Layer | Technology | Notes |
|---|---|---|
| Control plane | Python 3.11, FastAPI (`studio/`), port **8080** on 127.0.0.1 | Replaced Java/Spring Boot on 24 Nov (tag `java-final`). |
| Workflow engine | Temporal Python SDK; **Temporal dev server** on 7233 (UI 8233), SQLite persistence | Control queue `projectv-control-py`; media queue `projectv-media`. |
| Database | Postgres 16 + pgvector, host port **5433** | 5432 is the owner's own Postgres: never touch it. Server timezone UTC. |
| Migrations | `db/migration/V1..V48__*.sql`, Flyway-compatible, applied at start by `studio/migrate.py` | Uses the same `flyway_schema_history` table Java used. |
| Object store | **SeaweedFS** S3 on 8333 | Instead of MinIO (images gone). Bucket `projectv`. |
| Media worker | Python (`workers/`), `uv run python -m workers.main` | Needs FFmpeg on PATH. Voice, pictures, clips, assembly, quality gate. |
| GPU | Rented **RunPod** pods (main), Vast.ai (proven), ComfyUI inside the pod | Modal capped at 0 until 1 Dec 2026. |
| Dashboard | React + Vite + TypeScript (`dashboard/`), 127.0.0.1:5173 | Classic view default; new Create/Studio/Settings shell exists. |
| Edit hand-off | OpenTimelineIO to FCP7 XML (Premiere or DaVinci Resolve) | |
| Tooling | uv, ruff, pytest, npm, GitHub Actions CI on PRs and pushes to main, gitleaks secret scan | Last full test run: 1,011 passed, 1 skipped (DEVELOPER-TAKEOVER); full run on the handover commit 6e743d4 (25-26 Nov): 1,292 passed, 2 skipped, lint clean. Run it yourself. |
| Services | `infra/docker-compose.yml` (Postgres, Temporal, SeaweedFS) | Docker data on E: via a junction. |

Departures from the original book (ADR 0001): plain Temporal SDK instead of a Spring starter; Temporal dev server instead of a full cluster; SeaweedFS instead of MinIO; Langfuse deferred (never added); polling instead of WebSocket in the dashboard; UTC server time; Postgres on 5433. Embabel (JVM agent framework) was never used and is gone with Java.

### 5.2 How a video runs (the big picture)

```
Owner (dashboard :5173 or API :8080)
   |  POST /api/videos  (prompt, or planFile=docs/shorts/<plan>.json)
   v
studio/ (FastAPI)  -- starts -->  VideoProductionWorkflow (Temporal, deterministic)
   |                                   |  for each stage the plan includes:
   |                                   |   hold_check (hold before run?) -> run stage (activities)
   |                                   |   -> Inspector / Creative Director -> mode decides (Auto/Review/Manual/Producer)
   |                                   v
   |                           control.* activities (DB, LLM calls)   media activities on queue projectv-media
   |                                                                   (workers/: voice, ffmpeg, gate, pod client)
   v                                                                          |
Postgres (state, ledger, leases)   SeaweedFS (media)         GPU broker -> RunPod pod (ComfyUI: FLUX.2 klein, Wan 2.2)
```

Key design rules (CLAUDE.md rules 2a, 2b, 5, 6):
- Every stage supports Auto, Review and Manual (plus Producer). Never build a stage that can only run one way.
- A failed stage never kills a video; it waits (Retry / Continue without it / Resume).
- Workflow code is deterministic; all I/O in activities; pass asset ids, never media bytes.
- Every generated asset gets a Provenance row; every paid job a CostEntry; every routing choice a RoutingDecision.
- Finished stages can be redone with `REDO_WITH` dependencies (`studio/video/service.py`) so outputs never go stale.

### 5.3 `studio/` (the control plane), package by package

Line counts are approximate (26 Nov).

| Package | What it does | Key files |
|---|---|---|
| `__main__.py`, `worker.py` | Start the API (`python -m studio`) and the Temporal worker (`python -m studio.worker`). Both load `.env` (fixed 25 Nov; before that every key was blank). | |
| `config.py` | `Settings` dataclass: all env variables and defaults (models, GPU limits, feature switches). Chapter 8 lists the model settings. | |
| `migrate.py` | Applies `db/migration` at start (Flyway-compatible). | |
| `singleton.py` | Postgres advisory locks so only one watchdog and one job runner run even if two app copies start. | |
| `api/` | FastAPI app and routes. `app.py` (lane isolation: if one lane fails to start the other still runs), `video_routes.py` (videos, shots, producer, budget, archive), `dream_routes.py`, `plan_preview.py` (dry-run validate of a plan file; `HOLDABLE` set), `video_wiring.py` and `wiring.py` (reads env, builds services), `jobs.py` (periodic jobs). | |
| `workflow/` | Temporal workflows and activities. `stage.py` (the 23 stages and the 4 modes), `video_production.py` (792 lines: the main video workflow and five redo workflows; retry policies per activity kind; `HOLDABLE`, `INSPECTED`, `PER_SHOT` sets), `dream_workflow.py`, `autopilot_workflow.py`, `activities.py`, `queues.py`. | |
| `planner/` | `planner.py` (prompt to VideoPlan), `imported.py` (start from a plan file; `camera_warnings`), `narration.py` (spoken-pace check: 2.5 words/s, warn +10%, reject +25%, from `config/narration.yaml`), `bank.py` (character and location bank, per-beat continuity, claims), `presets.py`. | |
| `agents/` | LLM agents: `director.py` (shot plan), `writer.py` (script), `researcher.py`, `creative_director.py`, `llm.py` (model chain, Claude effort overrides), `chat.py`, `model_fallback.py`, `aicredits.py`, `types.py` (pydantic models: `PlannedShot`, `Script`, ...). | |
| `production/` | Stage implementations: `stages_m2a.py` (research, story, music, voice, animatic), `shot_stage.py` (shot plan), `video_stage.py` (clips: estimates, routing, broker route, saving), `video_gpu.py`, `finishing_stages.py` (upscale, assembly), `moon_stage.py`, `interpolate_stage.py`, `inspector.py`, `creative_director.py`, `producer.py`, `redo_policy.py`, `redo_stop_rule.py`, `camera.py`, `timing.py`, `diagram_specs.py`. | |
| `gpu/` | GPU safety and renting. `broker/broker.py` (804 lines: `GpuBroker.acquire()` is the only way to rent; cheapest live price; wallet; canary; one shared pod), adapters `runpod.py`, `vast.py`, `e2e.py`, `jarvislabs.py`, `community.py` (Community abort-and-retry rule), `wallet.py`; `watchdog.py`, `leases.py`, `self_stop.py` (in-pod guard), `pods.py`, `logs.py`, `events.py`. | |
| `ledger/` | `budget_guard.py` (caps checked before every paid call), `cost_ledger.py`, `cost_view.py`, `pricing.py` (reads `config/providers.yaml`). | |
| `provider/` | `caps.py` (per-provider daily/per-video caps), `registry.py`, `resilience.py` (retry, backoff, circuit breaker; never retries a non-idempotent paid call). | |
| `routing/` | `router.py`: quality floor per job, then min(quality, enough) minus lambda x cost. | |
| `video/` | `service.py` (VideoService: REDO_WITH, guards, archive), `clip_progress.py`. | |
| `dream/` | The Dream Shorts lane: `service.py` (865 lines), `dream_studio.py`, `gpu.py`, `scout.py`, `looks.py`, `music.py`, `producer.py`, `svi.py`, `timeline.py`, `branches.py` (choose the path), `lessons.py`, `evidence.py`. | |
| `autopilot/` | `service.py`: the daily autopilot for Dream Shorts. | |
| `media/`, `assets/` | Media types (`types.py`, defaults matter: missing defaults crashed two live saves), asset store, content cache (`cache.py`). | |
| `contract/` | Replay harness used during the Java-to-Python port. | |
| `tests/` | 66 test files, including `test_lane_isolation.py`. Tests that write data use a scratch database and skip without local Postgres. | |

### 5.4 `workers/` (the media worker and pod code)

- `activities/`: Temporal activities on the media queue: `voice.py` (provider choice, ElevenLabs/Gemini/Kokoro/Chatterbox, Whisper re-check), `keyframes.py`, `clips.py`, `animatic.py`, `assemble.py` (the edit), `mixing.py` (narration placed at start times, music ducked, two-pass loudnorm to -14 LUFS / -1 dBTP; `MUSIC_VOLUME` 0.12 since the owner heard the music too loud on 23 Nov), `sfx.py` (Freesound CC0/CC-BY only, never NonCommercial; needs `FREESOUND_API_KEY`), `music.py`, `research.py`, `sources.py`, `stock.py`, `footage.py`, `review.py`, `inspect.py`, `producer.py`, `gate_check.py` (quality gate after ASSEMBLY), `moon_layer.py`, `interpolate.py`, `finish.py`.
- `pod/server.py`: the self-contained GPU worker that runs inside a rented pod (shipped through env vars, pinned ComfyUI tag, weights from Hugging Face). Endpoints: `/live`, `/health`, `/picture` (Z-Image or FLUX.2 klein 4B), `/clip` (Wan 2.2, or Fun Camera with a tested preset), `/scene` (SVI). **The pod's models are fixed here: changing a pod model is a code change.**
- `modal_apps/`: Modal apps (Wan, Z-Image, finish = RIFE, upscale = SeedVR2). Capped at 0 until 1 Dec.
- `diagram/`: Manim diagram renderer and templates. `dream/`: Dream finishing and polish (all polish flags off). `youtube/`: OAuth and uploader (used by the autopilot path only). `quality_gate/`: adapter. `gemini_pool.py`, `groq_whisper.py`.

### 5.5 `tools/` (standalone tools, mostly pure functions plus tests)

- `film4k/`: the film pipeline and reconstruction helpers: `camera_presets.py` (named moves, `TESTED` set, `plan_camera`), `camera_graphs.py` (ComfyUI graphs for Fun Camera, VACE, VACE repair), `repair.py` (fix a defect: VACE inpaint or reroll, composite, splice), `edit.py` (shot-level editor, runs the quality gate), `sound.py` (procedural sound bed: rejected by the owner as synthetic), `ant_sound.py` (CC0 bed, event cues, ducking, -14 LUFS: the seed of the real AUDIO stage), `ant_fx.py` (ripple/shake experiment, rejected, kept for reference), `make_ant_plan.py`, `moon_layer.py`, `interpolate.py`, `run_shard.py` (never run on a real pod), `assemble.py`, `camera.py` (post push-in), `comfy.py`, `control_from_render.py`.
- `podkit/`: `rent.py`, `boot.py`, `guard.py` (fail-closed lease guards), `ledger.py` (spend reservation against a cap), `groups.py` (model download groups, pinned sha256 and revisions), `verify.py`, `reaper.py` and `reaper.ps1` (external kill-timer).
- `gpu_lease/lease.py`: watchdog lease for test pods (open, keep, heartbeat, release).
- `qualitygate/`: tiers 1-3 (see chapter 9). `golden/`: golden regression tests. `vetting/`: read-only scanner for third-party tools (licence, install hooks, binaries, secrets, network). `bakeoff/`, `model_ab/`, `blender/`, `metrics/`, `live_status/` (status page on :8099), `ops/` (`start_studio.ps1`, Wi-Fi keep-alive), `chatterbox/`.

### 5.6 `dashboard/`

React (Vite). Classic pages: video list and detail (`VideoList.tsx`, `VideoDetail.tsx`, storyboard, clips card, producer card, script editor), `LoadPlan.tsx` (load a plan file, dry-run validate, paid-stage list), Dream Shorts pages under `pages/dream`, Autopilot, Providers, Routing, GPU panel, budget meters. New shell (Phase 1, 22 Nov): Create / Studio / Settings at `#/create`, `#/studio`, `#/settings`; classic view stays default (`NEW_STUDIO_DEFAULT` in `App.tsx`). The archive button on each video row is new (25 Nov). There is no "clip n of m" progress on the stage card yet.

### 5.7 `db/migration/` (V1 to V48)

| Range | Theme |
|---|---|
| V1-V3 | Core schema (channel, film/video, script, shots, takes, assets, provenance, licence, provider, cost entries, routing decisions, approvals), M2a production, review mode |
| V4-V9 | Shot redo, Inspector, Creative Director review, video clips, real footage, master |
| V10-V15 | Smooth clips (RIFE), sharp clips (SeedVR2), licence rows for ElevenLabs, Kokoro, Commons, Chatterbox |
| V16-V19 | Router, provider owner switch, AI Producer (P1, P2) |
| V20-V30 | Dream Shorts (tables, music, music bed, evidence, Scout, editing), autopilot, Dream check, lessons, producer stop rule, Scout watch cache |
| V31-V33 | GPU pod watchdog, Dream GPU pick, lease short |
| V34-V40 | Dream hook/chain ideas, choose the path, chain bridge, GPU hold, pod user, SVI scenes, picture model |
| V41-V43 | Provider state, GPU boot timing, stock footage |
| V44-V48 | Per-video budget, redo/resume, asset cache, hold before run, **video archive** (`video.archived_at`, 25 Nov) |

The running studio had **not** been restarted after V48 at handover; restart it to apply V48 and the archive route.

### 5.8 `config/`

- `providers.yaml`: provider registry, **provider caps**, pricing (FX 94), per-video cap (Rs 150), hold estimates, Producer limits, router weights, Gemini cap (Rs 1,000/month, calibration x1.5, dated price changes on 1 Jan 2027), AICredits fallback (Rs 150), Anthropic cap ($8/month), ElevenLabs block (**stale: says `plan: free`**, see Appendix A), GPU broker caps (daily $3.50, monthly total $20, RunPod $12, Vast $12, E2E $5, Jarvislabs 0), GPU preferences (24 GB minimum, 48 GB RAM, 80 GB disk), Modal (cap 0, per-second prices).
- `style-presets.yaml`, `narration.yaml` (2.5 words per second English), `quality-gate.yaml` (judge model gpt-5.4-mini, $0.50 per run, $2 per day, booked at Rs 90/$).

### 5.9 The two lanes and their isolation

- **Video lane**: `studio/workflow/video_production.py`, `studio/production/`, `studio/video/`, `studio/planner/`, video routes. Documentary, explainer and Shorts from a prompt or a plan file.
- **Dream Shorts lane**: `studio/dream/`, `studio/autopilot/`, `studio/workflow/dream_workflow.py`, Dream routes, `workers/dream/`. Music-led surreal Shorts for the Dreamscapes channel (Liminal Drift), daily autopilot.
- Owner rule (25 Nov): keep both, independent, "dont remove: make it unaffected to each other". `studio/tests/test_lane_isolation.py` fails if one lane imports the other; if one lane fails to start, the API and worker log it and run the other alone (`studio/api/app.py`, `studio/worker.py`).
- **Still shared** (owner said "no" to splitting further for now): the video lane borrows the LLM, gateway, clip progress and GPU wrapper (`VideoGpu` uses `DreamGpu`) from the Dream wiring. Do not start that split without his yes.

### 5.10 Feature switches (env, all read at start)

`STUDIO_DREAM_ROUTES`, `STUDIO_VIDEO_ROUTES`, `STUDIO_JOBS`, `STUDIO_GPU_WATCHDOG`, `STUDIO_TEMPORAL_WORKER`, `STUDIO_MIGRATE` (all on by default; `=false` disables). `DREAM_GPU_PROVIDER` (`modal` default rents no pods; `broker` uses RunPod/Vast). `VIDEO_PROVIDER_DRAFT/FINAL/KEYFRAMES` (`broker` = the pod). `VIDEO_ONE_POD`, `INTERPOLATE_FPS`, `MOON_LAYER`, `QUALITY_GATE_ENABLED`, `VIDEO_OUTPUT_HEIGHT`, `VIDEO_DIAGRAM_LANE`, `VIDEO_CAMERA_PRESETS`, `VIDEO_CAMERA_ALLOW_UNTESTED`, `PROJECTV_VIDEO_AUTO_APPROVE_INR`, `ELEVENLABS_COMMERCIAL`, `POD_CLOUD_PREFERENCE`, `POD_WARM_IMAGE`. Flags the owner set this week (from the handover, values not re-read): `VIDEO_ONE_POD=true`, `INTERPOLATE_FPS=local`, `GEMINI_MODEL=openai:gpt-5.4-mini,gemini-3.6-flash`, `POD_CLOUD_PREFERENCE=secure_only`.

---

## 6. The 23-stage video pipeline in detail

Source of truth: `studio/workflow/stage.py` (the enum, in run order), `studio/workflow/video_production.py`, and `docs/pipeline-end-to-end.md` (25 Nov, read from code, config and the live database). The plan decides which stages a video needs; the others are recorded as Skipped.

### 6.1 How every stage is controlled

1. **Hold before run** (25 Nov). Before a stage in the `HOLDABLE` set spends money, the activity `control.hold_check` decides whether to wait for the owner's Approve or Skip: always when the stage is in Manual mode, and for every paid stage of a video with `approve_paid_first` on (on by default for plan files). The hold shows the estimate and the video's remaining budget. Reject skips the stage and spends nothing. `HOLDABLE` = SHOT_PLAN, MUSIC, NARRATION, KEYFRAMES, DRAFT_GEN, FINAL_GEN, CRITIC, UPSCALE, TAKE_SELECTION, AUDIO, PACKAGING. DRAFT_GEN, FINAL_GEN and UPSCALE always show their own estimate and wait unless `PROJECTV_VIDEO_AUTO_APPROVE_INR` is above it (default 0).
2. **Run.** The stage runs through activities. Paid LLM activities get few retries and never retry a budget refusal; clip batches are never retried by Temporal (a retry would pay again).
3. **Inspect.** The Inspector (free checks with different tools) looks at RESEARCH, SCRIPT, MUSIC, NARRATION, SHOT_PLAN, KEYFRAMES, ANIMATIC, DRAFT_GEN, FINAL_GEN and ASSEMBLY. The Creative Director reviews the shot plan (text) and the keyframes (vision). A FAIL is redone once automatically (pictures and clips: only the failing shots, with better prompts); a paid automatic redo waits for the owner unless the video has `auto_redo_paid`; a second FAIL always waits.
4. **Mode decides.** Auto continues; Review waits `reviewMinutes` (default 10) and continues; Manual waits; Producer asks the AI Producer (advise-only today, `adviseOnly: true`).
5. **Failure** never kills the video: Retry (Reject), Continue without it (Approve), or Resume (re-run only the save when the media work is done; 25 Nov).
6. **Redo rules** (`REDO_WITH`, `studio/video/service.py`): redoing SCRIPT also redoes NARRATION, SHOT_PLAN, KEYFRAMES, ANIMATIC; NARRATION and MUSIC redo ANIMATIC; SHOT_PLAN redoes KEYFRAMES and ANIMATIC; KEYFRAMES redoes ANIMATIC; UPSCALE redoes ASSEMBLY, MOON_LAYER, INTERPOLATE; ASSEMBLY redoes MOON_LAYER and INTERPOLATE. DRAFT_GEN and FINAL_GEN each run alone after an estimate. Which redos cost money: everything except ANIMATIC, ASSEMBLY, LIBRARY_MATCH, MOON_LAYER (`studio/production/redo_policy.py`).
7. **Producer redo stop rule** (`redo_stop_rule.py`, 19 Nov): if a review names the same problem twice (visible people, text, morphing, too little motion), try one firm prompt fix, then ask the owner instead of paying for another GPU round.

### 6.2 Plan files vs prompts

A video can start from a prompt (the paid planner, writer and Director invent the plan) or from a **plan file** (`POST /api/videos` with `planFile`, for example `docs/shorts/moon-10x-closer-plan.json`). With a plan file, PLAN, CONCEPT, SCRIPT and SHOT_PLAN are built from the file for free (`studio/planner/imported.py`), shot timing follows the plan's per-beat `startSec`/`durationSec`, and the Creative Director's text review is skipped. Plan files carry a character and location bank, per-beat continuity and claims (`studio/planner/bank.py`). The dashboard's Load plan page validates a plan without spending (`/api/plans/validate`, which also returns a clip estimate). Plan files in the repo: `moon-10x-closer-plan.json`, `ant-hears-at-night-plan.json`, `milky-seas-plan.json`.

### 6.3 Stage by stage

"Status" says whether the stage does real work today. Costs are from ledger rows in `docs/pipeline-end-to-end.md` unless marked est.

**1. PLAN.** Turns the prompt into a VideoPlan: format, length, aspect ratio, style preset, whether research, narration and music are needed, and which stages run. Model: `story_model` = `claude:claude-sonnet-5-5` (env `CLAUDE_STORY_MODEL`), falling back to the Gemini text chain, then a rule-based plan. Cost about Rs 1.6 per run (ant, TV). Plan file: free. Not holdable. Status: working.

**2. RESEARCH.** Finds 8-20 facts with web sources, then a free check that each number appears on its cited page (`workers/activities/sources.py`). Claude with Anthropic web search (max 5 searches, one attempt only because a retry pays again); fallback Gemini with Google Search grounding. Cost about Rs 8 (two searches). Inspected (no facts = FAIL). Skipped when the plan says no research. Status: working. The research report recommends Sonnet 5.5 with web_search plus web_fetch (read the full page, not snippets).

**3. CONCEPT.** Big idea, acts, optional on-screen "clock" (the MelodySheep device). Claude. About Rs 1.6 to 1.9. Status: working.

**4. SCRIPT.** Narration lines, a voice name, a reading style, a music brief; checks length against the plan. Model `script_model` = `openai:gpt-5.4-mini` (owner's A/B pick, 23 Nov); `SCRIPT_MODEL=claude:claude-sonnet-5-5` switches back. About Rs 0.5 (OpenAI booked at Rs 90/$). Plan file: the lines word for word. Inspected (too many words for the length fails). A plan-time narration check (`studio/planner/narration.py`, 2.5 words per second) warns at +10% and rejects at +25% per beat. Known weakness: the ant script had about 16 words per 10 s scene, leaving silent gaps (chapter 11). Status: working.

**5. MUSIC.** One instrumental score, extended with 4 s crossfades to the video length, then beat-mapped with librosa. Google Lyria `lyria-3.5` (fallback `lyria-3-pro-preview`, env `GEMINI_MUSIC_MODEL`), about Rs 11.28 per song (ledger, flagged estimate: $0.08 list x 1.5 calibration). Holdable. Provider cap `gemini_lyria`: Rs 15 per video, Rs 30 per day. **Bug**: the stage asks the cap about the maximum number of songs (`ceil(seconds/55)+1`), so a 90 s video asks Rs 33.84 and is refused; raise the caps to at least Rs 34 or fix the request. Google billing returned 403 on 24 Nov; it worked on 25 Nov. Commercial terms of Lyria fall under the general Gemini API terms: an owner decision before monetising. Status: working.

**6. NARRATION ("Voice").** Voices each script line as its own WAV, transcribes each with Whisper (Groq `whisper-large-v3`, free) and re-voices bad takes. Provider order in `workers/activities/voice.py`: Chatterbox (laptop, only if installed and `PROJECTV_CHATTERBOX` is not 0; retired), else Gemini TTS unless `ELEVENLABS_COMMERCIAL=1`, in which case **ElevenLabs `eleven_v4_turbo`, voice George** (`JBFqnCBsd6RMkjVDRZzb`) when credits cover it; fallbacks Gemini TTS `gemini-3.8-flash-tts` (voice Charon) then Kokoro (`bm_george`) on the laptop. ElevenLabs allows limited parallel requests: 2 workers plus retry on 429 (a 429 once silently moved a whole video to Gemini). ElevenLabs is on the **paid Starter plan** (prepaid credits, so the ledger shows Rs 0). The script's voice name applies only to Gemini. Holdable (hold estimate Rs 15 from config). Status: working. An "ElevenLabs-only voice lock" (wait instead of falling back to Gemini) was approved on 23 Nov for narrated videos; check whether it was built before relying on it (unverified).

**7. SHOT_PLAN.** Splits the video into shots timed to narration and beat; writes an image prompt and a motion prompt per shot, picks the camera move, marks hero shots (max 20%). Claude Sonnet 5.5 as "Director" (`studio/agents/director.py`, `studio/production/shot_stage.py`), plus a paid "Creative Director" text review. Cost: ant (45 shots) Rs 12.30 plus Rs 7.02 review. Output ceiling raised to 16,000 tokens on 22 Nov after a 26-shot plan was cut off. Plan file: one shot per beat, free. Inspected: dead air, shots under 0.8 s, narration too long for a Short. **This is where the ant video failed**: the plan gave 18 nearly identical pictures and no visible event per shot. Reconstruction phase 1 rebuilds this stage (visible event per shot, variety, camera per scene). Status: working but creatively weak.

**8. KEYFRAMES.** One still picture per shot before any video money. With `VIDEO_PROVIDER_KEYFRAMES=broker` (live on 25 Nov): **FLUX.2 klein 4B on a rented RunPod pod** (about 2.7 s a picture once loaded, first one about 49 s). Default without broker: Gemini Nano Banana 2.1 for hero shots, Z-Image Turbo on Modal for the rest (Modal capped). Then a Gemini vision review (`GEMINI_VISION_MODEL`, `gemini-3.6-flash` chain): meaning, accuracy, continuity, no writing. Moon: Rs 12.42 pods (three rentals) plus Rs 2.60 review for 2 pictures. Est for 18 pictures: Rs 20-25 pod plus about Rs 5 review. **Bug**: the hold text quotes the Gemini image price (about Rs 85 for 18 pictures) instead of the pod price. Reject with notes redoes only the weak shots. A content-keyed asset cache reuses identical checked keyframes for Rs 0. Status: working.

**9. ANIMATIC.** Pictures plus voice plus music cut together (ffmpeg on the laptop, free) so story and pacing can be checked before video spend. 1080p preview. Status: working. (Research: this is the standard step successful productions use; the gap is that nobody judged the ant animatic for "does anything happen".)

**10. LIBRARY_MATCH.** Free real footage (NASA, Wikimedia Commons; stock lane: Pixabay, Internet Archive Prelinger) for shots the Director marked; the owner picks. A miss never fails the shot. On the TV video the owner rejected all three stock picks as wrong content. Status: working, rarely useful so far.

**11. DRAFT_GEN.** One moving clip per shot from its picture. Live route: **Wan 2.2 14B fp8 Fun Camera** (`wan2.2_fun_camera_*_14B_fp8_scaled`, LightX2V 4-step LoRAs) in ComfyUI on the rented pod, 720x1280, 81 frames at 16 fps (about 5 s), one take per shot; every clip of a 9:16 video on the pod route uses a real camera move; beat 2 starts from beat 1's last frame (chains of 2). Measured: about 3.5-3.7 min per beat on a 4090 plus about 6 min setup plus pod boot. Moon: Rs 19.80 for 2 beats (0.3053 h on an A6000). Ant estimate 18 beats: Rs 94-188, 87 min. Always stops at its estimate unless auto-approve is set. Never retried by Temporal. Other routes (not live): Modal H100 Wan, Gemini Omni `gemini-omni-1.2-flash`, SVI scenes, Manim diagrams (flag). Inspected: frozen picture, black frames, flicker, wrong size. Status: working, but motion is weak with the 4-step distill at vertical 720p (chapter 11). Reconstruction phase 3 tests sampler settings.

**12. FINAL_GEN.** Full-quality second pass for approved drafts not marked "keep the draft". Wan hybrid on the pod, or Gemini Omni for hero shots. Camera clips are already final quality, so usually "No shots need final clips". Status: working, rarely used.

**13. CRITIC.** Meant to check every take automatically. **Placeholder** (`control.run_stage` does nothing). Since 25 Nov its hold estimate is Rs 0, so it no longer asks for money it does not spend. Phase 2's clip judge is the planned replacement. Phase 4 says skip placeholders so videos never stall.

**14. TAKE_SELECTION.** Meant to pick the best take per shot. **Placeholder.**

**15. UPSCALE.** Smooths motion to 24 fps (RIFE on an L4) and sharpens to 1080p short side (SeedVR2 7B on an H100), both on **Modal** (`workers/modal_apps/finish.py`, `upscale.py`). Modal cap is 0, so it is refused and skipped (the edit uses clips as they are). Target is 1080p, not 4K. Est for 18 clips: about Rs 3 plus Rs 130. Status: blocked by the Modal cap; research recommends FlashVSR to 4K once on the cut, and the motion block found SeedVR2 3B fp8 on a pod good for hero shots.

**16. AUDIO.** Meant to be the final mix. **Placeholder.** The real mixing happens inside ASSEMBLY (narration, ducked music, optional Freesound effects, two-pass loudnorm). There is no sound-design stage: no ambience bed per scene, no event cues. Reconstruction phase 3 builds it from `tools/film4k/ant_sound.py`. The owner rejected synthesised sound; use real CC0 recordings or ElevenLabs Sound Effects (his paid plan).

**17. ASSEMBLY.** The edit: each shot's clip on the narration timeline (slowed up to 1.6x to fill its slot, flagged), voice plus ducked music plus effects, mastered to -14 LUFS, word-timed captions burned in, SRT, a Premiere package (OTIO to FCP7 XML). Then the quality gate tiers 1-2 (free). Groq Whisper for caption timing. Default master 1080p (`VIDEO_OUTPUT_HEIGHT=0`; 1440 gives 2560x1440). A shot with no moving video stops the stage (never a panned still). Gate FAIL waits (Reject = redo the edit, Approve = continue). Status: working. Research recommends captions by forced alignment to the known script (WhisperX align) instead of transcription.

**18. MOON_LAYER.** Optional: composites a growing Moon picture (scale curve, glow, caption) over the master; only for a plan with a `moon` block (`tools/film4k/moon_layer.py`, free). Ran on the real Moon video. Status: working, niche.

**19. INTERPOLATE.** Optional: RIFE `rife-ncnn-vulkan` `rife-v4.6` on the laptop's RTX 3050 doubles the master 24 to 48 fps, audio copied untouched; about 7.5 s of work per second of video. Off unless `INTERPOLATE_FPS=local` (the owner set it this week). Never ran inside a real workflow before handover (unverified). Status: built.

**20. ROUGH_CUT_REVIEW.** Notes to changes. **Placeholder.**

**21. PACKAGING.** Captions, dubs, titles, thumbnails, Shorts. **Placeholder** (SRT is already made in ASSEMBLY). The Moon video sat waiting here in Manual.

**22. FINAL_APPROVAL.** **Placeholder.**

**23. PUBLISH.** Upload Private to YouTube. **Placeholder in the video workflow.** A YouTube uploader exists (`workers/youtube/`) but only the Dream/autopilot path calls it. The TV video was uploaded by hand through Claude in Chrome.

### 6.4 Cost of a whole video (measured and est)

| Video | Result | Ledger total | Notes |
|---|---|---|---|
| Midnight Descent (17 Nov, Java era) | 60 s Short, M2c demo, approved | Rs 312.63 | Modal H100 heavy |
| TV film `d3e53cd1` (22 Nov) | 180 s, 2560x1440, published | Rs 318.52 ($3.39); both attempts about Rs 352.5 | Vast pod Rs 125, Gemini Rs 84, Claude Rs 67, RunPod Rs 30, OpenAI vision Rs 12.5 |
| Moon Short `c2200780` (25 Nov) | 14 s master, not published | Rs 46.10 | Pods Rs 32.22 (70%), Gemini Rs 13.88 |
| Ant first attempt `539db64a` (25 Nov) | stuck at SHOT_PLAN (45 shots) | Rs 31.06 | text models only; to be archived |
| Ant redo `a013569d` (25 Nov) | 18 clips, stopped at CRITIC, unwatchable | project total about Rs 155 of Rs 180 (est) | clip pod RTX 4090 Secure about 1.3 h at $0.89/h (est Rs 110) |

Estimate for a typical 90 s video (18 beats of 5 s, plan file, no music, pod route): **about Rs 110 to 235** ($1.2 to 2.5, est); from a prompt with one song: about Rs 150 to 300 (est). The default per-video cap (Rs 150) would refuse the high end. Source: `docs/pipeline-end-to-end.md` section 4.2.

**Two "spent" numbers.** The video list column `spent_inr` leaves out GPU pod rentals (the broker writes rentals straight into `cost_entry`); the budget guard, per-video cap and cost view count all rows. Moon: list says Rs 13.88, true total Rs 46.10. This is a known bug.

---

## 7. GPU and cost control

### 7.1 Why this chapter matters

Rented GPUs are the biggest cost and the biggest risk. CLAUDE.md rule 8: GPU pods must auto-shutdown when idle; **a forgotten running pod is a critical bug**. The owner has been burned by surprise spend and by pods that vanished. Every pod you start must be leased, timed and checked.

### 7.2 The safety layers, in order

1. **Broker and lease.** Every studio rental goes through `GpuBroker.acquire()` (`studio/gpu/broker/broker.py`), which picks the cheapest live price in stock inside the wallet caps, writes a RoutingDecision, opens a lease row in `gpu_pod_lease` and heartbeats it. Every rental ends in `release()`, which terminates and books a CostEntry.
2. **Studio watchdog** (`studio/gpu/watchdog.py`, live, not dry-run, every 180 s): terminates any pod named `projectv-*` that has no lease after 10 minutes, a released lease, a heartbeat older than 15 minutes (idle), or has run past 180 minutes. It never touches pods without the prefix. One process at a time (advisory lock).
3. **In-pod self-stop** (`studio/gpu/self_stop.py`): the pod's own wrapper terminates the pod at an absolute deadline, when idle (no heartbeat file and GPU under 5% busy), or when the job ends, even with the laptop off. Terminate, not stop (a stopped pod still bills its disk).
4. **External reaper / kill-timer** (`tools/podkit/reaper.py`, `reaper.ps1`, docs/pod-reaper.md): independent of the studio, talks only to the RunPod REST API. Rules: (a) wall clock over 3 h or the `maxh=` in the pod name; (b) a `projectv-*` pod older than 15 minutes with no fresh lease; (c) would push today's estimated spend past $3.50 within 10 minutes. Dry-run by default; `-Apply` terminates. **The owner runs it by hand in a loop** (`E:\project-data\logs\reaper_loop.ps1 -Apply -MaxHours 2.5`, log `E:\project-data\logs\reaper-loop.log`). It is not a Windows scheduled task (registering one needs his OK; the command is in docs/pod-reaper.md). Its RunPod API field names (`desiredStatus`, `costPerHr`, `lastStartedAt`) were assumed; confirm on a real pod.
5. **Release before any human wait.** In one-pod mode the workflow lets the pod go before a pause, a review window, a manual hold, a failure or a cancel; an idle sweep every minute; per-batch release after each save.
6. **Boot checks.** `POD_CLOUD_PREFERENCE=secure_only` (owner's choice): never quote Community offers. The `community_then_secure` mode aborts a Community pod with no boot log in 9 minutes (never run on a real pod).
7. **Test pods outside the studio** must hold a lease: `tools/gpu_lease/lease.py keep <runpod|vast> <pod_id> <name> "<purpose>" --state <json> --max-minutes N`, plus an independent kill timer (`tools/bakeoff/killer*.py` or `tools/podkit/guard.py`). `lease.py` exits 2 ("NO LEASE") if Docker is down; the guard then makes the caller terminate the pod (fail-closed, after Docker was off on 24 Nov and the lease silently failed).

**Never rename pods to dodge the watchdog, and never change its settings without the owner.** On 23 Nov every test pod "vanished after 10-13 minutes": the cause was the studio's own watchdog killing unleased `projectv-*` pods (11 kills in the log), not RunPod. Earlier reports that said "cause unknown" were wrong.

### 7.3 Caps (current values, `config/providers.yaml` and settings)

| Cap | Value | Notes |
|---|---|---|
| All GPU pods per UTC day | **$3.50** (`gpuBroker.dailyCapUsd`) | Owner's standing cap since 21 Nov; raised to $5 and $10 while testing 21-22 Nov; back to $3.50 on 25 Nov. |
| All rented GPUs per month | $20 total; RunPod $12, Vast $12, E2E $5, Jarvislabs 0, CloudRift 0, Shadeform 0 | Owner, 22 Nov. |
| Modal GPU | **0 until 1 Dec 2026** (`modal.monthlyCapUsd`) | "set back to 30 on 1 Dec" per the config comment, but only with his OK. |
| Per video | Rs 150 default (`perVideo.defaultCapInr`), per-video `budgetInr` override, 0 = no cap | Checked before every paid call and pod rental: spent + reserved + estimate <= cap. |
| Provider caps (per day / per video) | `gemini_lyria` Rs 30 / 15; `gemini_image` Rs 60 / 40; `gemini_image_review` Rs 40 / 20; `gemini_omni` Rs 150 / 100; `gemini_api` text Rs 200 / none; `groq_whisper` free | Added 25 Nov. A refused call makes the stage wait. |
| Gemini, all features | Rs 1,000 a month | About Rs 681 spent, Rs 319 left on 25 Nov. |
| Claude API | $8 a month | About Rs 200 spent on 25 Nov. |
| AICredits text fallback | Rs 150 total | Only if `AICREDITS_FALLBACK_ENABLED=true`. |
| Quality-gate OpenAI judge | $0.50 per run, $2 per day | Judge is off by default. |
| AI Producer | Rs 100 per Short, Rs 500 per long video, asks above Rs 50 | Advise-only. |
| Autopilot | Rs 100 per Short, daily cap Rs 50 | Autopilot paused since the Modal credit ran out. |
| Pod limits (`Settings`) | 1 pod at a time, at most $2.00/h, 180 min max runtime, 15 min idle, 10 min lease grace | |
| Clip spend that does not ask | `PROJECTV_VIDEO_AUTO_APPROVE_INR` = 0 | Clips always ask. |
| **Spend approval** | His own "yes" plus a rupee figure in chat | The caps above are ceilings, not permission. |

### 7.4 Providers and measured prices

Never hard-code market prices (rule 7): the broker reads live hourly prices. Numbers below are what was seen, for planning only.

| Provider | Status | Measured or seen |
|---|---|---|
| **RunPod** | Primary, proven. Secure pods only by owner choice. Account deposit was about $5-10; balance about $6-7 after 24 Nov (unverified now). | Secure RTX 4090 **$0.89/h**; RTX A6000 $0.69/h; Community 4090 $0.34/h (stock often empty, some bad hosts: CUDA false, hung image pull); L40S about $1.09/h; A40 used for Blender. Download from Hugging Face 200-330 MB/s. |
| **Vast.ai** | Proven (canary 22 Nov). Needs a 2FA session key (TOTP seed in `.env`, at most two login attempts). Strangers' machines: tight filters, no secrets on the box, destroy rather than stop. | 4090 about $0.50/h (TV film used one at $0.505/h); downloads 19-65 MB/s (slow). |
| E2E Networks (India) | **Parked** by the owner 22 Nov. Adapter merged, never created a node: needs about 24 h of plan price in balance (about Rs 2,449 for L40S). About Rs 2,305 balance sits there unused. | $1.21-1.28/h. Do not raise again unless he asks. |
| Jarvislabs | Adapter on the Python SDK, never run; no REST API; A100 only. Cap 0. Not recommended. | Rs 84.24/h A100. |
| CloudRift | Key works, about $10 balance, few machines in Asia, terms unread. Cap 0. | 4090 $0.45/h, 5090 $0.65/h. |
| Salad | **Dropped** by the owner ("waste"). Wiped disks. | |
| Modal | Capped at 0 until 1 Dec. Was the first GPU route (Wan on H100, Z-Image on L40S, RIFE on L4, SeedVR2 on H100). $30 free credit a month. | H100 about $3.95/h; Wan container start about 140 s. |

### 7.5 Pod boot and warm start

- A pod boot downloads about 41 GB of weights (Wan fp8 experts, text encoder, VAE, LoRAs, FLUX), installs ComfyUI and nodes. Plain boot was about 29 minutes.
- **Warm image** `ghcr.io/nobpiyush/projectv-pod:comfy-v0.45.0` (public, ComfyUI plus nodes and deps, no weights, no studio code in layers), set via `POD_WARM_IMAGE`: RunPod healthy in about 8 minutes, Vast about 18 (22 Nov).
- Fun Camera adds about 38-40 GB of expert files (boot about 10 minutes). `tools/podkit/groups.py` downloads only the groups a job needs.
- Model files are pinned by sha256 and Hugging Face revision; pod images by digest; ComfyUI by tag v0.45.0; `verify.py` checks every download (25 Nov).
- A RunPod network volume for weights (about $4.2 a month, RunPod only) was tested on 20 Nov and judged not worth it then ("volume test result (volume not worth it)"); the idea comes back if real timings justify it.
- One-pod mode (`VIDEO_ONE_POD`) keeps one pod for pictures and clips; set true this week but not proven live before handover.
- Measured on the ant run: pod setup plus model load about 18 minutes before clip 1; later clips about 3.5 minutes each.

### 7.6 VRAM and sizes

Wan 2.2 14B fp8 needs about 24 GB peak at 720x1280 (each expert about 14 GB, one in VRAM at a time; text encoder 11 GB while encoding). A 4090 or 3090 works; a 48 GB card is not faster; 16 GB needs GGUF/offload (untested); 12 GB no. The pod needs 48 GB of system RAM and about 80 GB disk. Sizes: 4k+1 frames, width and height multiples of 16. The laptop's RTX 3050 (4 GB) cannot run video models; it runs RIFE (rife-ncnn-vulkan at `E:\tools\rife`), FFmpeg, Whisper-class work and Manim.

### 7.7 The direction that did not happen (yet): serverless GPU

On 23 Nov the owner approved a direction: no rented pods, serverless GPU with models baked in (RunPod Serverless or Modal), "plan-then-make" with input fingerprints, small on purpose. In practice the studio still rents pods: the pod route was proven and made safe first, and the 24 Nov landscape research put "RunPod Serverless for Wan" on its **ignore** list (cold starts with 40-80 GB weights unmeasured). Treat serverless as an open direction, not a current fact. Do not switch without a measured test and his OK.

### 7.8 How to run a paid test safely (checklist)

1. Write the estimate (Rs and $, est) and the cap. Get his "yes" with a figure.
2. Restart the control plane and workers if code changed (old builds skipped checks before).
3. Check Docker is up (the lease helper needs it) and no pod is running (`GET /api/gpu/pods`, reaper dry run, or the live status page `http://127.0.0.1:8099`).
4. Rent through the studio broker, or through `tools/podkit` with `lease.py keep` and a kill timer. Put `maxh=` in a test pod's name.
5. Make a short proof first (one beat) and look at it moving before the full batch.
6. Pull results to `E:\project-data\outputs\...`.
7. Release, then confirm: no pods, no open leases, reaper log quiet.
8. Report real spend from the ledger (estimates marked) and write the measured number back into `config/providers.yaml` if it was a new kind of run (memory: dream-shorts-lessons rule 1).

---

## 8. Models and providers per stage

### 8.1 Current settings (code defaults; live `.env` values not re-read)

All of these are set in `studio/config.py`, `studio/api/video_wiring.py` or `workers/activities/voice.py`, overridable in `.env`. Restart the control plane after changing a text or planner setting; restart the media worker after changing a worker setting. Pod models are **not** settings: they are fixed in `workers/pod/server.py`.

| Job | Setting | Default / current |
|---|---|---|
| Director: plan, concept, shot plan | `CLAUDE_STORY_MODEL` (not `STORY_MODEL`) | `claude:claude-sonnet-5-5`. Phase 1 moves planning and shot design to **Claude Opus 5.5** (owner's approval of the plan is pending). |
| Script writer | `SCRIPT_MODEL` | `openai:gpt-5.4-mini` (owner's A/B, 23 Nov). Keep until a new by-ear A/B. |
| Text chain (fallbacks, reviews) | `GEMINI_MODEL`, `GEMINI_TEXT_FALLBACKS` | Code default `gemini-3.6-flash`; live set to `openai:gpt-5.4-mini,gemini-3.6-flash` this week. Fallbacks: `gemini-3.5-flash`, NVIDIA nemotron and step-5 (free), Groq `gpt-oss-120b`, OpenRouter free ERNIE and Gemma, `gemini-3.5-flash-lite`. AICredits retries the same model on outages (text only). |
| Claude thinking effort | `CLAUDE_EFFORT_OVERRIDES` (e.g. `SHOT_PLAN:low`) | medium everywhere |
| OpenAI ledger prices | `OPENAI_PRICE_IN/OUT` | $0.75 / $4.50 per million tokens; booked at Rs 90/$ (the rest of the ledger uses 94) |
| Voice | `ELEVENLABS_MODEL`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_COMMERCIAL=1` | **ElevenLabs `eleven_v4_turbo`, voice George**, reserve 1,500 characters. Fallbacks: Gemini `gemini-3.8-flash-tts` (voice Charon), Kokoro `bm_george`. Chatterbox retired. |
| Speech check, captions | Groq `whisper-large-v3` | free |
| Music | `GEMINI_MUSIC_MODEL` | `lyria-3.5`, fallback `lyria-3-pro-preview` |
| Keyframes | `VIDEO_PROVIDER_KEYFRAMES` | `broker` = **FLUX.2 klein 4B** on the pod (with `qwen_3_4b` encoder and `flux2-vae`). Default route: Gemini `gemini-nano-banana-2.1` (hero) plus Z-Image Turbo (Modal). Never the FLUX klein 9B. |
| Picture review | `GEMINI_VISION_MODEL` | `gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite` |
| Clips | `VIDEO_PROVIDER_DRAFT/FINAL` | `broker` = **Wan 2.2 I2V A14B fp8** + LightX2V 4-step LoRAs, **Fun Camera** experts for camera moves, SVI 2.0 Pro LoRAs for scenes; 720x1280, 81 frames, 16 fps. Alternatives: Modal Wan (capped), Gemini Omni `gemini-omni-1.2-flash` (finals, hero). |
| Camera presets | `VIDEO_CAMERA_PRESETS`, `VIDEO_CAMERA_ALLOW_UNTESTED` | tested: `zoom_in`, `pan_left`, `pan_right`, `slow_push`, `drift_right` (motion block 25 Nov added pan_right, slow_push, drift_right as good; `fast_push` and `pan_down` bad) |
| Upscale | Modal apps | RIFE (L4) and SeedVR2 7B (H100): capped. On pods: Real-ESRGAN x4 (bulk) and SeedVR2 3B fp8 (hero). |
| Interpolation | `INTERPOLATE_FPS=local` | RIFE ncnn `rife-v4.6` on the laptop |
| Quality gate judge | `config/quality-gate.yaml` | `gpt-5.4-mini`, off |
| AI Producer | `ProducerService` | `gemini-3.6-flash` (multimodal), advise-only |
| Diagrams | `VIDEO_DIAGRAM_LANE` | Manim on the laptop (16:9 only) |
| Sound effects | `FREESOUND_API_KEY` | Freesound CC0/CC-BY previews |

### 8.2 What was tested and what the owner chose

| Area | Test | Result | Date |
|---|---|---|---|
| Video model | Wan 2.2 fast mode | "Needs better quality" | 16-17 Nov |
| Video precision | fp8 Wan on 4090 vs bf16 H100, blind | not noticeable: **fp8 accepted** | 20 Nov |
| Chaining | first-last frame vs last-frame | **last-frame** ("B looks better") | 20 Nov |
| Pictures | Z-Image vs FLUX.2 klein 4B vs Nano Banana 2.1, blind, 11 prompts | **FLUX.2 klein 4B** ("flux 2 is good"); Z-Image stays for Dream "lonely" | 21 Nov (19 Nov Dream test had picked Z-Image over Nano Banana) |
| Long shots | SVI 2.0 Pro | "good, better than average... for now good" | 21 Nov |
| Music | ACE-Step 1.5 vs Lyria | **Lyria** by ear ("seems better for this vibe") | 19 Nov |
| Voice | Chatterbox default, Gemini Puck, Gemini Aoede | not good; "kinda" Aoede; then stop voice tests | 18 Nov |
| Voice | ElevenLabs George `eleven_v4_turbo` | approved for the TV video ("good") | 22 Nov |
| Edit | polish flags (beat snap, smart transitions, HQ interpolation, two-pass loudness, soft end fade) | original edit better on all 3: **all flags off** | 22 Nov |
| Scripts | gpt-5.4-mini vs Claude Sonnet 5.5 | **OpenAI** ("script open ai best"); cost $0.0035 vs $0.038 per script | 23 Nov |
| Director | | "make claude the director" | 23 Nov |
| Wan vs LTX 2.5 | L40S bake-off | mixed; CTO decided **Wan stays main**, LTX optional lane | 23 Nov |
| Sound effects | Earshot (MIT, CPU) vs Freesound | Gemini listening: Freesound 3 of 5; keep Freesound | 23 Nov |
| Camera | Wan 2.2 Fun Camera, Fun VACE | Fun Camera makes a **real** move (best result); VACE follows a drawn path | 24 Nov |
| Upscale | SeedVR2 3B vs Real-ESRGAN | SeedVR2 a little crisper at 2-4x cost: hero shots only | 24-25 Nov |
| Character motion | Wan 2.2 Animate | usable only with a GPU pose step and matched framing | 25 Nov |
| Hosted vs own Wan | same photographic start image | close; own pod far cheaper | 24 Nov |

### 8.3 Research recommendations (25 Nov, `reports/Best models per stage and motion.md`)

Headline: **"Fix the plan before buying models."** The ant film did not fail because Wan is the wrong model; it failed because the plan asked for nothing visible and the sampler configuration moves least. Recommended per stage:

| Stage | Recommendation | Price (25 Nov) | Fallback |
|---|---|---|---|
| Plan / creative direction | **Claude Opus 5.5, high effort** | $4/$20 per M tokens; under Rs 10 per plan | gpt-5.6-sol or gpt-6.2-sol |
| Research + fact-check | Sonnet 5.5 + web_search/web_fetch, or Gemini 3.8 Flash grounding | grounding free to 5K requests a month, then $14/1K | Perplexity Search $5/1K |
| Script | gpt-5.4-mini (owner pick) | $0.75/$4.50 | re-A/B vs Opus 5.5 and gpt-5.6-sol by ear |
| Shot planning | Sonnet or Opus 5.5, structured JSON, **one event per shot** | $2/$10 (Sonnet) | gpt-6.2-sol |
| Keyframes | **MAI-Image-2.7-Flash or Nano Banana 2.1, A/B vs FLUX.2 klein** (judged after animation) | $0.010 / $0.034 per image | open: FLUX 3 Image Klein, Qwen-Image-2.1 (licences unverified) |
| Image-to-video default | **Wan 2.2 I2V A14B with a fixed sampler + SVI 2.0 Pro** | about Rs 0.84 per video second | 720p distill checkpoints (Apr 2026) |
| Image-to-video hero | MAGI-2 | $0.08/s (Rs 7.5/s); ToS unchecked | Seedance 2.5 Flash $0.15/s; Boreal-H3 $0.04/s |
| Camera | one path per scene, held across chained clips; Uni3C or Fun Camera | GPU time | ATI / Wan-Move for object paths (480p; licences unchecked) |
| Clip review | gemini-4-hydrogen at 4-8 fps, pairwise questions | about Rs 0.23 per 5 s clip | owner final |
| Narration | ElevenLabs v4 Turbo (already default) | $0.04/1K chars, in Starter | Eleven v4 for hero lines; Gemini 3.8 Flash TTS |
| Music | Lyria 3.5 | $0.08 per song | ElevenLabs Music $0.15/min (explicit licence) |
| Sound effects | ElevenLabs SFX + curated Pixabay/Freesound CC0 bank | $0.12/min API or 200 credits a generation | Foley models only in Manual (licence risk) |
| Mix | FFmpeg sidechain ducking + two-pass loudnorm, -14 LUFS / -1 dBTP | free | |
| Captions | forced alignment to the script (WhisperX align / MFA) | free | ElevenLabs Scribe |
| Interpolation | RIFE 16 to 48, retime to 24; GIMM-VFI on fast moves | GPU seconds | |
| Upscale | FlashVSR to 4K once on the cut (Apache 2.0) | about 1 min per 10 s on a 5090 | SeedVR2 (can over-sharpen clean clips) |
| Publish | altered-content flag on, private upload, variety check | free | |

Key facts behind it:
- The lightx2v maintainers said in writing (13 Aug 2025) that the 4-step Wan 2.2 Lightning model's motion "tends to be slower"; community testers found **vertical resolutions stayed nearly static** even with the 3-sampler fix. The studio renders vertical 720x1280 with the 4-step distill on both experts: the worst reported combination.
- Fixes with community evidence: a few undistilled high-noise steps at real CFG first (CFG about 2-3.5 on the high-noise expert only), distill LoRA strength 0.6-0.8 on high-noise with 1.0 on low-noise, or 8 total steps split 4+4. Reports conflict. lightx2v shipped full 720p I2V 4-step distilled checkpoints on 12 Apr 2026 (no motion claim).
- Wan 2.2 weights the start of the prompt most; a clip with no motion cue comes out static or morphing. Write subject first, then a strong action verb, then camera; one action per clip.
- Fun Camera presets start every clip from zero velocity; a new preset every 5 s beat guarantees a visible change of direction. SVI 2.0 Pro conditions on the last 4-12 frames' motion latents, carrying velocity across the seam.
- Hosted video leads on motion now. MAGI-2 is #1 image-to-video on the Artificial Analysis board (1163 Elo) at $0.08/s. Sora 2's API ended 24 Sep 2026; Veo 3.1 left the Gemini API 22 Oct 2026. Hosted is about 9x the per-second cost of own Wan, so a hybrid is a **quality** decision, not a cost one. One 5 s MAGI-2 hero shot in a 60 s Short costs about Rs 38.
- FLUX.2 klein ranks low on the text-to-image board (the 9B at 931 Elo); MAI-Image-2.7-Flash 1160 at $0.010; Nano Banana 2.1 1175 at $0.034. A still's Elo is not how well it animates: judge after animation.
- Vision judges are weak at artefacts (Artifact-Bench: many near chance). Use them for "did a visible event happen?" and pairwise choice, sampled at 4-8 fps, calibrated on about 20 owner ratings, advising only.
- Narration: `eleven_v4_turbo` tops the Nov 2026 speech arena (1342 Elo). The open voice complaint is about voice choice and delivery, not the model. Chatterbox (1023) is far below: retirement confirmed. ElevenLabs page conflict: Starter "about 30,000 credits" vs the API page's "75,000 included characters" vs the owner's account showing 40,000 a month (the account figure wins).
- Sound effects licences: MMAudio checkpoints CC-BY-NC; HunyuanVideo-Foley under a Tencent licence that blocks the EU; Pixabay sounds free for commercial use; Freesound CC0 safe (filter out NC); BBC archive non-commercial.
- Prices changing: gpt-5.6-sol promo ends 30 Nov; Gemini 4 Argon and Carbon double 1 Dec; ElevenLabs Starter offer ends 1 Dec; Gemini TTS and Gemini 3.8 Flash double 1 Jan 2027 (already encoded as dated price changes in `providers.yaml` for text and TTS).

### 8.4 Earlier research reports (summaries)

- **Strategy research (18 Nov)**: platform rules are the biggest risk; Blender rendering cost-effectively; asset sources; AI video prices.
- **Project V studio review (18 Nov, "Ship the first video, then harden")**: engine solid; nothing published so the YPP clock had not started; no backups; money gaps (control plane reachable on the network, check-then-act budget with no reservation, Gemini failover could pay twice on 5xx). Recommended: upload by hand, nightly backups, bind to 127.0.0.1, tests for money code, quality-neutral savings (Batch tier keyframes, prompt caching, Haiku for small text), decide the Gemini cap. Most of these were later done (backups, 127.0.0.1, reservations, money tests, first video published).
- **Project V optimisation review (19 Nov, "Stop paying for cold starts and redos")**: Dream Short real content cost Rs 15-20 but finished Shorts cost Rs 34-52 and the autopilot demo Rs 92.78, the difference being cold starts (58% of the autopilot Short) and redo loops that repeat the same fix. SeedVR2 on Modal was a third of all GPU spend (a 10-minute film about Rs 3,570 on Modal vs Rs 255 on a 4090 pod). The Dream quality check was failing on every call (fixed 19 Nov). Led to warm redos, the Producer stop rule, honest ledger, the pod route.
- **Open source repos for Project V (17 Nov)**: lossless speed from attention and compilation, MiniMax H3 and LTX-2.5 as challengers, SeedVR2 for 4K finals, RIFE for frame rate, Qwen models for keyframes and alignment, licence decisions only the owner can make.
- **Wan video model production workflows**: Wan 2.2 powers building blocks, not films; open weights end at 2.2 (2.5 to 3.0 are hosted only, probably); producers pick a variant per job (I2V, first-last, VACE, Animate, S2V) and chain clips, one late upscale and interpolation, then a normal editor; named production use is thin.
- **AI video studio landscape (24 Nov)**: Project V's stack already matches the field. Adopt now: keep Wan 2.2 fp8 + LightX2V + SVI + last-frame chaining; FLUX.2 klein 4B never 9B; confirm ElevenLabs commercial terms; character and location bank and claims list (done); tiered quality gate (done); mix recipe; caption alignment; external killer on a non-GPU host; private upload with `containsSyntheticMedia`; plan for the 1 Feb 2027 bar and lead with long-form. Test next (each needs his yes): SeedVR2 vs Real-ESRGAN (done), interpolation order and GIMM-VFI, hosted hero tier (about $10-15), LongCat-Video vs SVI, LTX-2.5 vs Wan, Fun Camera (done), narration blind A/B, judge calibration. **Ignore**: MoneyPrinterTurbo, ShortGPT, FramePack, LongLive, Open-Sora for finals, MiniMax H3 and Pika H3 Flex (territory), HunyuanVideo-1.5 for a global channel, FLUX.2 klein 9B, MMAudio, Fish S2 Pro and VibeVoice for commercial use, Chatterbox as primary, Sora, avatar tools, stock assemblers, Remotion at 4K, consumer hubs, RunPod Serverless for Wan, buying a GPU. **Watch**: Wan 2.2 Animate with SVI, LTX-2.5 licence, Wan 3.0 weights, Veo enterprise price, Kling 4K, licensed Suno/Udio, Shorts Creator Pool, Topaz pricing.
- **CTO report (23 Nov)**: 314 commits in 8 days; first product shipped; direction decided; risks: no off-machine backup (then pushed), pasted keys (still not rotated), GPU reliability (later explained by the watchdog), personal documents on the work drive, migration scope, licences, Claude credits.

### 8.5 Licences at a glance

| Model / tool | Licence | Use |
|---|---|---|
| Wan 2.2 (I2V, T2V, Animate) | Apache 2.0 (Animate: model card and GitHub; HF link 404s) | yes |
| Wan 2.2 Fun Camera / Fun VACE | licence file not verified | in use; verify |
| FLUX.2 klein 4B | Apache 2.0, not gated | yes (never 9B: different licence) |
| Z-Image Turbo | used since 16 Nov | yes |
| LTX-2.x | Community licence, free under $10M revenue, full text not read; HF gated | optional lane only |
| MiniMax H3 (open weights) | territory restrictions: commercial use and **displaying outputs** barred in US, EU, UK, South Korea | **excluded** |
| HunyuanVideo 1.5, HunyuanVideo-Foley | Tencent licence excluding EU/UK/Korea | excluded from Auto |
| MMAudio | CC-BY-NC | excluded |
| Suno | commercial on paid plans but not ownership, litigation, no API | excluded |
| ElevenLabs (voice, SFX, Music) | Starter plan includes commercial use | yes (check Terms page once before first monetised upload) |
| Lyria 3.5 | general Gemini API terms, no explicit music licence | owner decision before monetising |
| Freesound | use CC0 and CC-BY only (credits) | yes |
| Pixabay | free commercial use, no attribution | yes |
| NASA imagery | generally OK with credit (maps excepted; NOAA unread) | per item |
| Chatterbox | MIT, Perth watermark | retired |

---

## 9. Quality bar and lessons learned

### 9.1 The quality bar

- **Real AI motion in every shot.** A panned or zoomed still is never acceptable output, even in a demo (HANDOVER Appendix D, CLAUDE.md rule 10).
- **Something visible happens.** Each shot needs one event with a beginning and an end that a viewer can see (added by the ant post-mortem, 25 Nov).
- **Quality first, cost second, automation third.**
- **A cost-saving change ships off by default** and becomes the default only after a side-by-side A/B on real shots that the owner judges equal (memory: quality-first-cost-cuts). Label each saving with its quality impact; prefer savings with zero quality impact (batching, one container, fewer redos).
- **Judge by eye and ear**, at full speed and full size, never from stills or metrics alone.
- **Sound is half the film**: narration clean and well paced, real ambience, effects on events, music under the voice, no silent gaps, master at about -14 LUFS with true peak at or below -1 dBTP.
- Target master: 4K was the original goal (3840x2160 by upscaling); the studio today delivers 1080p or 2560x1440. Say the real resolution up front, and say how it was made (generated 720p, upscaled).

### 9.2 Every lesson, grouped

**Planning and story**
1. Fix the plan before buying models. A shot without a written visible event (subject, verb, start state, end state) will come out as a camera move over a still scene (ant, 25 Nov).
2. Every shot needs a distinct picture idea; reject near-duplicate keyframes before any clip money (ant: 18 nearly identical dark meadow pictures).
3. Give the viewer the subject. The ant brief banned showing an ant; the owner asked "where are ants?" and wants the subject visible next time. Viewers will not watch "just grass".
4. Short-form pacing is not long-form pacing: the first ant shot plan came out as 45 fast shots for a calm 90 s film. Imported plan files now carry the pacing.
5. Shorts pacing (research): hook in the first 2 s with a visual change and a 5-9 word overlay; cuts every 2-4 s; a new visible event at least every 5-7 s; loop-friendly ending; burned-in captions.
6. The script must fill the time: every beat has narration or is marked as a deliberate sound moment (ant: about 16 words per 10 s scene left silent gaps). The narration check now warns at +10% and rejects at +25% per beat for over-long lines; it should also flag under-filled beats.
7. Science content needs a claim check: never state unverified numbers (Lake Natron: "coated and preserved", never "calcified"; Milky Seas: no area figure or Java event year). Label speculation.
8. Recurring objects drift between pictures: write one exact "hero" description into every picture and use one seed per Short (Dream, 19 Nov); a character and location bank (`studio/planner/bank.py`) does this for plans.
9. Never copy other creators' IP (no Harry Potter/Hogwarts); copy formats, not scenes.

**Camera and motion**
10. Camera like a person (standing, slow walk, car passenger), never a drone or dolly glide; slower is better (Dream, 19 Nov).
11. **One camera direction and speed per scene**, carried across its clips, with eased starts and stops. A new preset every 5 s beat reads as "left, straight, left" (ant).
12. Never put phone, person, hand, someone or holding in a Wan prompt: "phone held at chest height" made Wan add a man filming. Describe only how the view moves.
13. SVI barely moves the camera whatever the prompt says; Wan 2.2 Fun Camera makes real moves. Tested good: pan_right, slow_push, drift_right, zoom_in, pan_left. Bad: pan_down (black artefacts), fast_push (scene rewrites itself after about 2 s). Rolls warp. Post camera moves on the 4K plate (push-in plus drift) are a fallback; a post "zoom out" crashed ffmpeg.
14. Chain at most 2 beats with Fun Camera (5 s beats): from beat 3 the colour drifts blue/purple and detail melts. Restart from a fresh or colour-matched keyframe at beat 3. SVI drifts after about 5 beats; 3 beats per scene with a fresh keyframe was the earlier film rule.
15. The 4-step LightX2V distill slows subject motion, worst at vertical 720x1280. Test sampler fixes before buying hosted video.
16. 16 fps looks fake to the owner ("worst, not even looking real"): interpolate (RIFE to 32 or 48 fps) but remember interpolation adds smoothness, not motion.
17. Judge MOTION on a short clip before any longer render; state fps and resolution up front; never praise from stills (23 Nov).
18. The owner's reference videos (sea monster POV, broom POV, "it all starts with a cube") are commercial-model tier (Kling/Veo/Seedance) or Blender block-out with video-to-video restyle.
19. The owner's Moon reference Shorts (25 Nov): 4 of 5 are a single continuous shot, camera nearly still (0 to 9% of frame width per second), always some ambient motion, 9-13 s, audio at about -14 LUFS. A Moon scaling over a frozen plate would trip the motion gate: the plate must carry real motion (waves).

**Sound**
20. The owner rejects synthesised sound (procedural tones, thumps). Use real recordings: Freesound CC0 (filter out NC), Pixabay, or ElevenLabs Sound Effects; keep a source row per file (`SOURCES.md`).
21. Music sat as loud as the voice once (23 Nov): `MUSIC_VOLUME` is now 0.12 (voice about 9 dB above music).
22. Voice tests fatigue him (18 Nov): do not start voice experiments unprompted. The next voice test (v4 vs v4 Turbo, two other voices) is in the plan as a later, low-priority item.
23. ElevenLabs allows limited parallel requests: 3 workers got HTTP 429 and the whole video fell back to Gemini silently (22 Nov). Now 2 workers and retry on 429.
24. Sync check on the final cut: every line at its scheduled start, no overlaps (the TV video's v6 had a 0.22 s overlap; v7 fixed lines 5, 8, 11 with a minimum gap of 0.72 s).

**Picture and look**
25. Effects that announce themselves (haze, heavy grain, vignette, ripple overlays, shake, colour grades, gamma lifts) are out. A shadows-only lift was closer but could not save the ant footage.
26. A Blender kitbash set with flat fog looked cheap; Wan on top did not fix it ("dumbass video", about $1 spent before the look was judged).
27. Keyframes that look good as stills can still produce fake-looking motion; judge pictures after animation.
28. No generated lettering in pictures (prevent in prompts, catch in review).
29. Stock clips often match the words but not the shot (TV video: all three picks rejected).

**Money and process**
30. Never quote a price from a scaled guess: the 720p estimate said Rs 25, real Rs 47. Use measured costs; write real numbers back into `config/providers.yaml`; say "unmeasured" plainly.
31. Spend on a proof first: the ant video paid for all 18 clips before a cheap 2-scene proof. Rule now: one 10 s proof clip first.
32. A single-clip redo costs near Rs 15 because the model load is billed per run: group redos.
33. Paying a fresh GPU start-up for every redo round was 58% of the autopilot Short: keep the GPU warm across review, and stop repeat redos (same issue twice: ask the owner).
34. Settings where 0 means something (dissolve 0 = hard cut) must not be read with `x or default`; test the 0 case.
35. Restart the control plane and workers before every paid test: an old build skipped checks and wrote wrong prompts.
36. Before blaming something for an approval or action, read the Temporal workflow history (signals record time and sender).
37. Check the real cause before accepting a theory (the ElevenLabs "missing env var" was a 429; the vanishing pods were the watchdog).
38. A laptop internet drop mid-batch failed the step while Modal still ran the queued clip (19 Nov): cancel remote work when the connection is lost.
39. The studio entrypoints did not load `.env` until 25 Nov, so every key and flag was blank: after any entrypoint change, check that settings are really loaded.
40. Missing default fields in result records crashed two live saves (Moon keyframes, Moon clips) after the money was spent: give every optional field a default; prefer "resume the save" over "re-render".
41. Docker Desktop stops on this laptop (sleep, crash): check it first when the backend is unreachable; the lease helper fails without it.
42. A backend restart auto-resumes paused videos: re-pause duplicates.
43. Manual mode used to run a stage and then wait (it did not hold before running): fixed by hold-before-run (25 Nov).
44. Pod start (8-18 min) exceeded a 5-minute activity limit and retries re-rented pods: fixed with join-on-retry.
45. A pod disk filled (Z-Image 31 GB plus Wan 45 GB on 80 GB): download only the model groups a job needs.
46. Estimates that use worst-case prices scare and mislead (draft estimate $5.36, real about $1.0): estimate from live prices and measured times.
47. Two reaper loops ran at once and the old one kept the old limit; the reaper's 1.5 h limit would have killed a 1.5 h job. Use per-pod limits (`maxh=`), one copy, and a version in the log line.
48. Usage: very long agent chats get expensive; start fresh sessions from handovers.

### 9.3 The quality gate (what exists)

`tools/qualitygate/`, docs/quality-gate.md. Runs automatically after ASSEMBLY (`workers/activities/gate_check.py`, flag `QUALITY_GATE_ENABLED`, default on) and in the film CLI (`tools/film4k/edit.py`, exit 3 on fail).

| Tier | Check | Fails when |
|---|---|---|
| 1 | Loudness (ebur128) | more than 9 LU from -14 LUFS (warn at 3 LU) or true peak above 0 dBTP |
| 1 | Black frames | a black run of 1 s or more away from fades |
| 1 | Frozen frames | a frozen run of 2 s or more |
| 1 | Captions | a cue outside the video or overlapping; reading speed over 25 cps |
| 2 | Motion (frame difference) | below 0.25 (cannot catch panned stills) |
| 2 | Independent motion (removes one global camera move) | below 1.5 (warn below 4.5): catches panned and zoomed stills |
| 2 | Colour drift | warns above 0.9 (never fails; weak) |
| 2 | Sharpness | below 0.8 |
| 2 | Flicker | above 4.0 |
| 3 | Vision judge (OpenAI gpt-5.4-mini) | **off**: missed the blue-wing colour drift and called good beats near-still |

Calibration (25 Nov, few clips): panned still 0.9, zoomed still 3.4, real moving beats 5.5-10.8, near-static real beats 2.7-3.3 (warn). Warn means "look at it". Golden tests pin the synthetic results (`tools/golden`). What the gate cannot do: tell whether a **story event** happened, judge taste, or catch a shot that moves but is boring. Phase 2 adds a clip judge for "did a visible event happen?".

---

## 10. Decisions log

Every owner decision found in the memory notes and documents, oldest first. "Why" is his reason or the recorded reason. Superseded decisions are marked.

| Date | Decision | Why / source |
|---|---|---|
| 16 Nov | Product = prompt to any complete publish-ready video; MelodySheep is one preset | Revision R1 |
| 16 Nov | Every stage Auto or Manual; Review mode added at his request | R1 |
| 16 Nov | Build the product first; budget later; every real spend needs his approval | R1 decision 3 |
| 16 Nov | M1 before M0 (backbone before GPU benchmarks) | R1 decision 6 |
| 16 Nov | English first; dubs later | R1 |
| 16 Nov | Premium private studio, production-grade, not for sale | CLAUDE.md positioning |
| 16 Nov | Never use unofficial Flow API wrappers; one account per provider; never farm free tiers | HANDOVER rules, ToS |
| 17 Nov | Quality is priority; cost cuts ship off until an A/B shows equal quality | memory: quality-first-cost-cuts |
| 17 Nov | M2c approved after Midnight Descent | memory: milestone-status |
| 17 Nov | Claude for research and concept (blind A/B); Claude capped at $5, later $8 a month | git log, providers.yaml |
| 18 Nov | Architecture Authority policy (he decides architecture; concerns labelled) | memory: architecture-authority |
| 18 Nov | CLAUDE.md rule 4 removed | git log (content of rule 4 not recorded) |
| 18 Nov | Gemini monthly cap Rs 1,000 | providers.yaml |
| 18 Nov | Two channels (Science + Dreamscapes); pace 2-3 a week each; every video a new idea | memory: channel-decisions (YouTube July 2026 inauthentic-content update) |
| 18 Nov | Blender: laptop first; cloud only when laptop render time exceeds about 10 h, behind a cost gate | memory: channel-decisions |
| 18 Nov | Voice: "anything is fine": Chatterbox main, Kokoro safety net, Gemini backup (**superseded**: Chatterbox retired 24 Nov; ElevenLabs paid since 23 Nov) | memory: channel-decisions |
| 18 Nov | Stop voice experiments ("its too much") | memory: voice-quality-open |
| 18 Nov | Shorts under Rs 20 each; ignore Google Flow; new work on a separate branch, existing pipeline untouched | memory: short-cost-target |
| 18 Nov | Lyria for Dream music; his own track is the free default music bed; "dont worry about copyright" for his track | memory: short-cost-target |
| 18 Nov | AI Producer budget Rs 100 per Short | git log, providers.yaml |
| 19 Nov | Dream picture model Z-Image over Nano Banana (**superseded for Video** by FLUX on 21 Nov; Z-Image stays for "lonely") | memory: short-cost-target |
| 19 Nov | Dream clip quality 480p default (720p costs about Rs 47 vs Rs 25) | memory: short-cost-target |
| 19 Nov | First full new-style Dream Short approved (Walk-Up vending machine) | memory: short-cost-target |
| 19 Nov | "money is fine" during test and learning; spend still needs his yes | memory: short-cost-target |
| 19 Nov | Dreamscapes channel "Liminal Drift"; content broader than liminal | memory: short-cost-target |
| 19 Nov | Lyria over ACE-Step by ear; ACE-Step stays off | memory: acestep-music-ab |
| 19 Nov | AICredits text fallback on, Rs 150 cap; not in the router | memory: aicredits-fallback |
| 19 Nov | Daily autopilot approved (off by default), autopilot daily cap Rs 50 | docs/autopilot.md |
| 19 Nov | "learn from mistakes": the Dream lessons list | memory: dream-shorts-lessons |
| 19 Nov | Science channel restart: claim ledger first, four visual lanes, quality scorecard, prove one documentary first | HANDOVER Appendix C item 7 |
| 19 Nov | Model policy for Claude sessions (Sonnet/Opus, max Opus-medium; 21 Nov: Opus low) | memory: model-policy |
| 20 Nov | "start m3": GPU broker | memory: milestone-status |
| 20 Nov | Modal cap 0 until 1 Dec | providers.yaml |
| 20 Nov | fp8 Wan on 24-32 GB cards accepted | memory: gpu-plan-runpod |
| 20 Nov | Cheapest live price wins; one shared pod for all Shorts ("1 pod only to run as many Shorts as I want") | broker.py, git log |
| 20 Nov | Last-frame chaining over first-last (first-last stays off) | memory: first-last-ab-result |
| 20 Nov | Growth idea: viewers steer the dream (comments pick the next Short's path) | memory: audience-steers-dream |
| 20 Nov | Salad dropped ("waste") | memory: gpu-plan-runpod |
| 20 Nov | Handover to a different Claude account near the usage limit | memory: owner-working-rules |
| 21 Nov | FLUX.2 klein 4B default picture model | memory: picture-model-test-plan |
| 21 Nov | SVI good enough to build into the studio | memory: picture-model-test-plan |
| 21 Nov | Watchdog live; $3.50 per UTC day GPU cap; small SVI resume test | memory: picture-model-test-plan |
| 21 Nov | CEO/CTO roles | memory: cto-mode |
| 21 Nov | Vast.ai: use it (strangers' machines, tight filters) | providers.yaml comment |
| 22 Nov | Monthly rented-GPU total $20 (RunPod 12, Vast 12) | providers.yaml |
| 22 Nov | E2E parked | memory: e2e-parked |
| 22 Nov | All edit polish flags off | memory: edit-sync-verdict |
| 22 Nov | Warm-start image proven and used; Vast TOTP seed saved in `.env` | memory: warm-start-result |
| 22 Nov | New dashboard layout approved; Story hidden until Phase 2; new projects default to Review (10 min); old pages retired only on his approval per page | handover 22 Nov |
| 22 Nov | TV video: 14-line script verbatim, George voice, Lyria 117 BPM approved by ear, cap $3 then $4 then $4.20 | handover 22 Nov, TV REPORT |
| 22 Nov | Night mandate: CTO leads on free work; spend, accounts, keys, push stay his | memory: night-mandate-22nov |
| 23 Nov | Project data to `E:\project-data`; personal files never moved | memory: project-data-on-e |
| 23 Nov | Everything on E: is project except `E:\Downloads` | memory: quality-gate-openai-and-e-drive |
| 23 Nov | OpenAI key funds the quality gate; log every call, keep a cap | same |
| 23 Nov | Wan vs LTX: "you tell": Wan main, LTX optional lane | same |
| 23 Nov | One good Blender + Wan Short wanted (CTO proposal: Dyson-swarm attack) | same |
| 23 Nov | Channel name Strange If True, handle @strange-if-true; no channel name on thumbnails | memory: channel-identity |
| 23 Nov | "publish now": TV video public | memory: channel-identity |
| 23 Nov | ElevenLabs paid Starter plan bought | memory: elevenlabs-free-plan |
| 23 Nov | Post-TV queue: housekeeping yes; voice lock only for ElevenLabs-narrated videos; one pod per video yes; quality gate yes; FLUX default yes (Z-Image optional); C: to E: yes; Blender test scene yes; upscale test yes | docs/plans/post-tv-queue.md |
| 23 Nov | Direction: Python only; serverless GPU with models baked in; plan-then-make with fingerprints; small on purpose | memory: architecture-direction-23nov |
| 23 Nov | OpenAI gpt-5.4-mini writes scripts; Claude is the Director | memory: script-writer-openai |
| 23 Nov | Lease every test pod ("Yes, lease them"); never dodge the watchdog | memory: gpu-pods-need-watchdog-lease |
| 23 Nov | Milky Seas director plan approved (not made) | handover 23 Nov |
| 23 Nov | "approved all", "yes push" (quality gate, OpenAI writer, migration S5 go) | memory: quality-gate-openai-and-e-drive |
| 24 Nov | Java removed; Python only (tag `java-final`) | docs/java-removal.md |
| 24 Nov | Code changes and refactors on branches without waiting; never on main | memory: film-pipeline-24nov |
| 24 Nov | Film Act 1 at $4 cap; Act 2 needs yes and about $6 | memory: film-pipeline-24nov |
| 24 Nov | Pod tests $1 cap ("yes do both tests, $1 cap, also wan vace") | docs/pod-tests-24nov.md |
| 25 Nov | Motion and animation first, then a real video through the Python studio | four-month plan |
| 25 Nov | Motion block cap $4.5 | docs/film4k.md |
| 25 Nov | Secure pods only (`POD_CLOUD_PREFERENCE=secure_only`) | handover 25 Nov |
| 25 Nov | Daily GPU cap back to $3.50 | providers.yaml |
| 25 Nov | Per-video cap Rs 150 default | providers.yaml |
| 25 Nov | His yes plus figure in the CTO chat covers side sessions (quote his words) | memory: spend-approval-via-cto |
| 25 Nov | Ant video: "yes redo - 90 secs : and track cost from start : 180 rs approved"; L40S fallback up to Rs 193 | CTO conversation, handover |
| 25 Nov | Archive/Cancel button for videos (he chose it) | ant notes item 5 |
| 25 Nov | Ant video dropped; rejected synthesised sound, grade, ripple/shake, gamma lift | handover 25 Nov |
| 25 Nov | "you are cto: intelligent technical decisions are urs to make" | CTO conversation |
| 25 Nov | "It should be done by studio right?": the studio runs the pipeline, not an AI's approval scripts | CTO conversation |
| 25 Nov | Keep both lanes, independent ("dont remove: make it unaffected to each other"); "no" to the deeper shared-services split for now | CTO conversation, handover |
| 25 Nov | Full reconstruction for general videos; perfect sound; visible effects; better motion | CTO conversation |
| 26 Nov | Work moves to other agents (Gemini, now GPT/Codex) | GEMINI.md, this book |

---

## 11. What failed and why

Plain accounts of every significant failure, with the root cause and what changed. Read this before you propose anything: most ideas have been tried.

### 11.1 The ant video (25 Nov): the reason for the reconstruction

**What it was.** "What an ant hears at night", a 90 s vertical film about the sounds of a meadow at night as an ant would sense them. Plan file `docs/shorts/ant-hears-at-night-plan.json` (made by `tools/film4k/make_ant_plan.py`): 9 scenes x 2 beats of 5 s, 18 clips. Video id `a013569d-50c7-44e6-9c75-159cc25ca005`. An abandoned first attempt from a prompt (`539db64a-ab73-4623-8af7-a7e35d68fba6`, 45-shot plan, Rs 31.06 of text calls) still sits at SHOT_PLAN; archive it after the restart.

**Approval.** His words: "yes redo - 90 secs : and track cost from start : 180 rs approved". Later he allowed an L40S fallback "up to Rs 193 total" (not used).

**What ran.** PLAN to DRAFT_GEN through the studio on live Temporal, then it stopped at CRITIC (a placeholder that asked for money it would not spend). 18 Fun Camera clips on a Secure RTX 4090 ($0.89/h, about 1.3 h). Project total about Rs 155 of Rs 180 (est: about Rs 44 in API and earlier sunk spend, plus est Rs 110 of pod).

**Verdict.** "worst video". Unwatchable. "where are ants?" Camera "left straight left". Viewers would not watch "just grass".

**Root causes** (CTO post-mortem, confirmed by the research report):
1. **The plan had nothing to show.** 18 nearly identical dark meadow pictures; no visible event written into any shot. Wan had nothing to animate, so it moved the camera over still grass. The brief banned showing the ant itself.
2. **The sampler moves least.** The 4-step LightX2V distill on both experts slows subject motion (its makers say so); vertical 720x1280 is the worst reported case.
3. **Jerky camera.** The camera preset changed every 5 s beat, each starting from rest.
4. **No sound design.** AUDIO is a placeholder; the script was about 16 words per 10 s scene, leaving silent gaps, for a film whose idea was sound.
5. **Spent before proving.** All 18 clips were paid for before a cheap 2-scene proof.
6. **After-fixes made it worse.** Synthesised sound, a colour grade, ripple and shake overlays (`tools/film4k/ant_fx.py`), a gamma lift (washed out): all rejected. A shadows-only lift and real CC0 sounds were closer, but the footage could not be saved. He dropped the video.

**Operational problems found during the run** (`docs/ant-run-notes-25nov.md`):
- RunPod create returned a bare "400 Bad Request" (HIGH): now the studio logs RunPod's reason (`studio/gpu/broker/runpod.py`).
- The per-video cap blocked the L40S fallback (MEDIUM): the hold should offer "raise cap to X".
- The reaper's 1.5 h global limit was shorter than the job (HIGH): now 2.5 h; should read per-pod limits.
- Two reaper loops ran at once (MEDIUM).
- The abandoned attempt sat in "Needs you" (MEDIUM): archive route and button built (V48).
- DRAFT_GEN showed RUNNING with no progress (LOW): no "clip n of m" yet.
- The first shot plan was 45 fast shots (MEDIUM).
- Cost views disagree (LOW): `spent_inr` excludes pods.
- The shot-plan Inspector gave a false alarm on the plan file (LOW).
- Slow first clip: about 18 minutes of setup and model load (LOW).
- Clip review on contact sheets: the look was consistent and the camera moved, but the "sound made visible" effects (ripples, trembling dew, raindrop shockwave, footstep shake) barely showed; clips 11, 12, 15 dark and soft.

**Files.** Clips `E:\project-data\outputs\ant-redo\clips\clip00..17.mp4`; previews `film_v2_preview.mp4`, `film_v3_preview.mp4`; CC0 sounds `ant-redo\snd\` with `SOURCES.md`.

### 11.2 The first demo (research phase): slideshow
Panned PIL drawings with a Kokoro voice. "where is the video?" Rule: real AI video in every shot.

### 11.3 Wan fast mode (16-17 Nov)
"Needs better quality". Led to the quality-first rule and the draft/final cascade.

### 11.4 Voice (18 Nov)
Chatterbox default "not good"; Gemini Puck "still not matching"; he stopped the tests. Later ElevenLabs George was accepted for the TV video. The voice question is not fully closed (research: test voice choice and delivery, not a new vendor).

### 11.5 Dream Shorts first tests (19 Nov)
Price quoted from a scaled guess (Rs 25 said, Rs 47 real); a 0 setting read as "use the default"; Wan added a man filming because the prompt said "phone held at chest height"; the shelter changed shape every shot; a dropped internet connection left Modal rendering clips nobody collected; a stale build skipped checks. All became rules (chapter 9).

### 11.6 Autopilot demo Short (19 Nov)
Rs 92.78 for one Short: four GPU start-ups (58%) and a Producer that redid the same "visible human figures" problem three times before asking. Fixes: warm redos, the stop rule, firm negative prompts.

### 11.7 The Dream quality check (until 19 Nov)
Failed on every call because the Gemini API rejected a `dict` schema; one quality gate was silently off. Fixed with fixed named fields.

### 11.8 Hand-written SVI pod scripts (21 Nov)
Two runs lost: a 45-minute limit hit mid-chain and pods that never became ready. Rule: never run long SVI chains from ad-hoc scripts; use the studio path with resume, replace and timeouts.

### 11.9 Edit polish (22 Nov)
Beat snap, smart transitions, HQ interpolation and the rest looked worse ("disturbances when the clips merge"), probably the 0.12 s blend replacing the 0.5 s dissolve. All off.

### 11.10 The TV video first attempt (22 Nov)
Narration silently fell back to Gemini (ElevenLabs 429); the writer shortened the approved script; the shot plan was cut off at 8,000 tokens and looped; pod boot exceeded a 5-minute activity limit and retries re-rented pods; a pod disk filled; the monthly GPU cap ran out mid-run; worst-case estimates. About Rs 57 wasted on the duplicate. The second attempt shipped, but the run took 6 h 49 min of wall time, mostly waiting on fixes. The CTO had not watched or listened to the cut before reporting; the owner had to.

### 11.11 Vanishing pods (23 Nov)
Every test pod "vanished" after 10-20 minutes. Earlier reports said "cause unknown"; the real cause was the studio's own watchdog killing unleased `projectv-*` pods. Lesson: read your own logs before blaming the provider. Fix: leases for test pods.

### 11.12 Cathedral Blender set and the 16 fps game cinematic (23 Nov)
Procedural kitbash looked cheap; Wan on top did not save it. FLUX keyframes plus Wan 16 fps plus Real-ESRGAN looked "worst, not even looking real" in motion although the stills looked good. Lesson: judge motion first; 16 fps reads as fake; commercial-tier references need commercial-tier models or a Blender block-out restyled.

### 11.13 Unreal Engine (23 Nov)
Epic launcher installed and signed in, but the Install Engine dialog never opened (EpicWebHelper timeout). Left to the owner (run as administrator or reinstall). Not pursued.

### 11.14 E2E Networks and Jarvislabs (22 Nov)
E2E never created a node (A40 out of stock; L40S needs about 24 h of plan price as balance). Jarvislabs has no REST API. Parked. About Rs 2,305 sits on E2E unused.

### 11.15 Game film Act 1 (24 Nov)
Made for about $3.15 but SVI barely moved the camera; beats did not join exactly (seam SSIM 0.43-0.76); SVI drifted after about 5 beats; a pod's 75-minute safety cut a shard mid-scene; the boot script waited forever on ComfyUI (a 25-minute idle pod); Docker was off so the lease helper failed silently. Code fixed; Act 2 not started.

### 11.16 Moon Short (25 Nov)
First live run through the Python studio: reached a master (Rs 46.10) but two saves crashed on missing default fields after the money was spent (fixed by hand); the Inspector sent a Moon-free plate to the picture judge, which failed it and triggered a second pod (fixed). Not published.

### 11.17 Java to Python
Not a failure, but a cost: 7 days of Java, then a full port (S0-S7c) and removal on 24 Nov. 15 old Java workflows are still open in Temporal (9 Dream, 6 video) and the JDK is still installed; both need his OK to clean up.

### 11.18 Patterns behind the failures
- Planning and creative direction were the weakest link, not the models.
- Money was spent before a cheap proof was judged by eye.
- Quality was reported from stills, metrics or stage summaries instead of watching.
- Placeholders and missing defaults stalled or crashed runs after spending.
- The AI did things the studio should do (approvals by script), and the owner noticed.

---

## 12. The reconstruction plan

The owner wants a full reconstruction of the studio for general videos, both lanes kept and independent. The plan below comes from `GEMINI.md` section 4, `docs/handover-25nov-2026.md` section 7 and the research report's prioritised table. Tag **`pre-reconstruction`** marks the starting point. Waiting on him: a "go" for phases 1 and 2 (free code) and a rupee figure for the phase 3 tests.

Ground rules for the whole rebuild:
- Keep the two lanes independent. `studio/tests/test_lane_isolation.py` must keep passing; never make one lane import the other. Do not start the deeper shared-services split (he said no for now).
- Minimal, targeted changes (CLAUDE.md rule 11); no big-bang rewrite. Work on a branch; merge only on his word.
- Every stage keeps Auto, Review and Manual. The studio and dashboard run the flow; the owner approves in the dashboard.
- Temporal determinism: use `workflow.patched` for workflow changes; keep replay tests passing (`docs/runbooks/temporal-versioning.md`).
- After each phase: build, run the full tests and lint, show him something he can see (a plan printout, a screenshot, a clip), get his verdict.

### Phase 1: plans that have something to show (free code)

| Item | What to build | Where | Done when |
|---|---|---|---|
| 1a. Visible event per shot (CRITICAL) | The shot plan gives every shot a distinct picture idea and one visible event: subject, verb, start state, end state. Reject event-free prompts; write the motion prompt subject first, then a strong verb, then the camera. | `studio/agents/types.py` (a `VisibleEvent` model is already drafted, uncommitted), `studio/agents/director.py`, `studio/production/shot_stage.py`, `studio/planner/imported.py` (plan files need the same field), the Inspector | A plan with an event-free shot fails the Inspector; the Director prompt asks for events; tests cover both routes (prompt and plan file). |
| 1b. Variety gate | After KEYFRAMES, compare keyframes (image embeddings or a vision model); if two are near-duplicates, re-plan one before any clip is made. | keyframes stage, Inspector, `studio/production/creative_director.py` | Near-duplicate pictures are caught before DRAFT_GEN. |
| 1c. Camera per scene | One direction and speed per scene carried across its clips, eased starts and stops; the shot planner obeys `camera_warnings`. | `studio/planner/imported.py` (`camera_warnings`), `tools/film4k/camera_presets.py` (`plan_camera`, `TESTED`), `studio/production/camera.py` | No mid-scene direction changes unless the plan says so. |
| 1d. Script fills the time | Every beat has narration or is marked as a sound moment; flag under-filled beats as well as over-long ones. | `studio/planner/narration.py`, `config/narration.yaml` | Silent gaps are caught at plan time. |
| 1e. Shorts pacing rules | Hook in the first 2 s, cuts every 2-4 s, a visible event at least every 5-7 s, loop check, AI-content flag at upload, episode-variety check. | planner presets (`shorts_punchy`), Inspector | Pacing warnings at plan time. |
| 1f. Models | Planning and shot design on **Claude Opus 5.5** (`story_model` / `CLAUDE_STORY_MODEL`); keep scripts on `openai:gpt-5.4-mini` until a new A/B. Re-verify model ids and prices first (rule 7). | `studio/config.py`, `.env` line for the owner | Ledger rows show the new model; cost per plan under about Rs 10 (est). |

### Phase 2: test before spending (free code)

| Item | What to build | Notes |
|---|---|---|
| 2a. Proof clip gate | A new hold before DRAFT_GEN that renders one 10 s proof of the hardest scene and waits for the owner. | Uses the existing hold-before-run machinery; must work in all modes; release the pod while waiting. |
| 2b. Preview tier | Low-res or few-step previews of all shots in one pod session; full renders only for chosen takes; keep every take with a Provenance row. | Research: creators keep about 25% of takes; drafts on the high-noise expert at low res save 32-49%. |
| 2c. Clip judge | Gemini watches each clip at 4-8 fps and answers "did a visible event happen?" plus pairwise take choice. Calibrate on about 20 owner ratings. It advises; it never decides alone. | Candidate home: the CRITIC placeholder. Respect provider caps (`gemini_image_review`). |

### Phase 3: motion and sound (paid tests need his yes and a figure; about Rs 400 together, est)

| Test | Est cost | What |
|---|---|---|
| 3a. Motion sampler test | about Rs 85 | Same 3 shots with the current 4-step, a 3-sampler split (high-noise steps without the distill LoRA at CFG about 3), high-noise LoRA 0.6-0.8, 4+4 steps, and the April 2026 720p distill; plus a full-step ceiling and a horizontal-then-crop variant. Owner picks by eye. |
| 3b. Picture model test | about Rs 50 | FLUX.2 klein vs MAI-Image-2.7-Flash vs Nano Banana 2.1, judged after animation. |
| 3c. Hero shot test | about Rs 115-210 | Same event shots on fixed Wan vs MAGI-2 or Seedance 2.5 Flash. Read their commercial terms first. |
| 3d. AUDIO stage (free code) | free (ElevenLabs credits prepaid) | Turn `tools/film4k/ant_sound.py` (CC0 bed, event cues, ducking, -14 LUFS) into the real AUDIO stage: per-scene ambience bed, cue sounds on events and cuts, music ducked under narration, loudness master, a source row per file. Check what `workers/activities/sfx.py` (Freesound CC0/CC-BY) and `workers/activities/mixing.py` (ducking, two-pass loudnorm) already do before writing new code. Real recordings only (Freesound CC0, Pixabay) or ElevenLabs Sound Effects. **Get his OK on one scene's sound before doing a whole film.** |

### Phase 4: finish and clean up (free)

- Finishing chain: interpolate to 48 fps (RIFE locally, `INTERPOLATE_FPS=local`), 4K upscale when Modal or a pod is allowed (FlashVSR on the cut, or SeedVR2 3B for hero shots), captions by forced alignment to the script.
- Skip the 7 placeholder stages (CRITIC, TAKE_SELECTION, AUDIO until built, ROUGH_CUT_REVIEW, PACKAGING, FINAL_APPROVAL, PUBLISH) so videos never stall on empty steps.
- Fix the known bugs (chapter 13.3).

### First real video after the rebuild

A 30 to 45 s Short built around visible events (water ripples, drops, motion you can see), about Rs 60 to 80 of GPU (est), only with his figure. Proof clip first. He must be able to see the subject (lesson from "where are ants?").

### How the research ranks the changes (for priority calls)

1 visible event + variety gate (CRITICAL); 2 camera per scene (HIGH); 3 sampler A/B (HIGH, about Rs 85); 4 AUDIO stage + no-silent-gap gate (HIGH); 5 preview tier (MEDIUM); 6 clip judge (MEDIUM); 7 keyframe A/B (MEDIUM, about Rs 50); 8 hero-shot A/B (MEDIUM, Rs 115-210); 9 finishing chain (MEDIUM); 10 Opus planner and script re-A/B (LOW cost, high leverage); 11 narration voice test (LOW); 12 pacing and publish gates (MEDIUM); 13 keep restricted-licence models out of Auto (HIGH if ignored).

### The four-month plan around it

`docs/plans/four-month-plan.md` (25 Nov): month 1 (to 31 Dec) prove a real video end to end and make it safe (reaper scheduled, smoke-test Short, captions, publishing set-up, 2-3 Shorts published); month 2 (January) control and a repeatable Short (shot-level review, storyboard editor, continuity checker); month 3 (February) quality and the channel bar (hero tier A/B, narration A/B, audience loop, 10 published videos); month 4 (March) scale (personal, second channel) or productise (client work): his decision at the end of February. "No new research or tooling work until the pipeline has made and published real videos." The reconstruction now comes first inside month 1.

---

## 13. Open items, owner jobs and known bugs

### 13.1 Waiting on the owner (decisions)

1. "Go" for reconstruction phases 1 and 2 (free code).
2. A rupee figure for the phase 3 tests (about Rs 400 together, est), one at a time or together.
3. Merge and push: `GEMINI.md` (`d148e98`) is committed locally, not pushed. This book and `AGENTS.md` are new and uncommitted.
4. Whether the uncommitted `VisibleEvent` work in the main checkout should be kept (put it on a branch first).
5. Lyria licence for monetised videos (Gemini API terms only).
6. Register the pod reaper as a Windows scheduled task (needs his OK; command in docs/pod-reaper.md) or move it to a non-GPU host.
7. Clear the 15 stuck Java workflows in Temporal and uninstall the JDK.
8. Monthly GPU budget frame (suggested $50) and month 4 direction (end of February).
9. Game film Act 2 (about $6, est) if he still wants it.
10. Earlier proposals still unanswered: blind A/B of hosted models vs Wan (cap Rs 500, 23 Nov); the two Short plans of 24 Nov ("What if you fell through the Earth?" about Rs 70-85; "The hallway that keeps adding doors" about Rs 25-35); one real Fun Camera scene at 720p.

### 13.2 Owner-only jobs

- **Rotate pasted keys**: ElevenLabs, Hugging Face, RunPod, Vast (key and TOTP seed), E2E (key and token), OpenAI, Pixabay. None rotated as of the last record. Then put the new ones in `.env`; you may test them without printing.
- Pay the Google billing (a 403 "outstanding balance" blocked Lyria on 24 Nov; it worked on 25 Nov: check before relying on it).
- Phone verification on YouTube for custom thumbnails (the TV video's thumbnail "A BEAM PAINTS EVERY FRAME" is waiting).
- Restart the studio to apply V48 (or let you do it when nothing runs), then archive `539db64a...`.
- Check the ElevenLabs Starter commercial terms on the Terms page once before the first monetised upload (a check, not a blocker; never call the plan free).
- Keep a second private copy of his personal documents (only one copy on `E:\Downloads`). Not GitHub, not any cloud job.
- YouTube OAuth for the studio uploader (docs/autopilot.md), if he wants studio uploads.
- Unreal: run the launcher as administrator or reinstall, if he still wants Unreal.

### 13.3 Known bugs (not fixed at handover)

| Bug | Where | Severity |
|---|---|---|
| Video list `spent_inr` leaves out pod rentals (broker writes rentals directly to `cost_entry`) | `PgBrokerRecords.cost` vs `CostLedger.record` | LOW but confusing |
| MUSIC asks the Lyria cap for the maximum number of songs; a 90 s video asks Rs 33.84 and is refused (cap Rs 15 per video, Rs 30 per day) | `MusicStage`, `providerCaps.gemini_lyria` | MEDIUM |
| KEYFRAMES hold text quotes the Gemini picture price (about Rs 85 for 18) instead of the pod price | keyframes hold estimate (`d.pricing.image_inr("1K") * n`) | LOW |
| Shot-plan Inspector false alarm on imported plans (checks Short rules against a 90 s film) | Inspector | LOW |
| No "clip n of m" progress or time left on the stage card | dashboard + media worker | LOW |
| Old lease rows unreleased (`projectv-salad-...` 19 Nov, `projectv-motionblock` 24 Nov) | `gpu_pod_lease` | LOW (bookkeeping) |
| `config/providers.yaml` ElevenLabs block says `plan: free`, 10,000 credits, non-commercial | providers.yaml | LOW in code, HIGH for trust: fix the text so no agent repeats it |
| `docs/pipeline-end-to-end.md` NARRATION row and estimate notes still mention "ElevenLabs free plan" in two places | docs | LOW (fix wording) |
| CREATIVE_REVIEW booked on Claude although the code passes no preferred model | ledger vs code | LOW, unexplained |
| First Moon pod rental booked 0.0006 h although it ran about 5 minutes | broker records | LOW, unexplained |
| Reaper: RunPod field names assumed; one global time limit; hand-run loop; no single-instance lock | `tools/podkit/reaper.py` | MEDIUM |
| Colour matching between chained beats not implemented | clips | MEDIUM |
| Clip activity should cancel remote work when the connection drops | `workers/activities/clips.py` | MEDIUM |
| Placeholder stages can stall a video in Manual | workflow | MEDIUM (phase 4) |

### 13.4 Unproven (risks)

- One-pod mode (`VIDEO_ONE_POD`), `tools/film4k/run_shard.py`, the Community abort-and-retry rule, VACE repair, long chained-beat drift, the reaper on a real pod, INTERPOLATE inside a live workflow, Temporal replay of old histories against the newest stages, VIDEO_OUTPUT_HEIGHT=2160 end to end, the Producer deciding alone, the PUBLISH stage, diagram quality from LLM-written specs.
- Whether the ledger's pod charges match the RunPod bill (ledger rows are estimates; the bill lags).
- Live `.env` values (never read by design).

### 13.5 Backlog after the reconstruction (from the takeover guide and four-month plan)

Run Shorts through the studio and publish; schedule the reaper; script-aligned captions; publishing set-up (Private first, AI disclosure, `publishAt`); blue-wing repair with VACE (about $0.5); tune gate thresholds; interpolation order and GIMM-VFI; LongCat-Video and LTX-2.5 tests after licence reads; narration blind A/B; audience loop (comments choose the next Short); Dreamscapes channel cadence; Milky Seas Short (plan approved 23 Nov, Blender scenes on a branch); Lake Natron (on hold); Blender + Wan Dyson-swarm Short; analytics review of the TV video (48 h and 7 days); secret scanning is in CI already (gitleaks).

---

## 14. Commands, environment and runbooks

### 14.1 The machine

- Windows 11 Home, Asus TUF Gaming A17 FA706IC (Ryzen 7 4800H, PCIe 3.0), **RTX 3050 Laptop 4 GB**, 32 GB RAM. C: about 454 GB (little free); **E:** WD_BLACK SN7100 931 GB (project data).
- Shells: PowerShell 5.1 (no `&&`), Git Bash. Use `127.0.0.1`, not `localhost` (IPv6 stalls here).
- Installed and used: uv (Python 3.11), Docker Desktop (WSL2), Node, FFmpeg on PATH, Blender 5.3 at `E:\Blender\blender.exe`, RIFE at `E:\tools\rife`, Real-ESRGAN at `E:\tools\realesrgan`, `runpodctl` v3.1.1 at `C:\dev\tools\runpodctl`, Earshot on `E:\project-data\tools\earshot`. JDK still installed (to remove with his OK).
- Junctions: Docker disk `E:\project-data\docker\disk`, uv cache `E:\project-data\uv-cache`, Hugging Face `E:\project-data\huggingface`. Outputs `E:\project-data\outputs\`. Logs `E:\project-data\logs\`.
- Docker Desktop sometimes stops (sleep, crash): check it first.
- The owner runs installs himself: give exact commands.

### 14.2 Start and stop

```powershell
# services (Postgres 5433, Temporal 7233 + UI 8233, SeaweedFS S3 8333)
docker compose -f infra/docker-compose.yml up -d
# control plane on 127.0.0.1:8080 (applies migrations at start; loads .env)
uv run --group studio python -m studio
# Temporal worker for the control plane (queue projectv-control-py)
uv run --group studio python -m studio.worker
# media worker (queue projectv-media; needs FFmpeg)
uv run python -m workers.main
# dashboard on 127.0.0.1:5173
npm --prefix dashboard run dev
# after a reboot: everything
powershell -File tools\ops\start_studio.ps1
```

Never restart the backend while a video stage, a Short or a pod is running (check first). A restart auto-resumes paused videos; re-pause duplicates. Two control planes must never run together (two watchdogs).

### 14.3 Test and lint

```powershell
uv run --group dev --group studio python -m pytest      # all tests (use project defaults; a bare tools/ path collects a Blender script needing bpy)
uv run --group lint ruff check .                         # lint
uv run --group dev python -m pytest tools/podkit tools/film4k tools/gpu_lease   # film tools, no network
$env:GOLDEN_UPDATE = "1"; uv run --group dev --group studio python -m pytest tools/golden -q; Remove-Item Env:GOLDEN_UPDATE   # deliberate golden update, then read git diff tools/golden/fixtures
```

Tests that write data run on a scratch database and skip without local Postgres. CI (GitHub Actions) runs on PRs and pushes to main, with a pgvector Postgres service, a migrations check and gitleaks.

### 14.4 Useful API calls (read-only unless noted)

- `GET /api/videos`, `GET /api/videos/{id}`, `GET /api/videos/{id}/cost` (true ledger totals), `GET /api/videos?archived=true`.
- `PUT /api/videos/{id}/archive {"archived": true}` (refuses a running video; a write: ask first if it is not obviously his request).
- `POST /api/videos` with `planFile` (starts a video: may lead to spend; holds protect it, but ask).
- `POST /api/plans/validate` (dry run, free): validates a plan file and estimates clip cost.
- `GET /api/gpu/pods` (should be empty when idle), `GET /api/gpu/broker/providers`, `GET /api/budget`, `GET /api/system`.
- Temporal UI `http://127.0.0.1:8233`. Live status page `http://127.0.0.1:8099` (`uv run python tools/live_status/status_server.py`).
- Leases: `docker exec projectv-postgres-1 psql -U projectv -d projectv -c "select pod_id,name,heartbeat_at,released_at from gpu_pod_lease order by created_at desc limit 5"`.

### 14.5 Tools you will use

```powershell
uv run python -m tools.qualitygate <video> --shots auto --judge off      # free quality gate
powershell -File tools\podkit\reaper.ps1                                  # reaper dry run (prints WOULD TERMINATE lines)
python tools\gpu_lease\lease.py keep runpod <pod_id> <name> "<purpose>" --state <json> --max-minutes 120
uv run python -m tools.film4k.ant_sound <outputs_dir> [video]              # the CC0 sound bed prototype
uv run python -m workers.diagram.templates <out_dir>                       # Manim diagram samples
uv run modal deploy workers/modal_apps/zimage.py                           # Modal app (capped until 1 Dec; ask first)
```

### 14.6 Runbooks in the repo

- `docs/runbooks/first-live-short.md` (long): running the first live Short through the Python studio, including the Moon layer (5b) and interpolation (5c).
- `docs/runbooks/temporal-versioning.md`: how to change workflow code without breaking replay (`workflow.patched`), determinism guard tests.
- `docs/runbooks/restore-drill.md`: backup and restore verified (counts match). Daily backups: pg_dump of every studio database, `.env` and `config/` to `C:\dev\backups`, 14 days (`scripts/backup.ps1`).
- `docs/runbooks/ssd-move.md`: moving data to the media SSD (done).
- `docs/pod-reaper.md`: reaper rules, scheduled-task command, running on a free non-GPU host.
- `docs/m3-gpu-router-runbook.md`, `docs/warm-start-plan.md`, `docs/svi-scenes.md`, `docs/stock-footage-lane.md`, `docs/video-lanes.md`, `docs/film4k.md`, `docs/quality-gate.md`, `docs/autopilot.md`, `docs/vetting-lab.md` (scan third-party tools before installing).

### 14.7 Start-of-session checklist

1. `git status` and `git log --oneline -10`. Note uncommitted work; do not overwrite it.
2. Check no pod is running: `GET /api/gpu/pods`, reaper dry run or `E:\project-data\logs\reaper-loop.log`.
3. Read the latest handover in `docs/` and the last section of this book's chapter 13.
4. If services are down: Docker first, then compose, then studio and workers.

### 14.8 End-of-session checklist

1. Tests and lint run, real results noted.
2. No pod running, no open lease you created.
3. A short dated handover (state, spend, open items), for example `docs/handover-<date>.md`, and a notes file of problems found during any run (like `docs/ant-run-notes-25nov.md`).
4. A short plain report to the owner: done, not done, est, not verified, what needs him.

### 14.9 How to report to him (template)

```
Done: <one or two lines>
Not done / not verified: <lines>
Spend: Rs X of Rs Y approved (est where marked); no pod running.
Needs you: <one short question, or none>
```

---

## 15. Glossary

- **Animatic**: the video assembled from still keyframes with voice and music, to check story and pacing before video spend.
- **Asset / Provenance**: every generated file is an asset with a provenance row (provider, model, prompt, seed, licence).
- **Beat**: a piece of a video, usually 5 s; one picture and one clip per beat. A 90 s video has 18.
- **bf16 / fp8**: number formats; fp8 halves memory so Wan 14B fits a 24 GB card. The owner accepted fp8.
- **Broker (GPU broker)**: the only way the studio rents a GPU (`GpuBroker.acquire()`): cheapest live price, caps, lease.
- **Canary**: a small paid test that proves a new GPU provider before Auto may use it.
- **Chain / last-frame chaining**: clip N+1 starts from the last frame of clip N. At most 2 beats with Fun Camera.
- **ComfyUI**: the node-graph engine that runs the image and video models on the pod.
- **CostEntry / ledger**: one row per cost, in rupees (Rs 94/$, OpenAI at Rs 90/$); estimates flagged.
- **Creative Director**: the model that checks the shot plan and pictures tell the story.
- **Distill / LightX2V / 4-step**: a speed-up LoRA that cuts Wan to 4 sampling steps; faster but slower motion.
- **Dream Shorts lane**: the separate pipeline for music-led surreal Shorts (`studio/dream`), with the autopilot.
- **Draft / final**: first clip render vs a full-quality second pass. Camera clips are already final quality.
- **Elo**: a ranking score from blind votes (Artificial Analysis boards).
- **Fun Camera / Fun VACE**: Wan 2.2 variants: a real camera move from a preset; a camera path from a drawn control video (and masked repair).
- **Hero shot**: an important shot (opening, climax, reveal), at most 20% of shots.
- **Hold before run**: a paid stage stops and asks Approve/Skip before spending.
- **Independent motion**: the quality-gate metric that removes one global camera move and measures what is left; catches panned stills.
- **Inspector**: free automatic checks after a stage, using different tools from the stage.
- **Keyframe**: the still picture a clip is animated from.
- **Lease**: a database row that proves a pod is on purpose; the watchdog kills `projectv-*` pods without one.
- **LUFS**: loudness unit; YouTube target about -14 LUFS integrated, true peak at or below -1 dBTP.
- **Mode**: Auto, Review (waits a window, then continues), Manual (waits), Producer (an AI advises).
- **Placeholder stage**: a stage that exists in the enum but does nothing yet (CRITIC, TAKE_SELECTION, AUDIO, ROUGH_CUT_REVIEW, PACKAGING, FINAL_APPROVAL, PUBLISH).
- **Plan file**: a finished JSON plan (`docs/shorts/*.json`) that replaces the paid planner, writer and Director.
- **Pod**: a rented GPU machine (RunPod, Vast), billed by the hour.
- **Reaper / kill-timer**: the external script that kills pods even when the studio is off.
- **REDO_WITH**: which later stages are redone when a stage is redone.
- **RIFE / GIMM-VFI**: frame interpolation (16 to 48 fps). Adds smoothness, not motion.
- **SeedVR2 / Real-ESRGAN / FlashVSR**: upscalers (quality, bulk, fast 4K).
- **Secure / Community (RunPod)**: datacentre pods vs cheaper community hosts (unreliable). Owner chose Secure only.
- **SVI (SVI 2.0 Pro)**: a Wan 2.2 LoRA for long continuous scenes from motion latents; barely moves the camera.
- **Temporal**: the durable workflow engine; workflow code must be deterministic.
- **Visible event**: what the reconstruction requires per shot: subject, action, start state, end state.
- **Wan 2.2 I2V A14B**: the open (Apache 2.0) image-to-video model the studio uses; two experts (high-noise, low-noise).
- **Warm image**: a pod image with ComfyUI preinstalled (no weights) that cuts boot to 8-18 minutes.
- **Watchdog**: the studio loop that terminates unleased, idle or over-time `projectv-*` pods.
- **YPP**: YouTube Partner Programme.

---

## 16. Appendix A: source conflicts (newer wins)

| Topic | Older source says | Newer source says | Use |
|---|---|---|---|
| ElevenLabs plan | free, non-commercial (memory 22 Nov; `config/providers.yaml`; TV REPORT; HANDOVER 12.5; parts of `docs/pipeline-end-to-end.md`) | **PAID Starter, commercial, 40,000 characters a month, bought 23 Nov** (memory elevenlabs-free-plan, 25 Nov; handover 25 Nov; GEMINI.md) | PAID. Fix the stale text when you touch those files. |
| Main voice | Chatterbox main (18 Nov); Gemini voices default (18 Nov) | ElevenLabs v4 Turbo George; Chatterbox retired (24 Nov) | ElevenLabs |
| Control plane | Java 21 / Spring Boot (HANDOVER, ADR 0001, old handoffs) | Python `studio/` (24 Nov, java-removal.md, CLAUDE.md) | Python |
| Object store | MinIO (HANDOVER) | SeaweedFS (ADR 0001) | SeaweedFS |
| Picture model | Z-Image (19 Nov Dream A/B) | FLUX.2 klein 4B (21 Nov); Z-Image only for Dream "lonely" | FLUX |
| GPU route | Modal first (16-19 Nov); "no rented pods, serverless" direction (23 Nov) | Rented RunPod pods are the live route; serverless on the research ignore list (24 Nov); Modal capped to 1 Dec | Pods, until a measured test and his OK |
| Daily GPU cap | $5 and $10 while testing (21-22 Nov) | $3.50 (25 Nov) | $3.50 |
| Chain length | 3 beats per scene with SVI (24 Nov, film4k) | at most 2 beats with Fun Camera 5 s beats (25 Nov motion block) | 2 for Fun Camera |
| Channels | one channel (16 Nov); "Under Every Surface" (23 Nov morning) | Strange If True (23 Nov) + Liminal Drift for Dreamscapes (19 Nov) | Strange If True |
| Reaper | dry-run, not scheduled (DEVELOPER-TAKEOVER, pod-reaper.md) | owner runs `reaper_loop.ps1 -Apply -MaxHours 2.5` by hand (handover 25 Nov) | hand-run loop, not scheduled |
| Interpolation | off by default (pipeline-end-to-end) | `INTERPOLATE_FPS=local` set by the owner this week (handover) | on in his `.env` (unverified) |
| Text model | code default `gemini-3.6-flash` | live `openai:gpt-5.4-mini,gemini-3.6-flash` (handover, `/api/system`) | live value |
| Relayed spend approvals | never count (memory owner-working-rules, 20 Nov) | his yes + figure in the CTO chat may be passed to side sessions, quoting his words (25 Nov) | only his own words with a figure; a relay from another session without his words does not count |
| Test count | 1,011 passed (DEVELOPER-TAKEOVER) | 1,292 passed, 2 skipped on commit 6e743d4 (26 Nov) | the 26 Nov figure (whole repo: studio, workers, tools) |
| Short cost target | under Rs 20 (18 Nov) | research: an all-Wan Short at realistic pod times is about Rs 50 (25 Nov, est) | target stands for daily production; re-measure |
| Pods vanishing | "cause unknown" (CTO report 23 Nov morning) | studio watchdog killed unleased pods (23 Nov evening) | watchdog |
| Owner's track copyright | (no concern raised) | "dont worry about copyright" for his own track | do not raise again |

## 17. Appendix B: where every fact came from

Memory notes (`C:\Users\piyus\.claude\projects\C--dev-project-v\memory\`): acestep-music-ab, aicredits-fallback, architecture-authority, architecture-direction-23nov, audience-steers-dream, channel-decisions, channel-identity, cto-mode, dream-shorts-lessons, e2e-parked, edit-sync-verdict, elevenlabs-free-plan (now says PAID), film-pipeline-24nov, first-last-ab-result, gpu-plan-runpod, gpu-pods-need-watchdog-lease, java-to-python-migration, milestone-status, model-policy, night-mandate-22nov, owner-working-rules, picture-model-test-plan, pod-tests-24nov, project-data-on-e, quality-first-cost-cuts, quality-gate-openai-and-e-drive, script-writer-openai, short-cost-target, spend-approval-via-cto, video-look-lessons-23nov, voice-quality-open, warm-start-result, and the index MEMORY.md.

Repo documents: `CLAUDE.md`, `GEMINI.md`, `docs/HANDOVER.md`, `docs/handover-25nov-2026.md`, `docs/DEVELOPER-TAKEOVER.md`, `docs/pipeline-end-to-end.md`, `docs/plans/four-month-plan.md`, `docs/ant-run-notes-25nov.md`, `docs/film4k.md`, `docs/quality-gate.md`, `docs/pod-reaper.md`, `docs/pod-tests-24nov.md`, `docs/java-removal.md`, `docs/video-lanes.md`, `docs/autopilot.md`, `docs/CLIENT-OVERVIEW.md`, `docs/adr/0001-m1-stack-updates.md`, `docs/handoffs/2026-11-22-cto-handover-2.md`, `docs/handoffs/2026-11-23-cto-handover.md`, `docs/plans/post-tv-queue.md`, `docs/plans/dream-shorts.md`, `docs/shorts/two-shorts-plans-24nov.md`, `docs/shorts/moon-reference-analysis.md`.

Reports: `reports/Best models per stage and motion.md`, `reports/AI video studio landscape.md`, `reports/Wan video model production workflows.md`, `reports/CTO-report-2026-11-23.md`, `reports/Project V studio review.md`, `reports/Project V optimisation review.md`, `reports/tv-explainer/REPORT.md`.

Code read: `studio/workflow/stage.py`, `studio/workflow/video_production.py` (header and policies), `studio/gpu/broker/broker.py` (header), `studio/config.py`, `config/providers.yaml`, headers of `workers/activities/sfx.py`, `workers/activities/mixing.py`, `tools/film4k/ant_sound.py`, `studio/planner/imported.py`, `studio/planner/bank.py`, `workers/pod/server.py`, `studio/gpu/watchdog.py`, `studio/gpu/self_stop.py`, `tools/gpu_lease/lease.py`; directory listings of `studio/`, `workers/`, `tools/`, `dashboard/src`, `db/migration`, `config/`; `git log` (457 commits) and `git diff --stat` of the uncommitted work.

From the CTO conversation (not all in files): the owner's quotes "you are cto: intelligent technical decisions are urs to make: i have other works to do", "It should be done by studio right?", "yes redo - 90 secs : and track cost from start : 180 rs approved", the Rs 193 L40S allowance, "how many times i need to say - its premium", "worst video", "where are ants?", "left straight left", "just grass", "dont remove: make it unaffected to each other", the "no" to the shared-services split, the reaper loop details, and what pleases or angers him.

Not read in full (skimmed or not opened): `docs/runbooks/first-live-short.md`, `docs/plans/java-to-python-migration.md`, `docs/studio-redesign-plan.md`, `docs/stock-footage-lane.md`, `docs/e2e-provider-notes.md`, `docs/jarvislabs-provider-notes.md`, `docs/m3-gpu-router-runbook.md`, older handoffs of 19-21 Nov, `reports/Open source repos for Project V.md` (headings only), `reports/2026-11-18 Strategy research - cost-effective quality.md` (headings only). Their decision-relevant content is covered through the memory notes and later documents, but check them if a detail matters.

*End of the book. Keep it current: when a decision changes, update chapter 10 and Appendix A in the same commit.*
