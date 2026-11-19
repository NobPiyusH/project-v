package com.projectv.dream;

import com.projectv.production.RedoStopRule;
import com.projectv.agents.Llm;
import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.dream.DreamTypes.DreamEstimate;
import com.projectv.dream.DreamTypes.DreamFinishClip;
import com.projectv.dream.DreamTypes.DreamFinishRequest;
import com.projectv.dream.DreamTypes.DreamFinishResult;
import com.projectv.dream.DreamTypes.DreamStep;
import com.projectv.dream.DreamTypes.DreamWorld;
import com.projectv.dream.DreamTypes.PlannedDreamShot;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.ClipJob;
import com.projectv.media.MediaTypes.ClipOutput;
import com.projectv.media.MediaTypes.ClipRequest;
import com.projectv.media.MediaTypes.ClipResult;
import com.projectv.media.MediaTypes.KeyframeJob;
import com.projectv.media.MediaTypes.KeyframeOutput;
import com.projectv.media.MediaTypes.KeyframeRequest;
import com.projectv.media.MediaTypes.KeyframeResult;
import com.projectv.routing.Router;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

/**
 * Dream Shorts (docs/plans/dream-shorts.md): the work behind each step. Reuses the studio's engines unchanged (Z-Image
 * and Wan on Modal through the existing media activities, the asset store, the router, the budget caps) and keeps its
 * own records in {@code dream_short} / {@code dream_shot}. Every asset gets provenance, every paid job a cost entry,
 * every provider choice a routing decision, all linked to the Short (rule 6).
 */
@Component
public class DreamService {

    private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(DreamService.class);

    static final String ZIMAGE = "modal_zimage";
    static final String WAN = "modal_wan";
    static final String LYRIA = "gemini_lyria";
    /** Provenance provider for pieces of the owner's music bed (cut free on the laptop). */
    static final String BED = "owner_music_bed";
    static final UUID LICENCE_OWNER_MUSIC = UUID.fromString("00000000-0000-0000-0000-00000000a012");
    /** Pieces of the bed Gemini compares for one Short, and what that listen is held to before the call (a few paise). */
    static final int BED_CANDIDATES = 6;
    static final BigDecimal LISTEN_ESTIMATE_INR = new BigDecimal("0.80"); // measured Rs 0.57-0.79 (19 Nov)
    /** Scout: Shorts watched per idea, and the estimate held before it runs (unmeasured until the first runs). */
    static final int SCOUT_VIDEOS = 6;
    /** Quality check: a ~360p copy watched by Gemini (estimate until measured). */
    static final BigDecimal CHECK_ESTIMATE_INR = new BigDecimal("0.50");
    static final BigDecimal SCOUT_ESTIMATE_INR = new BigDecimal("6"); // measured Rs 4.56 and Rs 5.96 (19 Nov)
    static final int FPS = 16;          // Wan's own frame rate; the finish makes it 24
    static final int MIN_FRAMES = 33;
    static final int MAX_FRAMES = 81;

    /**
     * A look: what the writer aims for, how the shots relate ({@code structure}), what every picture and clip prompt
     * ends with, how long a shot is (0 = the configured default) and how shots join (0 = hard cut).
     */
    record Look(String brief, String pictureStyle, String motionStyle, String structure, double shotSeconds, double dissolveSec,
                double speed, String handheld) {
        Look(String brief, String pictureStyle, String motionStyle) {
            this(brief, pictureStyle, motionStyle, ONE_WORLD, 0, 0.5, 1.0, "none");
        }
    }

    /**
     * How a person films (owner, 19 Nov 2026: "not like a human holding a camera ... more like an AI video"). Wan
     * glides like a drone or dolly; these words pull it towards a phone in someone's hand. Every realistic look's
     * camera prompt ends with it.
     */
    static final String HUMAN_CAMERA = "handheld first-person view at eye level, empty scene, nobody is ever visible (19 Nov: 'a person' and 'holding "
            + "a phone' in the prompt made Wan add a walking woman, then a man filming): small natural sway, nothing gliding; "
            + "no drone, no dolly, no crane, no zoom, no flying camera";

    /** Small text comes out garbled; signs stay big and face the camera. */
    static final String SIGNS = "at most one large sign facing the camera, no small labels, no small text";

    static final String ONE_WORLD = "Invent ONE coherent world and %d shots that move through it like a dream, each a single "
            + "continuous camera move.";

    /** Order shown on the new-Short form; the first is the default. */
    public static final List<String> LOOK_ORDER = List.of("lonely", "drive", "walkthrough", "dreamcore", "impossible");

    /**
     * The owner's rule for every Dream Short (19 Nov 2026): it may look different each time, but it must feel good or
     * nostalgic, have the best possible picture, and look real, never obviously AI.
     */
    static final String FEEL = "Feeling: warm, nostalgic, feel-good, calm wonder; beautiful and a little strange, never "
            + "scary, sad or gloomy. It must look REAL, like footage a person actually filmed: believable places, true-to-life "
            + "scale and light, small real-world imperfections; never like a 3D render, a game or an obvious AI image. "
            // 19 Nov test: the writer asked for drone, dolly and orbit moves and a Coca-Cola logo.
            + "Every camera move is something a person holding a phone can do: stand still, take a step, walk slowly, "
            + "turn their head, crouch; never a drone, dolly, crane, orbit, time-lapse or zoom. The person filming is never seen and "
            + "no people appear in any shot. No brand names or logos. In each shot's motion prompt describe only how the view "
            + "moves (e.g. 'handheld, slow step forward, slight sway'); never write the words phone, person, hand, someone or "
            + "holding (19 Nov: 'phone held at chest height' made Wan add a man filming).";

    /** Ends every picture prompt of the realistic looks: what makes a still read as a real photo, not an AI image. */
    static final String REAL_PHOTO = "real photograph, natural light falloff, real-world wear and small imperfections "
            + "(dust, scuffs, uneven grass, puddles), true-to-life scale, natural colours that are vivid but not over-processed HDR, "
            + "not a 3D render, not plastic, not airbrushed, not perfectly symmetrical";

