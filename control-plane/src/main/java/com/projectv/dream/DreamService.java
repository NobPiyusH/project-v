package com.projectv.dream;

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

    static final String ZIMAGE = "modal_zimage";
    static final String WAN = "modal_wan";
    static final String LYRIA = "gemini_lyria";
    /** Provenance provider for pieces of the owner's music bed (cut free on the laptop). */
    static final String BED = "owner_music_bed";
    static final UUID LICENCE_OWNER_MUSIC = UUID.fromString("00000000-0000-0000-0000-00000000a012");
    static final int FPS = 16;          // Wan's own frame rate; the finish makes it 24
    static final int MIN_FRAMES = 33;
    static final int MAX_FRAMES = 81;

    /** A look: what the writer aims for, and what every picture and clip prompt ends with. */
    record Look(String brief, String pictureStyle, String motionStyle) {
    }

    static final Map<String, Look> LOOKS = Map.of(
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
                        @Value("${projectv.dream.music-bed:}") String musicBed) {
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
    }

    static Look look(String id) {
        return LOOKS.getOrDefault(id == null ? "dreamcore" : id, LOOKS.get("dreamcore"));
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

    @Transactional
    public String writeShots(UUID id, String notes) {
        Map<String, Object> d = dream(id);
        Look look = look((String) d.get("look"));
        int count = ((Number) d.get("shot_count")).intValue();
        String system = """
                You are the writer of a short, wordless, music-led vertical video (YouTube Shorts / Reels) for a channel
                about dreams and impossible places. Invent ONE coherent world and %d shots that move through it like a
                dream, each a single continuous camera move. Every shot must be a fresh, specific image (not generic).
                Look: %s
                Return JSON: {"title": short title, "world": one paragraph describing the shared world (materials, objects,
                scale, mood), "palette": the exact colour palette, "light": the light and atmosphere,
                "shots": [{"scene": what this shot shows (one or two sentences, concrete), "motion": the camera move and
                the small motion inside the scene}]} with exactly %d shots.
                """.formatted(count, look.brief(), count);
        String user = "Idea: " + d.get("idea") + (notes == null || notes.isBlank() ? "" : "\nOwner's notes for this rewrite: " + notes);
        DreamWorld w = llm.call(null, "dream.write", system, user, DreamWorld.class);
        if (w == null || w.shots() == null || w.shots().isEmpty()) {
            throw new IllegalStateException("The writer returned no shots");
        }
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

    static String picturePrompt(DreamWorld w, PlannedDreamShot s, Look look) {
        return String.join(" ", s.scene().strip(), "World:", Objects.toString(w.world(), "").strip(),
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
            String prompt = (String) s.get("picture_prompt");
            if (notes != null && !notes.isBlank()) {
                prompt = prompt + " Owner's notes: " + notes.strip();
            }
            int attempt = ((Number) s.get("picture_takes")).intValue() + 1;
            jobs.add(new KeyframeJob(shotId, attempt, ZIMAGE, prompt, 768, 1344, "9:16", "1K", null,
                    Math.abs((long) shotId.hashCode()) + attempt));
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
        // One GPU, clips in turn: the ~2 min start-up + model load is paid once for the whole Short.
        return new ClipRequest(storageId(id), wanApp, wanClass, publicUrl, jobs, 1, gpu, inr.setScale(2, RoundingMode.HALF_UP),
                "wan", null, null);
    }

    List<ClipJob> clipJobs(UUID id, List<String> only) {
        Map<String, Object> d = dream(id);
        int[] size = clipSize((String) d.get("quality"));
        int frames = frames(shotSeconds);
        Set<String> wanted = only == null ? null : new HashSet<>(only);
        List<ClipJob> jobs = new ArrayList<>();
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
                    (String) s.get("picture_uri"), (String) s.get("motion_prompt"), size[0], size[1], frames,
                    Math.abs((long) shotId.hashCode()) + take));
        }
        return jobs;
    }

    /** Wan fast render time (scaled by pixels and frames from the measured 480p draft) + one container start-up. */
    double gpuSeconds(List<ClipJob> jobs, String quality) {
        if (jobs.isEmpty()) {
            return 0;
        }
        int[] size = clipSize(quality);
        double pixels = (double) size[0] * size[1] / (480.0 * 832.0);
        double per81 = pricing.wanSecondsPer81Frames("draft") * pixels;
        return jobs.stream().mapToDouble(j -> per81 * j.numFrames() / MAX_FRAMES).sum() + pricing.gpuContainerStartSeconds();
    }

    @Transactional
    public String saveClips(UUID id, ClipRequest req, ClipResult r) {
        Map<String, ClipJob> jobs = new java.util.HashMap<>();
        req.jobs().forEach(j -> jobs.put(j.shotId() + ":" + j.take(), j));
        int ok = 0;
        int stopped = 0;
        double gpu = 0;
        List<String> errors = new ArrayList<>();
        for (ClipOutput o : r.clips()) {
            ClipJob job = jobs.get(o.shotId() + ":" + o.take());
            if (job == null) {
                continue;
            }
            gpu += o.gpuSeconds();
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
        if (r.containers() > 0) {
            double start = r.containers() * pricing.gpuContainerStartSeconds();
            cost(id, "modal_h100", pricing.modalGpuInr("H100", start), start, "gpu_seconds", true,
                    "Dream Short: Wan GPU start-up + model load (estimated)");
        }
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
    double shortSeconds(int shots) {
        double clip = (double) frames(shotSeconds) / FPS;
        return shots * clip - Math.max(0, shots - 1) * Math.min(0.5, clip / 3) + 1;
    }

    @Transactional
    public com.projectv.media.MediaTypes.MusicRequest musicRequest(UUID id, String notes) {
        Map<String, Object> d = dream(id);
        Look look = look((String) d.get("look"));
        DreamWorld w = d.get("world_json") == null ? null : json.readValue(d.get("world_json").toString(), DreamWorld.class);
        double target = shortSeconds(shots(id).size());
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
        return new DreamTypes.DreamBedRequest(storageId(id), musicBed, shortSeconds(shots(id).size()), id + "#" + takes, used);
    }

    @Transactional
    public String saveMusicBed(UUID id, DreamTypes.DreamBedResult r) {
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
        return "Music: %.0f s of your track \"%s\" from %s (free).".formatted(r.durationSec(), r.bedName(), clock(r.startSec()));
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
            clips.add(new DreamFinishClip((String) s.get("clip_uri"), s.get("clip_fps") == null ? FPS : ((Number) s.get("clip_fps")).doubleValue()));
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
        return new DreamFinishRequest(storageId(id), (String) d.get("look"), clips, 1080, 1920, 24, 0.5,
                musicDir == null || musicDir.isBlank() ? null : musicDir, publicUrl, musicUri, musicName);
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
        UUID decision = router.recordExternal(null, "DREAM_FINISH", "interp.rife", "local_laptop",
                "laptop first: smoothing, look and mix are free on this laptop (" + r.smoothing() + ")",
                List.of(Map.of("provider", "local_laptop")));
        tag("routing_decision", decision, id);
        return "Finished: %.1f s, %dx%d at %d fps (%s)%s.".formatted(r.durationSec(), r.width(), r.height(), r.fps(),
                "rife-laptop".equals(r.smoothing()) ? "smoothed with RIFE on the laptop" : "smoothed with FFmpeg",
                r.music() == null ? ", no music" : ", music: " + r.music());
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
    }

    public void startClipBatch(UUID id) {
        clipProgress.startBatch(id);
    }
}
