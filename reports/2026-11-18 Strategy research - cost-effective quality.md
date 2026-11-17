# Project V: how to proceed (cost-effective + good quality) — Research Report
*Generated: 18 Nov 2026 | Searches: 10 | Sources: ~30 (mostly secondary/vendor; flagged) | Confidence: Medium*

## Executive summary
1. **YouTube's July 2026 inauthentic-content update targets generic, repetitive, template-based videos — explicitly including AI, CGI and reusable templates — judged at the channel level.** Any plan built on templates (Blender kits, AI recipes) must guarantee real variation, an original story per video, and human input. This shapes everything below.
2. **Blender belongs on RTX consumer GPUs**, which are cheapest per frame: Vast.ai RTX 4090 median ≈ $0.47/h (floor $0.13), RTX 5090 ≈ $0.48–1.02/h, versus Modal L40S $1.95/h. **EEVEE is unreliable headless on Linux cloud GPUs; Cycles is the safe cloud path.** So: EEVEE (low-poly, explainer) on the laptop under Windows; Cycles (photoreal cosmic) in the cloud.
3. **Hosted video models are now cheap enough to compare against our Wan finals:** Veo 3.1 Lite ≈ $0.05/s (720p), Veo 3.1 Fast ≈ $0.10/s, LTX-2.3 Fast ≈ $0.04/s (1080p), Kling 3 ≈ $0.07–0.11/s. Our Wan hybrid finals cost ≈ $0.057/s. A hero-shot A/B (Veo Lite/Fast vs Wan) is the cheapest quality lift available.
4. **Free assets cover low-poly well** (Kenney, Quaternius: CC0); Poly Pizza is mixed (mostly CC-BY, check per model). Space tools: Astro-Lab is free ($0+); good nebula/planet add-ons are paid (small one-time cost).
5. **SheepIt (free community render farm)** works for Blender Cycles/EEVEE via points earned by rendering for others, but timing is unpredictable and your files render on strangers' machines.