    static final Map<String, Look> LOOKS = Map.of(
            // Owner's examples (18 Nov 2026): one ordinary object alone and glowing in a vast empty place, a different
            // place every shot, one feeling; sharp photo-real, vivid golden/blue-hour and neon light; slow push-in; hard cuts.
            "lonely", new Look(
                    "Lonely object (parallel-world core): each shot is ONE ordinary man-made object or small building "
                            + "(vending machine, phone booth, neon kiosk, gas station, traffic light, bus stop, arcade cabinet, "
                            + "empty classroom, pool hallway) standing alone in a vast empty real place (flower field, cornfield, "
                            + "open road, prairie, beach, a sea of clouds), at golden hour, sunset, blue hour or night, glowing "
                            + "(neon, warm windows, a street lamp, fluorescent light) with wet shiny reflections. Nostalgic, "
                            + "calm, lonely, beautiful. "
                            + "No people, no animals, no readable words except at most one short sign.",
                    "photograph, shot on a full-frame camera with a wide lens, photorealistic, tack sharp, vivid saturated "
                            + "colours, golden hour, blue hour or night sky with glowing clouds, neon or warm lamp glow, wet "
                            + "reflections, high detail, cinematic, vertical composition with the object in the lower middle, "
                            + "no people, " + SIGNS + ", " + REAL_PHOTO,
                    "first-person view: we stand still looking at the object, maybe one slow step closer; the camera barely moves; the "
                            + "scene itself is alive: clouds drift across the sky, neon and lamps flicker and pulse softly, grass "
                            + "and flowers sway in the breeze, water ripples; the object stays still; no cuts, no people; "
                            + HUMAN_CAMERA,
                    "Invent %d shots, each a DIFFERENT place and a DIFFERENT object, joined by one feeling (not one connected "
                            + "world). Vary the object, the landscape and the time of light from shot to shot.",
                    3.5, 0, 0.7, "stand"),
            // Owner's example (18 Nov 2026): first-person walk through ordinary interiors where one impossible thing
            // happens (kitchen opening onto grass, hallway flooding into a sea, stairs melting into rainbow paint).
            "walkthrough", new Look(
                    "Walk-through (liminal fever dream): first-person walk through ordinary empty interiors (kitchen, "
                            + "hallway, stairwell, pool room, school corridor, hotel lobby) in pastel pink, mint and sky blue, "
                            + "where ONE impossible thing happens in each (a doorway opening onto a meadow, the floor flooding "
                            + "into a sea, stairs melting into rainbow paint, a night sky behind an exit door). Bright, glossy, "
                            + "strange but calm. No people, no text.",
                    "phone camera photo, first-person point of view at eye level, wide lens, photorealistic, sharp, bright "
                            + "fluorescent and daylight, pastel pink mint and sky-blue colours, glossy wet tiled floors with "
                            + "reflections, liminal space, no people, " + SIGNS + ", " + REAL_PHOTO,
                    "first-person view: we walk slowly forward at a relaxed pace, gentle up-and-down step bob, glancing a little left "
                            + "and right; water ripples and lights shimmer; continuous, no cuts, no people in view; " + HUMAN_CAMERA,
                    "Invent %d rooms or corridors walked through one after another, each a different ordinary place with "
                            + "its own impossible thing; the walk keeps going forward from shot to shot.",
                    5.0, 0.35, 0.75, "walk"),
            // Owner's examples (19 Nov 2026): dashcam on a highway that curves up into space; side window passing a
            // lonely house on a snowy field at blue hour. Real road details make the impossible sight believable.
            "drive", new Look(
                    "Drive-by: filmed from inside a car, either through the windscreen (dashcam) or out of the side window "
                            + "(the side mirror or window frame in view), on an ordinary road where ONE impossible, beautiful "
                            + "sight goes by (the highway curving up off the Earth into space, a lonely lit house on a frozen sea "
                            + "at sunset, a lighthouse standing on a cloud). Real road details (lane markings, guard rails, "
                            + "roadside poles, a few ordinary cars) make it believable. No readable words.",
                    "photo taken from inside a car, dashcam view through the windscreen or side-window view with the side "
                            + "mirror and window frame in frame, phone or dashcam lens, photorealistic, sharp, natural daylight "
                            + "or golden or blue hour, vivid sky, no people visible, " + SIGNS + ", " + REAL_PHOTO,
                    "filmed on a phone held by a passenger in a car driving at normal speed: light road vibration and small "
                            + "bumps; the landscape passes with natural parallax, lane markings and roadside poles move by, other "
                            + "cars keep driving; continuous, no cuts; no drone, no flying camera",
                    "Invent %d shots, each a different drive with its own impossible sight; the camera is inside a car in "
                            + "every shot (mix windscreen and side-window views).",
                    5.0, 0, 0.85, "car"),
            "dreamcore", new Look(
                    "Dreamcore / liminal fever dream: one soft pastel world (pink, mint, cream, sky blue), surreal oversized "
                            + "or out-of-place objects, water, tiles, clouds, empty calm places, nostalgic and slightly eerie. "
                            + "No people, no text, no brands.",
                    "dreamcore, liminal space, pastel pink and mint palette, soft hazy daylight, 35mm film photo, slight "
                            + "lens blur, nostalgic, surreal, calm, empty, no people, no text",
                    "slow continuous camera move, like walking forward in a dream; gentle, smooth, no cuts, no people"),
            "impossible", new Look(
                    "Impossible place: one photo-real scene where two worlds that never meet are joined (e.g. a roadside "
                            + "diner on the Moon), believable light and scale, wide views, awe. No people close up, no text, no brands.",
                    "photorealistic, cinematic wide shot, believable lighting and scale, rich detail, no text",
                    "slow cinematic camera move (push-in, drift or crane), subtle life in the scene, no cuts"));

    private final JdbcClient jdbc;
    private final ObjectMapper json;
    private final Llm llm;
    private final AssetStore assets;
    private final Pricing pricing;
    private final BudgetGuard budget;
    private final Router router;
    private final com.projectv.video.ClipProgress clipProgress;
    private final String zimageApp;
    private final String zimageClass;
    private final String wanApp;
    private final String wanClass;
    private final String publicUrl;
    private final BigDecimal autoApproveInr;
    private final double shotSeconds;
    private final String musicDir;
    private final String musicModel;
    private final String musicBed;
    private final String listenModel;
    private final DreamEvidence evidence;
    private final String briefModel;
    private final boolean qualityCheck;

    public DreamService(JdbcClient jdbc, ObjectMapper json, Llm llm, AssetStore assets, Pricing pricing, BudgetGuard budget,
                        Router router, com.projectv.video.ClipProgress clipProgress,
                        @Value("${projectv.modal.zimage-app:projectv-zimage}") String zimageApp,
                        @Value("${projectv.modal.zimage-class:ZImage}") String zimageClass,
                        @Value("${projectv.video.modal-app:projectv-wan}") String wanApp,
                        @Value("${projectv.video.modal-class:Wan}") String wanClass,
                        @Value("${projectv.public-url:http://127.0.0.1:8080}") String publicUrl,
                        @Value("${projectv.dream.auto-approve-inr:0}") BigDecimal autoApproveInr,
                        @Value("${projectv.dream.shot-seconds:3.5}") double shotSeconds,
                        @Value("${projectv.dream.music-dir:}") String musicDir,
                        @Value("${projectv.models.music:lyria-3.5}") String musicModel,
                        @Value("${projectv.dream.music-bed:}") String musicBed,
                        @Value("${projectv.models.vision:gemini-3.6-flash}") String listenModel,
                        @Value("${projectv.models.story:claude:claude-sonnet-5-5}") String briefModel,
                        @Value("${projectv.dream.quality-check:true}") boolean qualityCheck) {
        this.jdbc = jdbc;
        this.json = json;
        this.llm = llm;
        this.assets = assets;
        this.pricing = pricing;
        this.budget = budget;
        this.router = router;
        this.clipProgress = clipProgress;
        this.zimageApp = zimageApp;
        this.zimageClass = zimageClass;
        this.wanApp = wanApp;
        this.wanClass = wanClass;
        this.publicUrl = publicUrl;
        this.autoApproveInr = autoApproveInr;
        this.shotSeconds = shotSeconds;
        this.musicDir = musicDir;
        this.musicModel = musicModel;
        this.musicBed = musicBed;
        this.listenModel = listenModel;
        this.evidence = new DreamEvidence(jdbc, json);
        this.briefModel = briefModel;
        this.qualityCheck = qualityCheck;
    }

    public DreamEvidence evidence() {
        return evidence;
    }

    static Look look(String id) {
        return LOOKS.getOrDefault(id == null ? LOOK_ORDER.getFirst() : id, LOOKS.get(LOOK_ORDER.getFirst()));
    }

    /** 4n+1 frames covering the shot at 16 fps, within Wan's ~2-5 s range. */
    static int frames(double seconds) {
        int n = (int) Math.ceil(seconds * FPS / 4.0) * 4 + 1;
        return Math.max(MIN_FRAMES, Math.min(MAX_FRAMES, n));
    }

    /** Vertical clip size for a quality setting: 480p = 480x832 (Wan fast's native), 720p = 720x1280. */
    static int[] clipSize(String quality) {
        return "720p".equals(quality) ? new int[]{720, 1280} : new int[]{480, 832};
    }

    // ------------------------------------------------------------------------------------------- WRITE

    private DreamLessons lessons; // optional so hand-built services in tests work without it

    @org.springframework.beans.factory.annotation.Autowired(required = false)
    void setLessons(DreamLessons lessons) {
        this.lessons = lessons;
    }

    /** The owner's active lessons for a prompt (learning loop, phase 4), or "". */
    private String lessons(String target) {
        return lessons == null ? "" : lessons.forPrompt(target);
    }

