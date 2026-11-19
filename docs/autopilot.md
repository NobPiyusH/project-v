# Daily autopilot (Dreamscapes Shorts)

Owner approved 19 Nov 2026. **Off by default.** Turn it on in the dashboard: **Autopilot** tab.

## What it does each day

1. At the start time (default 07:00, India time) it takes the next idea: your own list first (oldest first), then an
   unused Scout angle from earlier Shorts (if "Use Scout ideas" is ticked). No ideas = the day is skipped, nothing spent.
2. It starts one Dream Short with every step in **AI Producer** mode, deciding by itself on this Short:
   - it reviews the plan, pictures, clips, music and the finished Short (Gemini reads, looks, watches, listens);
   - approves, or redoes only the weak shots with its precise notes (3 rounds per step at most);
   - approves the clips' cost only when it is ₹50 or less and the Short stays inside **₹100** and the **daily cap**;
     anything above waits for you ("Needs you").
3. When the Short is finished it writes a title, description and hashtags (#Shorts first) and the Short waits in the
   Autopilot tab as **Ready to post**. You can edit the words and choose Private / Unlisted / Public.
4. **Nothing is uploaded until you tap Post.** Then it uploads once through the YouTube Data API (marked as AI-made,
   "altered or synthetic content"). A failed upload shows the reason; tap Post again when it is fixed.

If a day fails or needs you, **the next days wait** until you deal with it (finish it in the Short's page, or tap
Dismiss). A Short waiting for Post does not hold up the next day.

## Modes (rule 2a)

| Step | Auto | Review | Manual | AI Producer |
|---|---|---|---|---|
| Start | starts at the time | waits the set minutes, then starts | waits for your Start | – |
| Scout … Finish | goes on | waits the review window, then goes on | waits for you | reviews and decides (default) |
| Post | – | – | **always your tap** | – |

Every step's mode can also be changed on the Short's own page while it runs.

## Money and records

- Limits: ₹100 per Short and "ask me above ₹50" (`config/providers.yaml`, `producer:`), and the daily cap (Autopilot tab,
  default ₹100). The Scout (≈₹6), writer and pictures run before the clips and count toward the limits.
- Every Producer review is a `producer_decision` row (linked to the Short) with its cost entry; the upload is a ₹0
  cost entry (YouTube quota). Provenance and routing decisions are written by the Dream Short steps as usual.

## The backend must be running

The schedule runs inside the control plane (every minute). If the laptop was off at the start time, today's run
starts when the backend is next running that day.

## One-time YouTube sign-in (owner)

This touches your Google account, so you do it yourself, once:

1. In the [Google Cloud console](https://console.cloud.google.com/), use (or create) a project, enable **YouTube Data API v3**,
   set up the OAuth consent screen (External, add yourself as a test user), and create an **OAuth client ID** of type
   **Desktop app**. Download its JSON.
2. From `C:\dev\project-v`, run:

   ```powershell
   uv run python -m workers.youtube.auth C:\path\to\client_secret.json
   ```

   Sign in with the channel's Google account and allow "Manage your YouTube videos" (upload only is requested). The
   sign-in is saved to `.secrets\youtube-token.json` (git-ignored, included in the daily backup). Nothing is printed.

**Important (Google's rule):** videos uploaded through a Google Cloud project that has not passed YouTube's API audit
are locked as **Private**, whatever privacy you choose. You can make them public by hand in YouTube Studio, or apply
for the audit (free, takes a few weeks) to post public directly. The Ready card shows the privacy YouTube actually set.

Also, while the consent screen is in "Testing", Google expires the sign-in after 7 days. To make it last, set the
consent screen to "In production": Google then shows an "unverified app" warning at sign-in, which you can click
through for your own use. Otherwise, re-run the sign-in weekly.

## Posting from your phone

The dashboard listens only on the laptop (127.0.0.1) so nobody else on the Wi-Fi can reach the studio. To tap Post from
the phone on the same Wi-Fi, two changes are needed, and both are your call:

1. Start the dashboard with `--host` so it listens on the network: `npm --prefix dashboard run dev -- --host`
2. Allow port 5173 through Windows Firewall for **Private** networks only (Windows asks the first time).

Then open `http://<laptop's Wi-Fi IP>:5173/#/autopilot` on the phone. **Risk: MEDIUM.** The studio has no login,
so anyone on the same Wi-Fi could use it (start Shorts, spend within the caps, post). Only do this on your home
Wi-Fi. A PIN for network access can be added later if you want it.