## 1. Platform rules (the biggest risk)
- July 16, 2026 changes don't ban AI video ([TechRepublic](https://techrepublic.com/article/news-youtube-ai-video-monetization-rules)); three categories: generic/repetitive, unsatisfying/off-putting, AI personas on sensitive topics; repetitive explicitly includes "AI, computer-generated imagery or reusable templates" with little variation between uploads ([Tubefilter](https://www.tubefilter.com/2026/07/13/youtube-inauthentic-content-monetization-policy-update/)).
- Using AI for scripting/editing is fine; mass-producing near-identical videos is not ([Metricool](https://metricool.com/youtube-ai-content-rules-monetization/)); reviews weigh the channel's overall character; no defined threshold ([MediaCopilot](https://mediacopilot.ai/?p=9228)).
- Some sources say it's a 2025 rename of the "repetitious content" policy rather than new ([Logie](https://logie.ai/news/youtube-ai-slop-crackdown-2026-monetization/)). Primary YouTube help text not found in results.
**Implication (inference):** quality and variation beat volume; each format needs per-video originality (new story, new scenes, owner's voice/curation), and the channel should not look like one template repeated.

## 2. Rendering Blender cost-effectively
- Benchmarks: RTX 4090 ≈ 11,600–13,500 Blender score; RTX 5090 ≈ 15,000–17,800 ([RenderJuice](https://www.renderjuice.com/gpus/rtx-4090-vs-rtx-5090), [iRender](https://irendering.net/how-does-the-rtx-5090-perform-in-blender/)) — aggregator figures, versions differ. No L40S/L4 Open Data medians found (gap).
- Vast.ai RTX 4090: cheapest $0.13, median $0.47/h (12 Nov feed) ([Toolhalla](https://toolhalla.ai/tool/vastai)); other trackers $0.20–0.52 ([gpu-cost](https://gpu-cost.pages.dev/gpu/rtx-4090/)); RTX 5090 $0.40–1.02 ([Flopper](https://flopper.io/pricing/nvidia-geforce-rtx-5090), [Lium](https://lium.io/gpu/rtx-5090)). Dedicated render rental is far pricier (iRender 5090 ≈ $10.8/node-h) ([iRender](https://irendering.net/price/)).
- Modal official: L4 $0.80/h, L40S $1.95/h, A100 80GB $2.50/h, H100 $3.95/h ([Modal](https://modal.com/pricing)). Modal has no consumer RTX cards. (Inference, not sourced here: H100 lacks RT cores, so it is poor value for Cycles.)
- Headless EEVEE on Linux has a history of "unable to open a display"/black-frame problems; Cycles in an NVIDIA container is the well-trodden path ([Blender issue](https://projects.blender.org/blender/blender/issues/77339), [DevTalk](https://devtalk.blender.org/t/blender-2-8-unable-to-open-a-display-by-the-rendering-on-the-background-eevee/1436?page=2), [docker image](https://hub.docker.com/r/meihaiyi/blender)). The 2026 iRender claim about EEVEE-Next black frames is vendor marketing (unverified).
- SheepIt: free, points-based, Cycles/EEVEE/Workbench only, volunteer-dependent ([SheepIt FAQ](https://www.sheepit-renderfarm.com/faq), [guide](https://superrendersfarm.com/article/sheepit-render-farm-free-point-system-blender-users)).

## 3. Assets
- Kenney and Quaternius CC0 (no attribution); Quaternius ~1,100 models; Poly Pizza mostly CC-BY, per-model licence ([Cinevva](https://app.cinevva.com/guides/free-3d-model-sites)). Secondary sources — verify each pack; animation licences not separately confirmed.
- Space: Astro-Lab $0+ (planets, galaxy, starfield, 2D nebula) ([Gumroad](https://blenderlabs.gumroad.com/l/astro-lab)); Nebulapunk free demo ([Superhive](https://superhivemarket.com/products/nebulapunk)); Procedural Planet Generator, Nebula Forge, Instant Sky Gen are marketplace add-ons (prices not shown) ([Superhive](https://superhivemarket.com/products/procedural-planet-generator-for-blender/docs)).

## 4. AI video prices (mid/late 2026, vendor/aggregator)
- Veo 3.1 Lite $0.05/s 720p; Fast $0.10/s; standard $0.20–0.40/s ([AI Video Bootcamp](https://aivideobootcamp.com/blog/cheapest-ai-video-generators-2026/), [fal](https://fal.ai/learn/tools/ai-video-generators)).
- Kling 3 ≈ $0.07–0.11/s official ([invideo](https://invideo.io/blog/ai-video-model-pricing)); Wan 2.6 hosted $0.05–0.10/s ([HeyGen](https://www.heygen.com/blog/ai-video-cost-per-second)); LTX-2.3 Fast ≈ $0.04/s 1080p, open weights ([AI Video Bootcamp](https://aivideobootcamp.com/blog/cheapest-ai-video-generators-2026/)). Resellers often 2–3× vendor rates ([invideo](https://invideo.io/blog/ai-video-model-pricing)).
- Ours (measured): Wan drafts ≈ ₹1.9 per ~3 s clip (≈ $0.007/s); Wan hybrid 720p finals ≈ ₹25 per 5 s (≈ $0.057/s).

## Key takeaways
- Spend on **originality and hero shots**, not on volume.
- **Laptop EEVEE for stylised formats (≈ ₹0), cloud Cycles on RTX 4090/5090 for photoreal** (Vast becomes worth it because of Blender, not only SeedVR2).
- **A/B Veo 3.1 Lite/Fast for hero shots** before building anything bigger.
- Use CC0 assets; record licences per asset (already done for footage).

## Gaps
- No official Blender Open Data medians for L40S/L4; no primary YouTube policy text; Vast prices are marketplace snapshots; add-on prices not listed.

## Methodology
10 web searches (standard mode) on: Blender GPU benchmarks; SheepIt; YouTube inauthentic-content policy; AI video API prices; Blender space add-ons; Modal pricing; Vast 4090/5090 prices; CC0 low-poly assets; headless EEVEE/Cycles. Sources are mostly secondary or vendor pages; claims marked accordingly. Search snippets only (no full-page crawls; firecrawl/exa not configured).