    @Transactional
    public String writeShots(UUID id, String notes) {
        Map<String, Object> d = dream(id);
        Look look = look((String) d.get("look"));
        int count = ((Number) d.get("shot_count")).intValue();
        Scouted scouted = scouted(d);
        // The owner's chosen Scout angle replaces the look's shot structure (19 Nov: as a user-message note it lost
        // to "a different place every shot" twice). The look still sets the style.
        String structure = scouted != null && scouted.chosen() != null
                ? ("Invent %d shots that are consecutive moments of ONE idea the owner chose, in the same place, flowing "
                + "into each other: %s - %s").formatted(count, scouted.chosen().title(), scouted.chosen().idea())
                : look.structure().formatted(count);
        String system = """
                You are the writer of a short, wordless, music-led vertical video (YouTube Shorts / Reels) for a channel
                about dreams and impossible places. %s Every shot must be a fresh, specific image (not generic).
                %s
                Look: %s
                Return JSON: {"title": short title, "world": one paragraph describing the shared feeling and world (materials,
                objects, scale, mood), "palette": the exact colour palette, "light": the light and atmosphere,
                "hero": the main object that appears in several shots, described exactly once so it can be drawn the same
                every time (shape, size, materials, colours, every part and where it sits; e.g. "green-painted steel bus
                shelter, rusty corrugated tin roof sloping back, three glass side panels, wooden slat bench inside, one
                round lamp under the roof"; empty if no object recurs), never mention the hero's look differently in a scene,
                "shots": [{"scene": what this shot shows (one or two sentences, concrete), "motion": the camera move and
                the small motion inside the scene}]} with exactly %d shots.
                """.formatted(structure, FEEL, look.brief(), count) + lessons("WRITER");
        String user = "Idea: " + d.get("idea") + (notes == null || notes.isBlank() ? "" : "\nOwner's notes for this rewrite: " + notes)
                + (scouted == null ? "" : scouted.forWriter());
        java.time.Instant since = java.time.Instant.now();
        DreamWorld w = llm.call(null, "dream.write", system, user, DreamWorld.class);
        if (w == null || w.shots() == null || w.shots().isEmpty()) {
            throw new IllegalStateException("The writer returned no shots");
        }
        String rewrite = scouted == null ? null : tooSimilar(id, w, scouted);
        if (rewrite != null) { // one rewrite: never copy a Short that already exists
            DreamWorld again = llm.call(null, "dream.write", system, user + "\nRewrite: " + rewrite, DreamWorld.class);
            if (again != null && again.shots() != null && !again.shots().isEmpty()) {
                w = again;
            }
        }
        linkLlmCosts(id, "dream.", since);
        List<PlannedDreamShot> shots = w.shots().size() > count ? w.shots().subList(0, count) : w.shots();
        jdbc.sql("DELETE FROM dream_shot WHERE dream_short_id = :d").param("d", id).update();
        int ord = 0;
        for (PlannedDreamShot s : shots) {
            jdbc.sql("""
                            INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt)
                            VALUES (:id, :d, :o, :scene, :pp, :mp)
                            """)
                    .param("id", UUID.randomUUID()).param("d", id).param("o", ord++).param("scene", s.scene())
                    .param("pp", picturePrompt(w, s, look)).param("mp", motionPrompt(s, look)).update();
        }
        jdbc.sql("""
                        UPDATE dream_short SET title = :t, world_json = CAST(:w AS jsonb), final_asset_id = NULL,
                               music_asset_id = NULL, updated_at = now() WHERE id = :d
                        """)
                .param("t", w.title()).param("w", json.writeValueAsString(w)).param("d", id).update();
        return "\"%s\": %d shots in one world (%s).".formatted(w.title(), shots.size(), Objects.toString(w.palette(), "").strip());
    }

    // ------------------------------------------------------------------------------------------- SCOUT

    /** What the Scout found and the angle the owner picked, as the writer will read it. */
    record Scouted(DreamTypes.ScoutBrief brief, List<DreamTypes.ScoutVideo> videos, int angle) {
        /** The angle the owner picked (1-based; out of range falls back to the nearest), or null when there are none. */
        DreamTypes.ScoutAngle chosen() {
            return brief.angles() == null || brief.angles().isEmpty() ? null
                    : brief.angles().get(Math.min(Math.max(angle, 1), brief.angles().size()) - 1);
        }

        String forWriter() {
            StringBuilder s = new StringBuilder("\n\nWhat already exists (make this Short clearly different):");
            if (brief.cliches() != null && !brief.cliches().isEmpty()) {
                s.append("\nAvoid these clichés: ").append(String.join("; ", brief.cliches()));
            }
            if (brief.angles() != null && !brief.angles().isEmpty()) {
                DreamTypes.ScoutAngle a = brief.angles().get(Math.min(Math.max(angle, 1), brief.angles().size()) - 1);
                // The owner's chosen angle shapes the WHOLE Short; where it conflicts with the look's usual structure
                // (e.g. Lonely object's "a different place every shot"), the angle wins (19 Nov test).
                s.append("\nThe owner chose this angle; build EVERY shot around it, as consecutive moments of it (it overrides ")
                        .append("the look's usual shot structure where they conflict): ").append(a.title()).append(" - ").append(a.idea());
            }
            List<String> seen = videos.stream().map(DreamTypes.ScoutVideo::shows).filter(Objects::nonNull).limit(8).toList();
            if (!seen.isEmpty()) {
                s.append("\nDo not copy any of these existing Shorts: ").append(String.join(" | ", seen));
            }
            return s.toString();
        }
    }

    Scouted scouted(Map<String, Object> d) {
        if (d.get("scout_json") == null) {
            return null;
        }
        Map<String, Object> m = json.readValue(d.get("scout_json").toString(), new tools.jackson.core.type.TypeReference<Map<String, Object>>() {
        });
        DreamTypes.ScoutBrief brief = json.convertValue(m.get("brief"), DreamTypes.ScoutBrief.class);
        List<DreamTypes.ScoutVideo> videos = json.convertValue(m.get("videos"),
                new tools.jackson.core.type.TypeReference<List<DreamTypes.ScoutVideo>>() {
                });
        return brief == null ? null : new Scouted(brief, videos == null ? List.of() : videos, ((Number) d.get("scout_angle")).intValue());
    }

    public boolean scoutWanted(UUID id) {
        return Boolean.TRUE.equals(dream(id).get("scout"));
    }

    public DreamTypes.DreamScoutRequest scoutRequest(UUID id, String notes) {
        Map<String, Object> d = dream(id);
        budget.require(SCOUT_ESTIMATE_INR, "Dream Short Scout (watch similar Shorts)");
        String query = Objects.toString(d.get("idea"), "") + (notes == null || notes.isBlank() ? "" : " " + notes.strip());
        return new DreamTypes.DreamScoutRequest(storageId(id), query.strip(), listenModel, SCOUT_VIDEOS,
                look((String) d.get("look")).brief());
    }

    @Transactional
    public String saveScout(UUID id, DreamTypes.DreamScoutRequest req, DreamTypes.DreamScoutResult r) {
        double paid = 0;
        if (r.usage() != null) {
            for (var u : r.usage()) {
                BigDecimal inr = pricing.textInr(u);
                paid += inr.doubleValue();
                cost(id, BudgetGuard.GEMINI, inr, u.totalTokens(), "token", false, "Dream Short Scout: Gemini watched a Short");
            }
        }
        if (r.searches() > 0 && "google".equals(r.source())) {
            BigDecimal inr = pricing.groundingInr(r.searches());
            paid += inr.doubleValue();
            cost(id, BudgetGuard.GEMINI, inr, r.searches(), "query", false, "Dream Short Scout: Google Search");
        }
        UUID decision = router.recordExternal(null, "DREAM_SCOUT", "video.watch", BudgetGuard.GEMINI,
                "found %d similar Shorts via %s; Gemini watches them from their links (only model that takes video)"
                        .formatted(r.videos() == null ? 0 : r.videos().size(), r.source()), List.of(Map.of("provider", BudgetGuard.GEMINI)));
        tag("routing_decision", decision, id);
        List<DreamTypes.ScoutVideo> seen = r.videos() == null ? List.of()
                : r.videos().stream().filter(v -> v.error() == null && v.shows() != null).toList();
        if (seen.isEmpty()) {
            return noAngles(id, paid, "found no similar Shorts" + (r.error() == null ? "" : " (" + r.error() + ")"));
        }
        Map<String, Object> d = dream(id);
        java.time.Instant since = java.time.Instant.now();
        String system = """
                You advise a private studio that makes short, wordless, music-led vertical videos (YouTube Shorts) that
                feel warm, nostalgic and real. Below are Shorts that already exist for the owner's idea, as a viewer saw
                them. Find what most of them do (the clichés to avoid), what none of them do (the gaps), and propose 3
                concrete angles that would make the owner's Short clearly different yet still feel-good and believable
                (a new object or place, a new camera idea, a new time of light, a twist at the end...). Never suggest
                copying any of them. Return JSON: {"cliches": [short phrases], "gaps": [short phrases],
                "angles": [{"title": 2-5 words, "idea": one or two concrete sentences, "why": why it stands out}]}.
                """;
        StringBuilder user = new StringBuilder("Owner's idea: " + d.get("idea") + "\nLook: " + look((String) d.get("look")).brief() + "\n");
        int n = 1;
        for (var v : seen) {
            user.append("\nShort ").append(n++).append(v.views() == null ? "" : " (" + v.views() + " views)").append(": ")
                    .append(v.shows()).append(" | look: ").append(v.look()).append(" | camera: ").append(v.camera())
                    .append(" | shots: ").append(v.shots()).append(" | music: ").append(v.music()).append(" | hook: ")
                    .append(v.hook()).append(" | why it works: ").append(v.works());
        }
        DreamTypes.ScoutBrief brief = llm.call(null, "dream.scout", system, user.toString(), DreamTypes.ScoutBrief.class, briefModel);
        linkLlmCosts(id, "dream.scout", since);
        if (brief == null || brief.angles() == null || brief.angles().isEmpty()) {
            return noAngles(id, paid + llmCostsSince(id, "dream.scout", since), "found Shorts but no useful angle");
        }
        jdbc.sql("UPDATE dream_short SET scout_json = CAST(:j AS jsonb), scout_angle = 1, updated_at = now() WHERE id = :d")
                .param("j", json.writeValueAsString(Map.of("videos", seen, "brief", brief, "source", r.source())))
                .param("d", id).update();
        evidence.costCheck(id, "SCOUT", SCOUT_ESTIMATE_INR.doubleValue(), paid + llmCostsSince(id, "dream.scout", since));
        long top = seen.stream().map(DreamTypes.ScoutVideo::views).filter(Objects::nonNull).mapToLong(Long::longValue).max().orElse(0);
        return "Watched %d similar Shorts%s. Most do: %s. Angle 1: %s (pick another before approving).".formatted(seen.size(),
                top > 0 ? " (top one %,d views)".formatted(top) : "",
                brief.cliches() == null || brief.cliches().isEmpty() ? "-" : brief.cliches().getFirst(),
                brief.angles().getFirst().title());
    }

    /**
     * Nothing similar exists (owner, 19 Nov 2026): carry on from the idea and the look alone instead of stopping, so Auto
     * mode and the autopilot never wait on an unusual idea. The writer then uses the look's own shot structure.
     */
    private String noAngles(UUID id, double paid, String why) {
        evidence.costCheck(id, "SCOUT", SCOUT_ESTIMATE_INR.doubleValue(), paid);
        evidence.event(id, "SCOUT", "scout", null, "Scout " + why + "; carrying on from the idea alone", null);
        return "Scout " + why + ", so the writer works from your idea and the look alone (no angle).";
    }

    /** Our shots too close to an existing Short? Returns rewrite notes, or null. Cheap Gemini text call. */
    String tooSimilar(UUID id, DreamWorld w, Scouted scouted) {
        List<String> theirs = scouted.videos().stream().map(DreamTypes.ScoutVideo::shows).filter(Objects::nonNull).toList();
        if (theirs.isEmpty()) {
            return null;
        }
        StringBuilder user = new StringBuilder("Our shots:");
        for (int i = 0; i < w.shots().size(); i++) {
            user.append("\n").append(i + 1).append(". ").append(w.shots().get(i).scene());
        }
        user.append("\n\nExisting Shorts:");
        for (int i = 0; i < theirs.size(); i++) {
            user.append("\n").append((char) ('A' + i)).append(". ").append(theirs.get(i));
        }
        DreamTypes.Similar s = llm.call(null, "dream.similar", """
                Compare our planned shots with Shorts that already exist. A shot is too similar only when it is nearly the
                same scene (same object in the same kind of place, seen the same way). Sharing a mood or a colour is fine.
                Return JSON {"similar": [{"shot": our shot number, "like": the letter it copies, "why": a few words}]};
                an empty list when nothing is too close.
                """, user.toString(), DreamTypes.Similar.class);
        if (s == null || s.similar() == null || s.similar().isEmpty()) {
            return null;
        }
        String notes = s.similar().stream().map(x -> "shot %d is too close to an existing Short (%s): make it a different scene"
                .formatted(x.shot(), x.why())).collect(java.util.stream.Collectors.joining("; "));
        evidence.event(id, "WRITE", "similar", null, notes, null);
        return notes;
    }

    /** LLM costs are recorded without a Short; link this Short's calls (by purpose, since a moment) to it. */
    private void linkLlmCosts(UUID id, String purposePrefix, java.time.Instant since) {
        jdbc.sql("""
                        UPDATE cost_entry SET dream_short_id = :d
                        WHERE dream_short_id IS NULL AND video_id IS NULL AND note LIKE :p AND ts >= :since
                        """).param("d", id).param("p", purposePrefix + "%").param("since", java.sql.Timestamp.from(since)).update();
    }

    private double llmCostsSince(UUID id, String purposePrefix, java.time.Instant since) {
        return jdbc.sql("""
                        SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry WHERE dream_short_id = :d AND note LIKE :p AND ts >= :since
                        """).param("d", id).param("p", purposePrefix + "%").param("since", java.sql.Timestamp.from(since))
                .query(Double.class).single();
    }

    static String picturePrompt(DreamWorld w, PlannedDreamShot s, Look look) {
        String hero = Objects.toString(w.hero(), "").strip();
        return String.join(" ", s.scene().strip(), hero.isEmpty() ? "" : "The same " + hero + " in every shot, exactly as described.",
                "World:", Objects.toString(w.world(), "").strip(),
                "Palette:", Objects.toString(w.palette(), "").strip(), "Light:", Objects.toString(w.light(), "").strip(),
                "Style:", look.pictureStyle()).replaceAll("\\s+", " ");
    }

    static String motionPrompt(PlannedDreamShot s, Look look) {
        return (Objects.toString(s.motion(), "").strip() + ". " + s.scene().strip() + ". " + look.motionStyle()).replaceAll("\\s+", " ");
    }

    // ------------------------------------------------------------------------------------------- PICTURES

    @Transactional
    public KeyframeRequest pictureRequest(UUID id, List<String> only, String notes) {
        List<KeyframeJob> jobs = new ArrayList<>();
        Set<String> wanted = only == null ? null : new HashSet<>(only);
        for (Map<String, Object> s : shots(id)) {
            String shotId = s.get("id").toString();
            if (wanted == null ? s.get("picture_asset_id") != null : !wanted.contains(shotId)) {
                continue;
            }
            String prompt = (String) s.get("picture_prompt") + lessons("PICTURE");
            if (notes != null && !notes.isBlank()) {
                prompt = prompt + " Owner's notes: " + notes.strip();
            }
            int attempt = ((Number) s.get("picture_takes")).intValue() + 1;
            // One seed for the whole Short (owner, 19 Nov: the shelter changed shape every shot); a redo moves it on.
            jobs.add(new KeyframeJob(shotId, attempt, ZIMAGE, prompt, 768, 1344, "9:16", "1K", null,
                    Math.abs((long) id.hashCode()) + attempt - 1));
        }
        if (!jobs.isEmpty()) {
            BigDecimal inr = pricing.zimageInr(jobs.size());
            route(id, "DREAM_PICTURES", "image.dream", ZIMAGE, inr);
            budget.requireGpu(inr, jobs.size() + " Dream Short pictures");
        }
        return new KeyframeRequest(storageId(id), null, zimageApp, zimageClass, jobs);
    }

    @Transactional
    public String savePictures(UUID id, KeyframeRequest req, KeyframeResult r) {
        Map<String, KeyframeJob> jobs = new java.util.HashMap<>();
        req.jobs().forEach(j -> jobs.put(j.shotId(), j));
        int ok = 0;
        List<String> errors = new ArrayList<>();
        for (KeyframeOutput o : r.results()) {
            KeyframeJob job = jobs.get(o.shotId());
            if (job == null) {
                continue;
            }
            UUID shotId = UUID.fromString(o.shotId());
            if (o.uri() == null) {
                errors.add(o.error());
                jdbc.sql("UPDATE dream_shot SET error = :e, updated_at = now() WHERE id = :s").param("e", o.error()).param("s", shotId).update();
                continue;
            }
            UUID asset = assets.create(new NewAsset(null, "image", o.uri(), "image/png", job.width(), job.height(), null,
                    ZIMAGE, Objects.toString(o.model(), "Tongyi-MAI/Z-Image-Turbo"), job.prompt(), o.seed(),
                    AssetStore.LICENCE_ZIMAGE, true));
            jdbc.sql("UPDATE asset SET dream_short_id = :d WHERE id = :a").param("d", id).param("a", asset).update();
            // A new picture makes the old clip stale: it must be made again from this picture.
            jdbc.sql("""
                            UPDATE dream_shot SET picture_asset_id = :a, picture_takes = picture_takes + 1, clip_asset_id = NULL,
                                   error = NULL, updated_at = now() WHERE id = :s
                            """).param("a", asset).param("s", shotId).update();
            ok++;
        }
        if (r.modalGpuSeconds() > 0) {
            cost(id, "modal_l40s", pricing.modalGpuInr("L40S", r.modalGpuSeconds()), r.modalGpuSeconds(), "gpu_seconds", true,
                    "Dream Short pictures (Z-Image, %d)".formatted(ok));
        }
        router.reportBatch(ZIMAGE, ok, errors.size(), errors.isEmpty() ? null : errors.getFirst());
        if (ok == 0) {
            throw new IllegalStateException("No pictures were made: " + String.join(" | ", errors.stream().filter(Objects::nonNull).toList()));
        }
        staleFinal(id);
        return "%d/%d pictures made (Z-Image)%s.".formatted(ok, req.jobs().size(),
                errors.isEmpty() ? "" : "; " + errors.size() + " failed: " + errors.getFirst());
    }

    // ------------------------------------------------------------------------------------------- CLIPS

    public DreamEstimate clipEstimate(UUID id, List<String> only) {
        List<ClipJob> jobs = clipJobs(id, only);
        double gpu = gpuSeconds(jobs, (String) dream(id).get("quality"));
        BigDecimal inr = pricing.modalGpuInr("H100", gpu).setScale(2, RoundingMode.HALF_UP);
        boolean ask = !jobs.isEmpty() && inr.compareTo(autoApproveInr) > 0;
        String summary = jobs.isEmpty() ? "No shots need a clip."
                : "%d clips on Modal Wan (fast, %s): about ₹%s of GPU and %.0f min. ₹%s of this month's GPU cap left."
                .formatted(jobs.size(), dream(id).get("quality"), inr.setScale(0, RoundingMode.HALF_UP), Math.ceil(gpu / 60),
                        budget.gpuRemaining().max(BigDecimal.ZERO).setScale(0, RoundingMode.HALF_UP));
        return new DreamEstimate(DreamStep.CLIPS, inr, gpu / 60, ask, summary);
    }

    @Transactional
    public ClipRequest clipRequest(UUID id, List<String> only) {
        List<ClipJob> jobs = clipJobs(id, only);
        if (jobs.isEmpty()) {
            throw new IllegalStateException("No shots with a picture need a clip");
        }
        double gpu = gpuSeconds(jobs, (String) dream(id).get("quality"));
        BigDecimal inr = pricing.modalGpuInr("H100", gpu);
        route(id, "DREAM_CLIPS", "video.i2v.dream", WAN, inr);
        budget.requireGpu(inr, jobs.size() + " Dream Short clips");
        // One GPU, clips in turn: the ~2 min start-up + model load is paid once for the whole Short. When the AI
        // Producer runs this Short by itself, the GPU stays warm a little after each batch so its redo skips a start-up.
        return new ClipRequest(storageId(id), wanApp, wanClass, publicUrl, jobs, 1, gpu, inr.setScale(2, RoundingMode.HALF_UP),
                "wan", null, null, holdSeconds(id));
    }

    /** Warm-redo hold for this Short's clip batches: only when the AI Producer decides on it by itself (autopilot). */
    int holdSeconds(UUID id) {
        boolean producerDecides = jdbc.sql("SELECT producer_decides FROM dream_short WHERE id = :d").param("d", id)
                .query(Boolean.class).optional().orElse(false);
        return producerDecides ? pricing.wanRedoHoldSeconds() : 0;
    }

    List<ClipJob> clipJobs(UUID id, List<String> only) {
        Map<String, Object> d = dream(id);
        int[] size = clipSize((String) d.get("quality"));
        int frames = frames(secondsOf(look((String) d.get("look"))));
        Set<String> wanted = only == null ? null : new HashSet<>(only);
        List<ClipJob> jobs = new ArrayList<>();
        // The Producer's stop-rule fixes for this Short's clips (a cause that came back twice): firm prompt + negative.
        List<RedoStopRule.Cause> fixes = jdbc.sql("""
                        SELECT DISTINCT stop_cause FROM producer_decision
                        WHERE dream_short_id = :d AND stage = 'DREAM_CLIPS' AND stop_rule = 'prompt_fix'
                        """).param("d", id).query(String.class).list().stream().map(RedoStopRule::causeByKey)
                .filter(java.util.Objects::nonNull).toList();
        String fixPrompt = fixes.stream().map(c -> " " + c.promptFix()).collect(java.util.stream.Collectors.joining());
        String negative = fixes.isEmpty() ? null
                : fixes.stream().map(RedoStopRule.Cause::negative).collect(java.util.stream.Collectors.joining(", "));
        for (Map<String, Object> s : shots(id)) {
            String shotId = s.get("id").toString();
            if (s.get("picture_uri") == null) {
                continue;
            }
            if (wanted == null ? s.get("clip_asset_id") != null : !wanted.contains(shotId)) {
                continue;
            }
            int take = ((Number) s.get("clip_takes")).intValue() + 1;
            jobs.add(new ClipJob(shotId, ((Number) s.get("ord")).intValue() + 1, take, "dream", "fast",
                    (String) s.get("picture_uri"), fixPrompt.isEmpty() ? (String) s.get("motion_prompt") : s.get("motion_prompt") + fixPrompt, size[0], size[1], frames,
                    Math.abs((long) shotId.hashCode()) + take, negative));
        }
        return jobs;
    }

    /** Wan fast render time (scaled by pixels and frames from the measured 480p draft) + one container start-up. */
    double gpuSeconds(List<ClipJob> jobs, String quality) {
        if (jobs.isEmpty()) {
            return 0;
        }
        // Measured timings (recalibrated after every batch) win over the config's numbers. 720p is its own number:
        // render time grows faster than the pixel count (19 Nov 2026: ~2x the old pixel-scaled guess).
        String kind = clipKind(quality);
        evidence.recalibrate(kind); // cheap; keeps the estimate on the latest real timings
        double per81 = evidence.calibrated(kind + ".per81")
                .orElse(pricing.wanSecondsPer81Frames("720p".equals(quality) ? "draft720" : "draft"));
        double firstExtra = evidence.calibrated(kind + ".first_extra").orElse(pricing.wanFirstClipExtraSeconds());
        return jobs.stream().mapToDouble(j -> per81 * j.numFrames() / MAX_FRAMES).sum()
                + firstExtra + pricing.gpuContainerStartSeconds();
    }

    static String clipKind(String quality) {
        return "wan.fast." + ("720p".equals(quality) ? "720p" : "480p");
    }

    @Transactional
    public String saveClips(UUID id, ClipRequest req, ClipResult r) {
        Map<String, ClipJob> jobs = new java.util.HashMap<>();
        req.jobs().forEach(j -> jobs.put(j.shotId() + ":" + j.take(), j));
        int ok = 0;
        int stopped = 0;
        double gpu = 0;
        BigDecimal spent = BigDecimal.ZERO;
        String kind = clipKind((String) dream(id).get("quality"));
        boolean first = r.startUps() > 0;
        List<String> errors = new ArrayList<>();
        for (ClipOutput o : r.clips()) {
            ClipJob job = jobs.get(o.shotId() + ":" + o.take());
            if (job == null) {
                continue;
            }
            gpu += o.gpuSeconds();
            if (o.gpuSeconds() > 0) {
                evidence.gpuTiming(id, kind, o.frames() > 0 ? o.frames() : job.numFrames(), o.gpuSeconds(), first);
                first = false;
                spent = spent.add(pricing.modalGpuInr("H100", o.gpuSeconds()));
            }
            if (o.gpuSeconds() > 0) {
                cost(id, "modal_h100", pricing.modalGpuInr("H100", o.gpuSeconds()), o.gpuSeconds(), "gpu_seconds", true,
                        "Dream Short clip, shot %d take %d (Wan fast)".formatted(job.shotNumber(), job.take()));
            }
            UUID shotId = UUID.fromString(o.shotId());
            if (o.uri() == null) {
                if ("stopped".equals(o.error())) {
                    stopped++;
                } else {
                    errors.add("shot " + job.shotNumber() + ": " + o.error());
                    jdbc.sql("UPDATE dream_shot SET error = :e, updated_at = now() WHERE id = :s").param("e", o.error()).param("s", shotId).update();
                }
                continue;
            }
            int fps = o.fps() > 0 ? o.fps() : FPS;
            UUID asset = assets.create(new NewAsset(null, "video", o.uri(), "video/mp4", job.width(), job.height(),
                    o.frames() > 0 ? (double) o.frames() / fps : null, WAN, "wan2.2-i2v-a14b " + o.mode(), job.prompt(),
                    job.seed(), AssetStore.LICENCE_WAN, true));
            jdbc.sql("UPDATE asset SET dream_short_id = :d, fps = :f WHERE id = :a").param("d", id).param("f", fps).param("a", asset).update();
            jdbc.sql("UPDATE dream_shot SET clip_asset_id = :a, clip_takes = clip_takes + 1, error = NULL, updated_at = now() WHERE id = :s")
                    .param("a", asset).param("s", shotId).update();
            ok++;
        }
        if (r.startUps() > 0) { // observed cold starts: a redo on a warm GPU pays none
            double start = r.startUps() * pricing.gpuContainerStartSeconds();
            cost(id, "modal_h100", pricing.modalGpuInr("H100", start), start, "gpu_seconds", true,
                    "Dream Short: Wan GPU start-up + model load (estimated)");
            spent = spent.add(pricing.modalGpuInr("H100", start));
        }
        if (req.estimateInr() != null && gpu > 0) {
            evidence.costCheck(id, "CLIPS", req.estimateInr().doubleValue(), spent.doubleValue());
        }
        evidence.recalibrate(kind).forEach(c -> log.info("Clip estimate recalibrated: {}", c));
        router.reportBatch(WAN, ok, errors.size(), errors.isEmpty() ? null : errors.getFirst());
        if (ok == 0 && stopped == 0) {
            throw new IllegalStateException("No clips were made: " + String.join(" | ", errors));
        }
        staleFinal(id);
        return "%d/%d clips made (Wan fast, %s)%s%s%s.".formatted(ok, req.jobs().size(), dream(id).get("quality"),
                gpu > 0 ? " in %.0f s of GPU time".formatted(gpu) : "",
                stopped > 0 ? "; stopped before " + stopped + " finished" : "",
                errors.isEmpty() ? "" : "; " + errors.size() + " failed: " + errors.getFirst());
    }

    // ------------------------------------------------------------------------------------------- MUSIC

    /** Length the finished Short will have: the shots minus the dissolves between them, plus a second of tail. */
    double shortSeconds(int shots, Look look) {
        double clip = (double) frames(secondsOf(look)) / FPS / look.speed();
        return shots * clip - Math.max(0, shots - 1) * Math.min(look.dissolveSec(), clip / 3) + 1;
    }

    /** A look's own shot length, else the configured default. */
    double secondsOf(Look look) {
        return look.shotSeconds() > 0 ? look.shotSeconds() : shotSeconds;
    }

    @Transactional
    public com.projectv.media.MediaTypes.MusicRequest musicRequest(UUID id, String notes) {
        Map<String, Object> d = dream(id);
        Look look = look((String) d.get("look"));
        DreamWorld w = d.get("world_json") == null ? null : json.readValue(d.get("world_json").toString(), DreamWorld.class);
        double target = shortSeconds(shots(id).size(), look);
        BigDecimal inr = pricing.musicInr(1);
        route(id, "DREAM_MUSIC", "music.dream", LYRIA, inr);
        budget.require(inr, "Dream Short music (1 Lyria generation)");
        String world = w == null ? String.valueOf(d.get("idea"))
                : String.join(" ", Objects.toString(w.world(), ""), "Palette:", Objects.toString(w.palette(), ""),
                "Light:", Objects.toString(w.light(), ""));
        String prompt = String.join("\n",
                "Instrumental music only, no vocals, no lyrics. About " + Math.round(target) + " seconds, for a wordless vertical Short.",
                "Feeling: a calm, slightly eerie dream; soft, warm, floating, nostalgic. Gentle start, no hard drums.",
                "Look of the video: " + look.brief(),
                "World: " + world,
                notes == null || notes.isBlank() ? "" : "Owner's notes: " + notes.strip()).strip();
        return new com.projectv.media.MediaTypes.MusicRequest(storageId(id), musicModel, prompt, target, 1);
    }

    @Transactional
    public String saveMusic(UUID id, com.projectv.media.MediaTypes.MusicResult r) {
        int songs = r.usage() == null || r.usage().isEmpty() ? Math.max(1, r.segments()) : r.usage().size();
        // Recorded as gemini_api: the provider the monthly Gemini cap counts (BudgetGuard.GEMINI).
        cost(id, BudgetGuard.GEMINI, pricing.musicInr(songs), songs, "song", true, "Dream Short music (" + r.model() + ")");
        UUID asset = assets.create(new NewAsset(null, "audio", r.uri(), "audio/wav", null, null, r.durationSec(),
                LYRIA, r.model(), null, null, AssetStore.LICENCE_GEMINI, true));
        jdbc.sql("UPDATE asset SET dream_short_id = :d WHERE id = :a").param("d", id).param("a", asset).update();
        jdbc.sql("UPDATE dream_short SET music_asset_id = :a, final_asset_id = NULL, updated_at = now() WHERE id = :d")
                .param("a", asset).param("d", id).update();
        return "Music: %.0f s at about %.0f BPM (%s).".formatted(r.durationSec(), r.bpm(), r.model());
    }

    public String musicSource(UUID id) {
        return "lyria".equals(dream(id).get("music_source")) ? "lyria" : "bed";
    }

    /** A new piece of the bed each time (seeded by the Short and how many pieces it has had), away from used starts. */
    public DreamTypes.DreamBedRequest musicBedRequest(UUID id) {
        if (musicBed == null || musicBed.isBlank()) {
            throw new IllegalStateException("No music bed is set: put the track's storage address in DREAM_MUSIC_BED "
                    + "(or choose Lyria for this Short)");
        }
        List<Double> used = jdbc.sql("""
                        SELECT p.seed FROM provenance p JOIN asset a ON a.provenance_id = p.id
                        WHERE p.provider = :p AND p.model = :m AND p.seed IS NOT NULL
                        """).param("p", BED).param("m", bedName()).query(Long.class).list()
                .stream().map(ms -> ms / 1000.0).toList();
        int takes = jdbc.sql("""
                        SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id
                        WHERE a.dream_short_id = :d AND p.provider = :p
                        """).param("d", id).param("p", BED).query(Integer.class).single();
        Map<String, Object> d = dream(id);
        List<Map<String, Object>> shots = shots(id);
        DreamWorld w = d.get("world_json") == null ? null : json.readValue(d.get("world_json").toString(), DreamWorld.class);
        List<String> scenes = shots.stream().map(s -> Objects.toString(s.get("scene"), "")).filter(s -> !s.isBlank()).toList();
        String brief = String.join("\n",
                "Look: " + look((String) d.get("look")).brief(),
                "Idea: " + d.get("idea"),
                w == null ? "" : "World: " + Objects.toString(w.world(), "") + " Palette: " + Objects.toString(w.palette(), "")
                        + " Light: " + Objects.toString(w.light(), ""),
                scenes.isEmpty() ? "" : "Shots, in order: " + String.join(" | ", scenes)).strip();
        List<String> pictures = shots.stream().map(s -> (String) s.get("picture_uri")).filter(Objects::nonNull).limit(3).toList();
        String model = listenModel;
        try {
            budget.require(LISTEN_ESTIMATE_INR, "Dream Short music pick (Gemini listens to the pieces)");
        } catch (BudgetGuard.BudgetExceededException e) {
            model = null; // over the Gemini cap: pick by loudness only, never block the Short over a few paise
        }
        return new DreamTypes.DreamBedRequest(storageId(id), musicBed, shortSeconds(shots.size(), look((String) d.get("look"))),
                id + "#" + takes, used,
                model, brief, pictures, BED_CANDIDATES);
    }

    @Transactional
    public String saveMusicBed(UUID id, DreamTypes.DreamBedResult r) {
        if (r.usage() != null) {
            evidence.costCheck(id, "MUSIC", LISTEN_ESTIMATE_INR.doubleValue(), pricing.textInr(r.usage()).doubleValue());
            cost(id, BudgetGuard.GEMINI, pricing.textInr(r.usage()), r.usage().totalTokens(), "token", false,
                    "Dream Short music pick: Gemini listened to " + r.heard() + " pieces (" + r.model() + ")");
            UUID pick = router.recordExternal(null, "DREAM_MUSIC_PICK", "audio.listen", BudgetGuard.GEMINI,
                    "best fit of " + r.heard() + " pieces: " + Objects.toString(r.why(), ""), List.of(Map.of("provider", BudgetGuard.GEMINI)));
            tag("routing_decision", pick, id);
        }
        UUID decision = router.recordExternal(null, "DREAM_MUSIC", "music.dream", "local_laptop",
                "owner's music bed: free and his own track (Lyria only when chosen for this Short)",
                List.of(Map.of("provider", "local_laptop")));
        tag("routing_decision", decision, id);
        cost(id, "local_laptop", BigDecimal.ZERO, r.durationSec(), "second", false,
                "Dream Short music: the owner's music bed (" + r.bedName() + "), free");
        UUID asset = assets.create(new NewAsset(null, "audio", r.uri(), "audio/wav", null, null, r.durationSec(),
                BED, bedName(), "piece from %s to %s of %s".formatted(clock(r.startSec()), clock(r.startSec() + r.durationSec()), musicBed),
                Math.round(r.startSec() * 1000), LICENCE_OWNER_MUSIC, false));
        jdbc.sql("UPDATE asset SET dream_short_id = :d WHERE id = :a").param("d", id).param("a", asset).update();
        jdbc.sql("UPDATE dream_short SET music_asset_id = :a, final_asset_id = NULL, updated_at = now() WHERE id = :d")
                .param("a", asset).param("d", id).update();
        String how = r.usage() != null ? "best of " + r.heard() + " pieces by ear: " + r.why()
                : r.why() == null || r.why().isBlank() ? "picked by loudness" : r.why();
        return "Music: %.0f s of your track \"%s\" from %s (%s).".formatted(r.durationSec(), r.bedName(), clock(r.startSec()), how);
    }

    /** The bed's file name, e.g. "7 weeks and 3 days 1 hour.m4a" (used as the provenance model). */
    private String bedName() {
        return musicBed.substring(musicBed.lastIndexOf('/') + 1);
    }

    private static String clock(double sec) {
        long s = Math.round(sec);
        return "%d:%02d".formatted(s / 60, s % 60);
    }

    // ------------------------------------------------------------------------------------------- FINISH

    public DreamFinishRequest finishRequest(UUID id) {
        List<DreamFinishClip> clips = new ArrayList<>();
        for (Map<String, Object> s : shots(id)) {
            if (s.get("clip_uri") == null) {
                throw new IllegalStateException("Shot " + (((Number) s.get("ord")).intValue() + 1) + " has no clip yet");
            }
            clips.add(new DreamFinishClip((String) s.get("clip_uri"), s.get("clip_fps") == null ? FPS : ((Number) s.get("clip_fps")).doubleValue(),
                    (String) s.get("scene")));
        }
        if (clips.isEmpty()) {
            throw new IllegalStateException("No shots to finish");
        }
        Map<String, Object> d = dream(id);
        String musicUri = d.get("music_asset_id") == null ? null : assets.uriOf((UUID) d.get("music_asset_id"));
        String musicName = d.get("music_asset_id") == null ? null : jdbc.sql("""
                        SELECT CASE WHEN p.provider = :bed THEN 'your track (' || p.model || ')' ELSE 'Lyria score' END
                        FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.id = :a
                        """).param("bed", BED).param("a", d.get("music_asset_id")).query(String.class).optional().orElse(null);
        Look look = look((String) d.get("look"));
        return new DreamFinishRequest(storageId(id), (String) d.get("look"), clips, 1080, 1920, 24, look.dissolveSec(),
                musicDir == null || musicDir.isBlank() ? null : musicDir, publicUrl, musicUri, musicName,
                look.speed(), look.handheld(), !Boolean.FALSE.equals(d.get("edit_music")),
                !Boolean.FALSE.equals(d.get("edit_ambience")), !Boolean.FALSE.equals(d.get("edit_pacing")),
                // a picked Scout angle is one story told in order: pacing must not reshuffle it (19 Nov: the ending played third)
                scouted(d) != null && scouted(d).chosen() != null);
    }

    @Transactional
    public String saveFinish(UUID id, DreamFinishResult r) {
        if (r.uri() == null) {
            throw new IllegalStateException("Finish failed: " + r.error());
        }
        UUID asset = assets.create(new NewAsset(null, "video", r.uri(), "video/mp4", r.width(), r.height(), r.durationSec(),
                "local_laptop", "dream finish (" + r.smoothing() + ", FFmpeg look)", null, null, AssetStore.LICENCE_WAN, true));
        jdbc.sql("UPDATE asset SET dream_short_id = :d, fps = :f WHERE id = :a").param("d", id).param("f", r.fps()).param("a", asset).update();
        jdbc.sql("UPDATE dream_short SET final_asset_id = :a, updated_at = now() WHERE id = :d").param("a", asset).param("d", id).update();
        evidence.metrics(id, asset, (String) dream(id).get("look"), r.metrics());
        UUID decision = router.recordExternal(null, "DREAM_FINISH", "interp.rife", "local_laptop",
                "laptop first: smoothing, look and mix are free on this laptop (" + r.smoothing() + ")",
                List.of(Map.of("provider", "local_laptop")));
        tag("routing_decision", decision, id);
        return "Finished: %.1f s, %dx%d at %d fps (%s)%s.".formatted(r.durationSec(), r.width(), r.height(), r.fps(),
                "rife-laptop".equals(r.smoothing()) ? "smoothed with RIFE on the laptop" : "smoothed with FFmpeg",
                r.music() == null ? ", no music" : ", music: " + r.music());
    }

    // ------------------------------------------------------------------------------------------- QUALITY CHECK

    public DreamTypes.DreamCheckRequest checkRequest(UUID id) {
        Map<String, Object> d = dream(id);
        if (!qualityCheck || d.get("final_asset_id") == null) {
            return null;
        }
        try {
            budget.require(CHECK_ESTIMATE_INR, "Dream Short quality check");
        } catch (BudgetGuard.BudgetExceededException e) {
            return null; // over the Gemini cap: the Short is finished anyway
        }
        return new DreamTypes.DreamCheckRequest(storageId(id), assets.uriOf((UUID) d.get("final_asset_id")), checkBrief(id, d),
                listenModel, null);
    }

    /** The same check on the pictures (a few paise), so most redos happen before any GPU clip is paid for. */
    public DreamTypes.DreamCheckRequest pictureCheckRequest(UUID id) {
        Map<String, Object> d = dream(id);
        List<String> pictures = shots(id).stream().map(s -> (String) s.get("picture_uri")).filter(Objects::nonNull).toList();
        if (!qualityCheck || pictures.isEmpty()) {
            return null;
        }
        try {
            budget.require(CHECK_ESTIMATE_INR, "Dream Short picture check");
        } catch (BudgetGuard.BudgetExceededException e) {
            return null;
        }
        return new DreamTypes.DreamCheckRequest(storageId(id), null, checkBrief(id, d), listenModel, pictures);
    }

    private String checkBrief(UUID id, Map<String, Object> d) {
        StringBuilder brief = new StringBuilder("Look: " + look((String) d.get("look")).brief());
        brief.append(lessons("WRITER")).append(lessons("PICTURE"));
        brief.append("\nTitle: ").append(Objects.toString(d.get("title"), "")).append("\nIdea: ").append(d.get("idea"));
        Scouted s = scouted(d);
        if (s != null && s.chosen() != null) {
            brief.append("\nThe owner's chosen angle (one story, shots in this order): ").append(s.chosen().title())
                    .append(" - ").append(s.chosen().idea());
        }
        int n = 1;
        for (Map<String, Object> shot : shots(id)) {
            brief.append("\nShot ").append(n++).append(": ").append(shot.get("scene"));
        }
        return brief.toString();
    }

    @Transactional
    public String saveCheck(UUID id, DreamTypes.DreamCheckResult r, String stage) {
        boolean pictures = "PICTURES".equals(stage);
        if (r.usage() != null) {
            BigDecimal inr = pricing.textInr(r.usage());
            cost(id, BudgetGuard.GEMINI, inr, r.usage().totalTokens(), "token", false,
                    "Dream Short " + (pictures ? "picture" : "quality") + " check (" + r.model() + ")");
            evidence.costCheck(id, pictures ? "PICTURE_CHECK" : "CHECK", CHECK_ESTIMATE_INR.doubleValue(), inr.doubleValue());
            UUID decision = router.recordExternal(null, "DREAM_CHECK", "video.watch", BudgetGuard.GEMINI,
                    "quality check: Gemini watches the finished Short (only model that takes video)",
                    List.of(Map.of("provider", BudgetGuard.GEMINI)));
            tag("routing_decision", decision, id);
        }
        if (r.error() != null || r.overall() <= 0) {
            evidence.event(id, stage, "check", null, "check unavailable: " + r.error(), null);
            return (pictures ? "Picture" : "Quality") + " check unavailable this time.";
        }
        jdbc.sql("UPDATE dream_short SET " + (pictures ? "picture_check_json" : "check_json")
                        + " = CAST(:j AS jsonb), updated_at = now() WHERE id = :d")
                .param("j", json.writeValueAsString(r)).param("d", id).update();
        evidence.event(id, stage, "check", null, r.summary(), Map.of("overall", r.overall(), "issues",
                r.issues() == null ? 0 : r.issues().size()));
        int issues = r.issues() == null ? 0 : r.issues().size();
        return (pictures ? "Picture" : "Quality") + " check: %d/10%s.".formatted(r.overall(), issues == 0 ? ", no issues"
                : ", %d issue%s - %s".formatted(issues, issues > 1 ? "s" : "", r.issues().getFirst().problem()));
    }

    // ------------------------------------------------------------------------------------------- shared helpers

    /** The router's choice, logged and linked to the Short. Throws (deferral) when nothing can run it. */
    private void route(UUID id, String stage, String capability, String provider, BigDecimal inr) {
        var choice = router.choose(null, stage, capability, List.of(new Router.Candidate(provider, inr)), provider, true);
        tag("routing_decision", choice.decisionId(), id);
    }

    private void tag(String table, UUID rowId, UUID dreamId) {
        if (rowId != null) {
            jdbc.sql("UPDATE " + table + " SET dream_short_id = :d WHERE id = :id").param("d", dreamId).param("id", rowId).update();
        }
    }

    private void cost(UUID id, String provider, BigDecimal inr, double units, String unitType, boolean estimated, String note) {
        jdbc.sql("""
                        INSERT INTO cost_entry (id, video_id, dream_short_id, provider_id, amount_inr, units, unit_type, estimated, note)
                        VALUES (:id, NULL, :d, :p, :amt, :u, :ut, :est, :note)
                        """)
                .param("id", UUID.randomUUID()).param("d", id).param("p", provider).param("amt", inr)
                .param("u", BigDecimal.valueOf(units)).param("ut", unitType).param("est", estimated).param("note", note).update();
    }

    private void staleFinal(UUID id) {
        jdbc.sql("UPDATE dream_short SET final_asset_id = NULL, updated_at = now() WHERE id = :d").param("d", id).update();
    }

    /**
     * The id the media worker files things under (videos/&lt;id&gt;/...) and reports clip progress / checks Stop with.
     * UUIDs never collide with a video's, so the existing clip progress and Stop endpoints work unchanged.
     */
    static String storageId(UUID id) {
        return id.toString();
    }

    Map<String, Object> dream(UUID id) {
        return jdbc.sql("SELECT * FROM dream_short WHERE id = :id").param("id", id).query().singleRow();
    }

    List<Map<String, Object>> shots(UUID id) {
        return jdbc.sql("""
                        SELECT s.*, p.uri_hot AS picture_uri, c.uri_hot AS clip_uri, c.fps AS clip_fps
                        FROM dream_shot s LEFT JOIN asset p ON p.id = s.picture_asset_id LEFT JOIN asset c ON c.id = s.clip_asset_id
                        WHERE s.dream_short_id = :d ORDER BY s.ord
                        """).param("d", id).query().listOfRows();
    }

    public void status(UUID id, String step, String status, String message) {
        jdbc.sql("""
                        UPDATE dream_short SET step = :step, status = :st, message = COALESCE(:m, message), updated_at = now()
                        WHERE id = :id
                        """).param("step", step).param("st", status).param("m", message).param("id", id).update();
        if ("FAILED_WAITING".equals(status)) {
            evidence.event(id, step, "failure", null, message, null);
        }
    }

    public void startClipBatch(UUID id) {
        clipProgress.startBatch(id);
    }
}
