package com.projectv.dream;

import com.projectv.production.RedoStopRule;
import com.projectv.agents.Llm;
import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.dream.DreamLooks.Look;
import com.projectv.dream.DreamScout.Scouted;
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

import static com.projectv.dream.DreamLooks.*;

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
    /** The rented-pod route's provider id (pictures and clips): config/providers.yaml, docs/plans/m3-gpu-router.md. */
    static final String POD = "gpu_pod";
    /** Provenance model name for FLUX.2 klein pictures (licence row a013 says Apache-2.0). */
    static final String FLUX2_KLEIN_NAME = "FLUX.2 klein 4B";
    static final String LYRIA = "gemini_lyria";
    /** Provenance provider for pieces of the owner's music bed (cut free on the laptop). */
    static final String BED = "owner_music_bed";
    static final UUID LICENCE_OWNER_MUSIC = UUID.fromString("00000000-0000-0000-0000-00000000a012");
    /** Pieces of the bed Gemini compares for one Short, and what that listen is held to before the call (a few paise). */
    static final int BED_CANDIDATES = 6;
    static final BigDecimal LISTEN_ESTIMATE_INR = new BigDecimal("0.80"); // measured Rs 0.57-0.79 (19 Nov)
    /** Quality check: a ~360p copy watched by Gemini (estimate until measured). */
    static final BigDecimal CHECK_ESTIMATE_INR = new BigDecimal("0.50");

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
    private final String listenModel;
    private final DreamEvidence evidence;
    private final DreamMusic music;
    private final DreamScout scout;
    private final String briefModel;
    private final boolean qualityCheck;
    private final DreamGpu gpu; // null on the Modal route (the default)

    /** Modal route (the default): what every existing caller and test uses. */
    private final String pictureModel; // the studio default for new Shorts on the pod (projectv.dream.picture-model)

    /** The studio's default pod picture model for new Shorts. */
    public String defaultPictureModel() {
        return pictureModel;
    }

    /** This Short's pod picture model ("flux2-klein" or "zimage"). */
    static String pictureModelOf(Map<String, Object> d) {
        return DreamSettings.cleanPictureModel((String) d.get("picture_model"), "zimage");
    }

    public DreamService(JdbcClient jdbc, ObjectMapper json, Llm llm, AssetStore assets, Pricing pricing, BudgetGuard budget,
                        Router router, com.projectv.video.ClipProgress clipProgress, DreamSettings settings) {
        this(jdbc, json, llm, assets, pricing, budget, router, clipProgress, settings, null);
    }

    @org.springframework.beans.factory.annotation.Autowired
    public DreamService(JdbcClient jdbc, ObjectMapper json, Llm llm, AssetStore assets, Pricing pricing, BudgetGuard budget,
                        Router router, com.projectv.video.ClipProgress clipProgress, DreamSettings settings,
                        DreamGpu gpu) {
        this.jdbc = jdbc;
        this.json = json;
        this.llm = llm;
        this.assets = assets;
        this.pricing = pricing;
        this.budget = budget;
        this.router = router;
        this.clipProgress = clipProgress;
        this.zimageApp = settings.zimageApp();
        this.zimageClass = settings.zimageClass();
        this.wanApp = settings.wanApp();
        this.wanClass = settings.wanClass();
        this.publicUrl = settings.publicUrl();
        this.autoApproveInr = settings.autoApproveInr();
        this.shotSeconds = settings.shotSeconds();
        this.musicDir = settings.musicDir();
        this.listenModel = settings.listenModel();
        this.evidence = new DreamEvidence(jdbc, json);
        this.briefModel = settings.briefModel();
        this.qualityCheck = settings.qualityCheck();
        this.pictureModel = settings.pictureModel();
        this.scout = new DreamScout(this, jdbc, json, llm, pricing, budget, router, evidence, settings);
        this.music = new DreamMusic(this, jdbc, json, assets, pricing, budget, router, evidence, settings);
        this.gpu = gpu != null && gpu.enabled() ? gpu : null;
    }

    /** Start the Short's pod while the writer works, so the weight download overlaps the writing. Best effort. */
    private void warmGpu(UUID id) {
        // Collect first, rent later: in a batch the pod is rented when the Shorts reach their pictures (gpuBatchAhead).
        if (gpu != null && (gpu.usersOnPod() > 0 || !othersStillWriting(id))) {
            gpu.warm(id);
        }
    }

    /** The pod is released as soon as it cannot be needed soon: GPU steps over, Short ended, or a step failed (Retry rents afresh). */
    private void releaseGpuIfDone(UUID id, String step, String status) {
        if (gpu != null && (List.of("COMPLETED", "CANCELLED", "FAILED_WAITING").contains(status)
                || "MUSIC".equals(step) || "FINISH".equals(step))) {
            // GPU power Manual (owner, 20 Nov 2026): the pod stays on after the GPU steps, for a quick redo. It goes off by
            // the owner's button, after the idle time (DreamGpu.releaseIdleManual), or when the Short is cancelled.
            if (gpu.holdManual(id) && !"CANCELLED".equals(status)) {
                return;
            }
            gpu.release(id, step + " " + status);
        }
    }

    public DreamEvidence evidence() {
        return evidence;
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
        warmGpu(id);
        Map<String, Object> d = dream(id);
        if (unfiltered(d)) {
            return writeUnfiltered(id, d);
        }
        Look look = look((String) d.get("look"));
        int count = ((Number) d.get("shot_count")).intValue();
        Scouted scouted = scouted(d);
        boolean chain = Boolean.TRUE.equals(d.get("chain_shots"));
        int group = ((Number) d.get("chain_group")).intValue();
        boolean locked = chain && group == 0 && Boolean.TRUE.equals(d.get("chain_locked"));
        boolean hook = Boolean.TRUE.equals(d.get("hook_title"));
        boolean paths = Boolean.TRUE.equals(d.get("path_choice"));
        var seed = DreamIdeaPacks.resolve((String) d.get("idea_ref"));
        boolean svi = DreamSvi.on(d);
        int sviScenes = svi ? DreamSvi.scenes(d) : 0;
        int sviBeats = svi ? DreamSvi.beatsFor(DreamSvi.seconds(d)) : 0;
        // The owner's chosen Scout angle replaces the look's shot structure (19 Nov: as a user-message note it lost
        // to "a different place every shot" twice). The look still sets the style. Chained shots (off by default) are
        // one unbroken take, so the writer describes one continuous camera move in one world.
        String structure = svi ? DreamSvi.structure(sviScenes, DreamSvi.seconds(d), sviBeats)
                : scouted != null && scouted.chosen() != null
                ? ("Invent %d shots that are consecutive moments of ONE idea the owner chose, in the same place, flowing "
                + "into each other: %s - %s").formatted(count, scouted.chosen().title(), scouted.chosen().idea())
                : locked ? LOCKED_STRUCTURE.formatted(count)
                : Boolean.TRUE.equals(d.get("chain_bridge")) ? BRIDGE_STRUCTURE.formatted(count)
                : chain ?(group > 0 ? CHAIN_GROUP_STRUCTURE.formatted(count, group, group) : CHAIN_STRUCTURE.formatted(count))
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
                %s%s}%s.
                """.formatted(structure, "architecture".equals(d.get("look")) ? FEEL_ARCHITECTURE : FEEL, look.brief(),
                svi ? DreamSvi.jsonFields(sviScenes, sviBeats) : SHOTS_FIELD,
                (hook ? HOOK_FIELD : "") + (locked ? CAMERA_PATH_FIELD : "") + (paths ? DreamBranches.PATH_FIELD : ""),
                svi ? "" : " with exactly %d shots".formatted(count)) + lessons("WRITER");
        String user = "Idea: " + d.get("idea") + (notes == null || notes.isBlank() ? "" : "\nOwner's notes for this rewrite: " + notes)
                + (seed.isPresent() ? DreamIdeaPacks.forWriter(seed.get()) : "")
                + (scouted == null ? "" : scouted.forWriter())
                + continuation(d);
        java.time.Instant since = java.time.Instant.now();
        DreamWorld w = llm.call(null, "dream.write", system, user, DreamWorld.class, briefModel); // Claude when configured (owner 20 Nov 2026), else the Gemini list
        List<DreamSvi.Beat> beats = svi && w != null ? DreamSvi.flatten(w.scenes(), sviScenes, sviBeats) : null;
        if (svi && w != null) { // the beats as shots, so every check below (and the dashboard) reads them as usual
            w = new DreamWorld(w.title(), w.world(), w.palette(), w.light(), w.hero(),
                    beats.stream().map(b -> new PlannedDreamShot(b.sceneText(), b.motion())).toList(), w.hook(), w.cameraPath(),
                    w.paths(), w.style(), w.scenes());
        }
        if (w == null || w.shots() == null || w.shots().isEmpty()) {
            throw new IllegalStateException("The writer returned no shots");
        }
        String rewrite = scouted == null ? null : tooSimilar(id, w, scouted);
        if (rewrite != null && !svi) { // one rewrite: never copy a Short that already exists
            DreamWorld again = llm.call(null, "dream.write", system, user + "\nRewrite: " + rewrite, DreamWorld.class, briefModel);
            if (again != null && again.shots() != null && !again.shots().isEmpty()) {
                w = again;
            }
        }
        linkLlmCosts(id, "dream.", since);
        if (svi) {
            return saveSviBeats(id, w, beats, look);
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
                    .param("pp", picturePrompt(w, s, look)).param("mp", locked ? lockedMotionPrompt(w, s, look) : motionPrompt(s, look)).update();
        }
        jdbc.sql("""
                        UPDATE dream_short SET title = :t, world_json = CAST(:w AS jsonb), final_asset_id = NULL,
                               music_asset_id = NULL, updated_at = now() WHERE id = :d
                        """)
                .param("t", w.title()).param("w", json.writeValueAsString(w)).param("d", id).update();
        if (hook && w.hook() != null && !w.hook().isBlank()) { // the owner's (or the idea pack's) own hook is never replaced
            jdbc.sql("UPDATE dream_short SET hook_text = :h WHERE id = :d AND NOT hook_locked").param("h", w.hook().strip())
                    .param("d", id).update();
        }
        List<String> options = paths ? DreamBranches.cleanPaths(w.paths()) : null;
        if (options != null) { // the owner's own edited options are never replaced
            jdbc.sql("UPDATE dream_short SET path_a = :a, path_b = :b WHERE id = :d AND NOT path_locked")
                    .param("a", options.get(0)).param("b", options.get(1)).param("d", id).update();
        }
        return "\"%s\": %d shots in one world (%s).".formatted(w.title(), shots.size(), Objects.toString(w.palette(), "").strip());
    }

    /** Writer's return field for ordinary shots (SVI scenes ask for {@link DreamSvi#jsonFields} instead). */
    static final String SHOTS_FIELD = "\"shots\": [{\"scene\": what this shot shows (one or two sentences, concrete), \"motion\": "
            + "the camera move and\nthe small motion inside the scene}]";

    /**
     * SVI scenes: one dream_shot row per beat. Beat 1 carries the scene's picture prompt (the world, palette, light and the
     * one shared style line); every beat has its own motion line plus the same style line, so the scenes look like one film.
     */
    private String saveSviBeats(UUID id, DreamWorld w, List<DreamSvi.Beat> beats, Look look) {
        String style = Objects.toString(w.style(), "").strip();
        String styled = style.isEmpty() ? "" : " Style: " + style + ".";
        jdbc.sql("DELETE FROM dream_shot WHERE dream_short_id = :d").param("d", id).update();
        int ord = 0;
        for (DreamSvi.Beat b : beats) {
            PlannedDreamShot s = new PlannedDreamShot(b.sceneText(), b.motion());
            String motion = b.beat() == 1 ? motionPrompt(s, look) : (b.motion() + ". " + look.motionStyle()).replaceAll("\\s+", " ");
            insertBeat(id, ord++, b, (picturePrompt(w, s, look) + styled).strip(), (motion + styled).strip());
        }
        jdbc.sql("""
                        UPDATE dream_short SET title = :t, shot_count = :n, world_json = CAST(:w AS jsonb), final_asset_id = NULL,
                               music_asset_id = NULL, updated_at = now() WHERE id = :d
                        """)
                .param("t", w.title()).param("n", beats.size()).param("w", json.writeValueAsString(w)).param("d", id).update();
        if (Boolean.TRUE.equals(dream(id).get("hook_title")) && w.hook() != null && !w.hook().isBlank()) {
            jdbc.sql("UPDATE dream_short SET hook_text = :h WHERE id = :d AND NOT hook_locked").param("h", w.hook().strip())
                    .param("d", id).update();
        }
        List<String> options = Boolean.TRUE.equals(dream(id).get("path_choice")) ? DreamBranches.cleanPaths(w.paths()) : null;
        if (options != null) {
            jdbc.sql("UPDATE dream_short SET path_a = :a, path_b = :b WHERE id = :d AND NOT path_locked")
                    .param("a", options.get(0)).param("b", options.get(1)).param("d", id).update();
        }
        long scenes = beats.stream().mapToInt(DreamSvi.Beat::scene).distinct().count();
        return "\"%s\": %d SVI scene%s, %d beats in one world (%s).".formatted(w.title(), scenes, scenes == 1 ? "" : "s",
                beats.size(), Objects.toString(w.palette(), "").strip());
    }

    private void insertBeat(UUID id, int ord, DreamSvi.Beat b, String picture, String motion) {
        jdbc.sql("""
                        INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt, scene_no, beat_no)
                        VALUES (:id, :d, :o, :scene, :pp, :mp, :sn, :bn)
                        """)
                .param("id", UUID.randomUUID()).param("d", id).param("o", ord).param("scene", b.sceneText())
                .param("pp", picture).param("mp", motion).param("sn", b.scene()).param("bn", b.beat()).update();
    }

    /** Unfiltered "writing": no model is called. Each non-empty line of the prompt becomes one shot, word for word (at most 8). */
    private String writeUnfiltered(UUID id, Map<String, Object> d) {
        if (DreamSvi.on(d)) { // SVI: a blank line separates scenes, each line is a beat; words exactly as written
            List<DreamSvi.Beat> beats = DreamSvi.unfiltered((String) d.get("idea"));
            if (beats.isEmpty()) {
                throw new IllegalStateException("The prompt is empty");
            }
            String[] words = beats.getFirst().motion().split("\\s+");
            String title = String.join(" ", java.util.Arrays.copyOf(words, Math.min(words.length, 6))) + (words.length > 6 ? "…" : "");
            jdbc.sql("DELETE FROM dream_shot WHERE dream_short_id = :d").param("d", id).update();
            int ord = 0;
            for (DreamSvi.Beat b : beats) {
                insertBeat(id, ord++, b, b.sceneText(), b.motion());
            }
            DreamWorld w = new DreamWorld(title, "", "", "", "", beats.stream().map(b -> new PlannedDreamShot(b.sceneText(), b.motion())).toList());
            jdbc.sql("""
                            UPDATE dream_short SET title = :t, shot_count = :n, world_json = CAST(:w AS jsonb), final_asset_id = NULL,
                                   music_asset_id = NULL, updated_at = now() WHERE id = :d
                            """)
                    .param("t", title).param("n", beats.size()).param("w", json.writeValueAsString(w)).param("d", id).update();
            long scenes = beats.stream().mapToInt(DreamSvi.Beat::scene).distinct().count();
            return "Unfiltered SVI: %d scene%s, %d beats, your words exactly as written (nothing added, no writer called)."
                    .formatted(scenes, scenes == 1 ? "" : "s", beats.size());
        }
        List<String> lines = Objects.toString(d.get("idea"), "").lines().map(String::strip).filter(l -> !l.isEmpty()).limit(8).toList();
        if (lines.isEmpty()) {
            throw new IllegalStateException("The prompt is empty");
        }
        String[] words = lines.getFirst().split("\\s+");
        String title = String.join(" ", java.util.Arrays.copyOf(words, Math.min(words.length, 6))) + (words.length > 6 ? "…" : "");
        List<PlannedDreamShot> shots = lines.stream().map(l -> new PlannedDreamShot(l, l)).toList();
        jdbc.sql("DELETE FROM dream_shot WHERE dream_short_id = :d").param("d", id).update();
        int ord = 0;
        for (PlannedDreamShot s : shots) {
            jdbc.sql("""
                            INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt)
                            VALUES (:id, :d, :o, :scene, :pp, :mp)
                            """)
                    .param("id", UUID.randomUUID()).param("d", id).param("o", ord++).param("scene", s.scene())
                    .param("pp", s.scene()).param("mp", s.motion()).update();
        }
        DreamWorld w = new DreamWorld(title, String.join(" ", lines), "", "", "", shots);
        jdbc.sql("""
                        UPDATE dream_short SET title = :t, shot_count = :n, world_json = CAST(:w AS jsonb), final_asset_id = NULL,
                               music_asset_id = NULL, updated_at = now() WHERE id = :d
                        """)
                .param("t", title).param("n", shots.size()).param("w", json.writeValueAsString(w)).param("d", id).update();
        return "Unfiltered: %d shot%s, your words exactly as written (nothing added, no writer called).".formatted(shots.size(),
                shots.size() == 1 ? "" : "s");
    }

    // ------------------------------------------------------------------------------------------- SCOUT

    // Scout: see DreamScout.
    Scouted scouted(Map<String, Object> d) {
        return scout.scouted(d);
    }

    public boolean scoutWanted(UUID id) {
        return scout.scoutWanted(id);
    }

    public DreamTypes.DreamScoutRequest scoutRequest(UUID id, String notes) {
        return scout.scoutRequest(id, notes);
    }

    void rememberWatched(List<DreamTypes.ScoutVideo> videos, String model) {
        scout.rememberWatched(videos, model);
    }

    @Transactional
    public String saveScout(UUID id, DreamTypes.DreamScoutRequest req, DreamTypes.DreamScoutResult r) {
        return scout.saveScout(id, req, r);
    }

    String tooSimilar(UUID id, DreamWorld w, Scouted scouted) {
        return scout.tooSimilar(id, w, scouted);
    }

    /** LLM costs are recorded without a Short; link this Short's calls (by purpose, since a moment) to it. */
    void linkLlmCosts(UUID id, String purposePrefix, java.time.Instant since) {
        jdbc.sql("""
                        UPDATE cost_entry SET dream_short_id = :d
                        WHERE dream_short_id IS NULL AND video_id IS NULL AND note LIKE :p AND ts >= :since
                        """).param("d", id).param("p", purposePrefix + "%").param("since", java.sql.Timestamp.from(since)).update();
    }

    double llmCostsSince(UUID id, String purposePrefix, java.time.Instant since) {
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

    /** Locked scene: every clip restates the one world and the one camera path, so the shots stay the same scene. */
    static String lockedMotionPrompt(DreamWorld w, PlannedDreamShot s, Look look) {
        String path = Objects.toString(w.cameraPath(), "").strip();
        return (motionPrompt(s, look) + (path.isEmpty() ? "" : " One continuous camera path for the whole video: " + path + ".")
                + " The same world throughout: " + Objects.toString(w.world(), "").strip() + " Palette: "
                + Objects.toString(w.palette(), "").strip() + " Light: " + Objects.toString(w.light(), "").strip())
                .replaceAll("\\s+", " ");
    }

    static String motionPrompt(PlannedDreamShot s, Look look) {
        return (Objects.toString(s.motion(), "").strip() + ". " + s.scene().strip() + ". " + look.motionStyle()).replaceAll("\\s+", " ");
    }

    // ------------------------------------------------------------------------------------------- PICTURES

    @Transactional
    public KeyframeRequest pictureRequest(UUID id, List<String> only, String notes) {
        List<KeyframeJob> jobs = new ArrayList<>();
        Set<String> wanted = only == null ? null : new HashSet<>(only);
        boolean chain = chained(id);
        int group = chain ? chainGroup(id) : 0;
        Map<String, Object> d = dream(id);
        boolean fromParent = d.get("start_clip_asset_id") != null;
        for (Map<String, Object> s : shots(id)) {
            String shotId = s.get("id").toString();
            if (continues(chain, group, ((Number) s.get("ord")).intValue()) || DreamSvi.beatOf(s) > 1) {
                continue; // chained / a later SVI beat: it continues from the clip before, so it needs no picture
            }
            if (fromParent && ((Number) s.get("ord")).intValue() == 0) {
                continue; // a continued episode: shot 1 starts from the previous episode's last frame
            }
            if (wanted == null ? s.get("picture_asset_id") != null : !wanted.contains(shotId)) {
                continue;
            }
            String prompt = (String) s.get("picture_prompt") + (unfiltered(d) ? "" : lessons("PICTURE"));
            if (notes != null && !notes.isBlank()) {
                prompt = prompt + " Owner's notes: " + notes.strip();
            }
            int attempt = ((Number) s.get("picture_takes")).intValue() + 1;
            // One seed for the whole Short (owner, 19 Nov: the shelter changed shape every shot); a redo moves it on.
            jobs.add(new KeyframeJob(shotId, attempt, gpu != null ? POD : ZIMAGE, prompt, 768, 1344, "9:16", "1K", null,
                    sceneSeed(id, d) + attempt - 1, gpu != null ? pictureModelOf(d) : null)); // Modal route: Z-Image only
        }
        if (gpu != null) {
            if (jobs.isEmpty()) {
                return new KeyframeRequest(storageId(id), null, zimageApp, zimageClass, jobs);
            }
            BigDecimal inr = gpu.inr(gpu.pictureSeconds(id, jobs.size()));
            route(id, "DREAM_PICTURES", "image.dream", POD, inr);
            var session = gpu.session(id);
            return new KeyframeRequest(storageId(id), null, zimageApp, zimageClass, jobs, session.url(), session.readyTimeoutSeconds());
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
            boolean flux = "flux2-klein".equals(job.podModel());
            UUID asset = assets.create(new NewAsset(null, "image", o.uri(), "image/png", job.width(), job.height(), null,
                    job.provider(), flux ? FLUX2_KLEIN_NAME : Objects.toString(o.model(), "Tongyi-MAI/Z-Image-Turbo"), job.prompt(),
                    o.seed(), flux ? AssetStore.LICENCE_FLUX2_KLEIN : AssetStore.LICENCE_ZIMAGE, true));
            jdbc.sql("UPDATE asset SET dream_short_id = :d WHERE id = :a").param("d", id).param("a", asset).update();
            // A new picture makes the old clip stale: it must be made again from this picture.
            jdbc.sql("""
                            UPDATE dream_shot SET picture_asset_id = :a, picture_takes = picture_takes + 1, clip_asset_id = NULL,
                                   error = NULL, updated_at = now() WHERE id = :s
                            """).param("a", asset).param("s", shotId).update();
            if (DreamSvi.on(dream(id))) { // SVI: every beat of this scene grew from this picture, so all are stale
                jdbc.sql("""
                                UPDATE dream_shot b SET clip_asset_id = NULL, updated_at = now()
                                FROM dream_shot s WHERE s.id = :s AND b.dream_short_id = s.dream_short_id AND b.scene_no = s.scene_no
                                """).param("s", shotId).update();
            }
            if (bridged(id)) { // first-last frame: the shot before ends on this picture, so its clip is stale too
                jdbc.sql("""
                                UPDATE dream_shot b SET clip_asset_id = NULL, updated_at = now()
                                FROM dream_shot s WHERE s.id = :s AND b.dream_short_id = s.dream_short_id AND b.ord = s.ord - 1
                                """).param("s", shotId).update();
            }
            ok++;
        }
        if (r.modalGpuSeconds() > 0) {
            cost(id, "modal_l40s", pricing.modalGpuInr("L40S", r.modalGpuSeconds()), r.modalGpuSeconds(), "gpu_seconds", true,
                    "Dream Short pictures (Z-Image, %d)".formatted(ok));
        }
        router.reportBatch(gpu != null ? POD : ZIMAGE, ok, errors.size(), errors.isEmpty() ? null : errors.getFirst());
        if (ok == 0) {
            throw new IllegalStateException("No pictures were made: " + String.join(" | ", errors.stream().filter(Objects::nonNull).toList()));
        }
        staleFinal(id);
        boolean anyFlux = req.jobs().stream().anyMatch(j -> "flux2-klein".equals(j.podModel()));
        return "%d/%d pictures made (%s%s)%s.".formatted(ok, req.jobs().size(), anyFlux ? FLUX2_KLEIN_NAME : "Z-Image",
                gpu != null ? " on a rented pod" : "",
                errors.isEmpty() ? "" : "; " + errors.size() + " failed: " + errors.getFirst());
    }

    // ------------------------------------------------------------------------------------------- CLIPS

    public DreamEstimate clipEstimate(UUID id, List<String> only) {
        List<ClipJob> jobs = clipJobs(id, only);
        boolean svi = DreamSvi.on(dream(id));
        if (svi && this.gpu == null) {
            throw new IllegalStateException("SVI scenes need the rented GPU pod (the GPU broker is off)");
        }
        if (this.gpu != null && !jobs.isEmpty()) {
            double sec = podClipSeconds(id, jobs, svi);
            BigDecimal inr = this.gpu.inr(sec);
            return new DreamEstimate(DreamStep.CLIPS, inr, sec / 60, inr.compareTo(autoApproveInr) > 0, (svi
                    ? "%d SVI beats in %d scene(s) on a rented GPU pod (Wan fp8 + SVI 2.0 Pro, 480p): about ₹%s and %.0f min at the cheapest live price."
                    .formatted(jobs.size(), jobs.stream().mapToInt(ClipJob::scene).distinct().count(), inr.setScale(0, RoundingMode.HALF_UP), Math.ceil(sec / 60))
                    : "%d clips on a rented GPU pod (Wan fp8, %s): about ₹%s and %.0f min at the cheapest live price."
                            .formatted(jobs.size(), dream(id).get("quality"), inr.setScale(0, RoundingMode.HALF_UP), Math.ceil(sec / 60))));
        }
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
        boolean svi = DreamSvi.on(dream(id));
        if (svi && this.gpu == null) {
            throw new IllegalStateException("SVI scenes need the rented GPU pod (the GPU broker is off)");
        }
        if (this.gpu != null) {
            double sec = podClipSeconds(id, jobs, svi);
            BigDecimal inr = this.gpu.inr(sec);
            // rule 6: the routing decision names the clip method (stage DREAM_CLIPS_SVI for SVI scenes)
            route(id, svi ? "DREAM_CLIPS_SVI" : "DREAM_CLIPS", "video.i2v.dream", POD, inr);
            var session = this.gpu.session(id);
            return new ClipRequest(storageId(id), wanApp, wanClass, publicUrl, jobs, 1, sec, inr, "pod", null, null, 0,
                    session.url(), session.readyTimeoutSeconds(), !svi && chained(id), chainGroup(id), !svi && locked(id), svi);
        }
        double gpu = gpuSeconds(jobs, (String) dream(id).get("quality"));
        BigDecimal inr = pricing.modalGpuInr("H100", gpu);
        route(id, "DREAM_CLIPS", "video.i2v.dream", WAN, inr);
        budget.requireGpu(inr, jobs.size() + " Dream Short clips");
        // One GPU, clips in turn: the ~2 min start-up + model load is paid once for the whole Short. When the AI
        // Producer runs this Short by itself, the GPU stays warm a little after each batch so its redo skips a start-up.
        return new ClipRequest(storageId(id), wanApp, wanClass, publicUrl, jobs, 1, gpu, inr.setScale(2, RoundingMode.HALF_UP),
                "wan", null, null, holdSeconds(id), null, 0, chained(id), chainGroup(id), locked(id));
    }

    /** Pod time for these clips. An SVI beat renders like one normal 81-frame clip; the first scene job also fetches the SVI files. */
    double podClipSeconds(UUID id, List<ClipJob> jobs, boolean svi) {
        return this.gpu.clipSeconds(id, jobs.stream().mapToInt(ClipJob::numFrames).sum()) + (svi ? DreamSvi.SETUP_SECONDS : 0);
    }

    /** Whether this Short chains its shots (each clip starts from the last frame of the one before). Off by default. */
    boolean chained(UUID id) {
        return Boolean.TRUE.equals(dream(id).get("chain_shots"));
    }

    /** First-last frame chaining: clip N runs from picture N to picture N+1. Off by default; never with {@link #chained}. */
    boolean bridged(UUID id) {
        return Boolean.TRUE.equals(dream(id).get("chain_bridge"));
    }

    /** Locked scene: one chain through the whole Short with one camera path, one world and one seed (needs chaining, no groups). */
    boolean locked(UUID id) {
        var d = dream(id);
        return Boolean.TRUE.equals(d.get("chain_shots")) && ((Number) d.get("chain_group")).intValue() == 0
                && Boolean.TRUE.equals(d.get("chain_locked"));
    }

    /** Chain length: 0 = one chain through the whole Short, N = chains of N shots (2: shots 1+2 and 3+4 chained). */
    int chainGroup(UUID id) {
        return ((Number) dream(id).get("chain_group")).intValue();
    }

    /** Whether shot {@code ord} (0-based) continues from the clip before it, instead of starting from a picture of its own. */
    static boolean continues(boolean chain, int group, int ord) {
        return chain && ord > 0 && (group <= 0 || ord % group != 0);
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
        // (Unfiltered: nothing is ever added to the owner's words.)
        List<RedoStopRule.Cause> fixes = unfiltered(d) ? List.<RedoStopRule.Cause>of() : jdbc.sql("""
                        SELECT DISTINCT stop_cause FROM producer_decision
                        WHERE dream_short_id = :d AND stage = 'DREAM_CLIPS' AND stop_rule = 'prompt_fix'
                        """).param("d", id).query(String.class).list().stream().map(RedoStopRule::causeByKey)
                .filter(java.util.Objects::nonNull).toList();
        String fixPrompt = fixes.stream().map(c -> " " + c.promptFix()).collect(java.util.stream.Collectors.joining());
        String negative = fixes.isEmpty() ? null
                : fixes.stream().map(RedoStopRule.Cause::negative).collect(java.util.stream.Collectors.joining(", "));
        if (DreamSvi.on(d)) {
            return sviClipJobs(id, wanted, fixPrompt, negative);
        }
        boolean chain = Boolean.TRUE.equals(d.get("chain_shots"));
        int group = chain ? ((Number) d.get("chain_group")).intValue() : 0;
        boolean locked = chain && group == 0 && Boolean.TRUE.equals(d.get("chain_locked"));
        long sceneSeed = sceneSeed(id, d);
        // A continued episode: shot 1 starts from the last frame of the previous episode's last clip (no picture of its own).
        String parentClip = d.get("start_clip_asset_id") == null ? null : assets.uriOf((UUID) d.get("start_clip_asset_id"));
        String before = null; // the clip of the shot before this one, when it already exists (chained shots)
        boolean bridge = Boolean.TRUE.equals(d.get("chain_bridge"));
        List<Map<String, Object>> all = shots(id);
        for (int i = 0; i < all.size(); i++) {
            Map<String, Object> s = all.get(i);
            // First-last frame: this clip ends on the next shot's picture (the last clip, or a missing picture: ends freely).
            String endImage = bridge && i + 1 < all.size() ? (String) all.get(i + 1).get("picture_uri") : null;
            String shotId = s.get("id").toString();
            String clipBefore = before;
            before = (String) s.get("clip_uri");
            int ord = ((Number) s.get("ord")).intValue();
            boolean continues = continues(chain, group, ord);
            boolean fromParent = ord == 0 && parentClip != null;
            if (fromParent) {
                clipBefore = parentClip;
            }
            if (s.get("picture_uri") == null && !continues && !fromParent) {
                continue; // (a chained shot after the first has no picture of its own: it starts from the clip before)
            }
            if (wanted == null ? s.get("clip_asset_id") != null : !wanted.contains(shotId)) {
                continue;
            }
            int take = ((Number) s.get("clip_takes")).intValue() + 1;
            jobs.add(new ClipJob(shotId, ((Number) s.get("ord")).intValue() + 1, take, "dream", "fast",
                    (String) s.get("picture_uri"), fixPrompt.isEmpty() ? (String) s.get("motion_prompt") : s.get("motion_prompt") + fixPrompt, size[0], size[1], frames,
                    (locked ? sceneSeed : Math.abs((long) shotId.hashCode())) + take, negative, continues || fromParent ? clipBefore : null,
                    endImage));
        }
        return jobs;
    }

    /**
     * SVI beats to render, one scene after another. A scene is touched when one of its beats is asked for (a redo) or,
     * with nothing asked, when one has no clip (first run, Retry). It re-renders from that beat to the scene's end: the
     * later beats grew from it, so they would be stale. Earlier beats are kept, and the first beat rendered continues from
     * the clip (and saved latent) of the beat before it. Every beat is 81 frames at 480x832 (the proven SVI recipe) and
     * starts from the scene's one picture (the anchor); a scene without its picture waits.
     */
    List<ClipJob> sviClipJobs(UUID id, Set<String> wanted, String fixPrompt, String negative) {
        List<ClipJob> jobs = new ArrayList<>();
        Map<Integer, List<Map<String, Object>>> scenes = new java.util.LinkedHashMap<>();
        for (Map<String, Object> s : shots(id)) {
            scenes.computeIfAbsent(DreamSvi.sceneOf(s), k -> new ArrayList<>()).add(s);
        }
        for (var e : scenes.entrySet()) {
            List<Map<String, Object>> beats = e.getValue();
            String picture = (String) beats.getFirst().get("picture_uri");
            if (picture == null) {
                continue;
            }
            int from = -1;
            for (int i = 0; i < beats.size() && from < 0; i++) {
                Map<String, Object> s = beats.get(i);
                if (wanted == null ? s.get("clip_asset_id") == null
                        : wanted.contains(s.get("id").toString()) || s.get("clip_asset_id") == null && beats.stream()
                        .anyMatch(b -> wanted.contains(b.get("id").toString()))) {
                    from = i;
                }
            }
            for (int i = Math.max(0, from); from >= 0 && i < beats.size(); i++) {
                Map<String, Object> s = beats.get(i);
                String shotId = s.get("id").toString();
                int take = ((Number) s.get("clip_takes")).intValue() + 1;
                String prompt = fixPrompt.isEmpty() ? (String) s.get("motion_prompt") : s.get("motion_prompt") + fixPrompt;
                jobs.add(new ClipJob(shotId, ((Number) s.get("ord")).intValue() + 1, take, "dream", "fast", picture, prompt,
                        480, 832, DreamSvi.BEAT_FRAMES, Math.abs((long) shotId.hashCode()) + take, negative,
                        i > 0 ? (String) beats.get(i - 1).get("clip_uri") : null, null, e.getKey(), DreamSvi.beatOf(s)));
            }
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
        boolean onPod = "pod".equals(req.provider());
        List<String> errors = new ArrayList<>();
        for (ClipOutput o : r.clips()) {
            ClipJob job = jobs.get(o.shotId() + ":" + o.take());
            if (job == null) {
                continue;
            }
            gpu += o.gpuSeconds();
            if (!onPod && o.gpuSeconds() > 0) {
                evidence.gpuTiming(id, kind, o.frames() > 0 ? o.frames() : job.numFrames(), o.gpuSeconds(), first);
                first = false;
                spent = spent.add(pricing.modalGpuInr("H100", o.gpuSeconds()));
            }
            if (!onPod && o.gpuSeconds() > 0) {
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
                    o.frames() > 0 ? (double) o.frames() / fps : null, onPod ? POD : WAN,
                    (req.svi() ? "wan2.2-i2v-a14b-fp8+svi2.0-pro scene %d beat %d ".formatted(job.scene(), job.beat())
                            : onPod ? "wan2.2-i2v-a14b-fp8 " : "wan2.2-i2v-a14b ") + o.mode(), job.prompt(),
                    job.seed(), AssetStore.LICENCE_WAN, true));
            jdbc.sql("UPDATE asset SET dream_short_id = :d, fps = :f WHERE id = :a").param("d", id).param("f", fps).param("a", asset).update();
            jdbc.sql("UPDATE dream_shot SET clip_asset_id = :a, clip_takes = clip_takes + 1, error = NULL, updated_at = now() WHERE id = :s")
                    .param("a", asset).param("s", shotId).update();
            ok++;
        }
        if (!onPod && r.startUps() > 0) { // observed cold starts: a redo on a warm GPU pays none
            double start = r.startUps() * pricing.gpuContainerStartSeconds();
            cost(id, "modal_h100", pricing.modalGpuInr("H100", start), start, "gpu_seconds", true,
                    "Dream Short: Wan GPU start-up + model load (estimated)");
            spent = spent.add(pricing.modalGpuInr("H100", start));
        }
        if (req.estimateInr() != null && gpu > 0) {
            evidence.costCheck(id, "CLIPS", req.estimateInr().doubleValue(), spent.doubleValue());
        }
        if (!onPod) {
            evidence.recalibrate(kind).forEach(c -> log.info("Clip estimate recalibrated: {}", c));
        }
        router.reportBatch(onPod ? POD : WAN, ok, errors.size(), errors.isEmpty() ? null : errors.getFirst());
        if (ok == 0 && stopped == 0) {
            throw new IllegalStateException("No clips were made: " + String.join(" | ", errors));
        }
        staleFinal(id);
        return "%d/%d clips made (Wan %s, %s%s)%s%s%s.".formatted(ok, req.jobs().size(), onPod ? "fp8 on a rented pod" : "fast", dream(id).get("quality"),
                req.svi() ? ", SVI scenes" : req.chain() ? ", chained" : req.jobs().stream().anyMatch(j -> j.endImageUri() != null) ? ", first-last frame" : "",
                gpu > 0 ? " in %.0f s of GPU time".formatted(gpu) : "",
                stopped > 0 ? "; stopped before " + stopped + " finished" : "",
                errors.isEmpty() ? "" : "; " + errors.size() + " failed: " + errors.getFirst()
                        + (req.chain() ? " (the shots after a failed one wait for it: Retry or redo it, they continue from its last frame)"
                        : req.svi() ? " (Retry re-renders that scene from the failed beat on; earlier beats are kept)" : ""));
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

    // MUSIC lives in DreamMusic; these keep the public API.

    @Transactional
    public com.projectv.media.MediaTypes.MusicRequest musicRequest(UUID id, String notes) {
        return music.musicRequest(id, notes);
    }

    @Transactional
    public String saveMusic(UUID id, com.projectv.media.MediaTypes.MusicResult r) {
        return music.saveMusic(id, r);
    }

    public String musicSource(UUID id) {
        return music.musicSource(id);
    }

    public DreamTypes.DreamBedRequest musicBedRequest(UUID id) {
        return music.musicBedRequest(id);
    }

    @Transactional
    public String saveMusicBed(UUID id, DreamTypes.DreamBedResult r) {
        return music.saveMusicBed(id, r);
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
        boolean svi = DreamSvi.on(d);
        // SVI: beats are cross-faded into one shot per scene first; between scenes a hard cut (default) or a 0.5 s dissolve
        List<Integer> sviScenes = svi ? DreamSvi.beatsPerScene(shots(id)) : null;
        List<Integer> sviFrames = !svi ? null : sviScenes.stream()
                .map(n -> unfiltered(d) ? 0 : Math.min(DreamSvi.framesOf(n), DreamSvi.seconds(d) * DreamSvi.FPS + 1)).toList();
        return new DreamFinishRequest(storageId(id), (String) d.get("look"), clips, 1080, 1920, 24,
                svi ? ("dissolve".equals(d.get("svi_between")) ? 0.5 : 0.0) : look.dissolveSec(),
                musicDir == null || musicDir.isBlank() ? null : musicDir, publicUrl, musicUri, musicName,
                look.speed(), look.handheld(), !Boolean.FALSE.equals(d.get("edit_music")),
                !Boolean.FALSE.equals(d.get("edit_ambience")), !Boolean.FALSE.equals(d.get("edit_pacing")),
                // a picked Scout angle is one story told in order: pacing must not reshuffle it (19 Nov: the ending played third)
                // and so is a path-choice Short: the end card asks about where the LAST shot leaves you
                // and so are SVI scenes: their order is the story
                scouted(d) != null && scouted(d).chosen() != null || pathChoice(d) || svi,
                Boolean.TRUE.equals(d.get("hook_title")) ? blankToNull((String) d.get("hook_text")) : null,
                (String) d.get("hook_color"),
                pathChoice(d) ? blankToNull((String) d.get("path_a")) : null,
                pathChoice(d) ? blankToNull((String) d.get("path_b")) : null, sviScenes, sviFrames);
    }

    /** "Choose the path" is on and both options are written. */
    static boolean pathChoice(Map<String, Object> d) {
        return Boolean.TRUE.equals(d.get("path_choice")) && blankToNull((String) d.get("path_a")) != null
                && blankToNull((String) d.get("path_b")) != null;
    }

    /** The world's seed: inherited down an episode chain (scene_seed), else derived from the Short's id as before. */
    static long sceneSeed(UUID id, Map<String, Object> d) {
        return d.get("scene_seed") != null ? ((Number) d.get("scene_seed")).longValue() : Math.abs((long) id.hashCode());
    }

    /** The writer's continuation context for an episode after the first (empty for every other Short). */
    String continuation(Map<String, Object> d) {
        if (d.get("parent_id") == null) {
            return "";
        }
        Object branch = dream((UUID) d.get("parent_id")).get("branch_json");
        Map<String, Object> memory = branch == null ? Map.of()
                : json.readValue(branch.toString(), new tools.jackson.core.type.TypeReference<Map<String, Object>>() {
                });
        return DreamBranches.forWriter(memory, (String) d.get("chosen_path"), ((Number) d.get("episode")).intValue());
    }

    /**
     * The owner edits the two path options (review or manual step); they apply at the next finish ("Redo from Finish",
     * free) and a rewrite by the writer never replaces them.
     */
    public void setPaths(UUID id, String a, String b) {
        String ca = DreamBranches.cleanPath(a), cb = DreamBranches.cleanPath(b);
        if (ca == null || cb == null) {
            throw new IllegalArgumentException("Write both paths (2-4 words each)");
        }
        jdbc.sql("UPDATE dream_short SET path_a = :a, path_b = :b, path_locked = true, updated_at = now() WHERE id = :id")
                .param("a", ca).param("b", cb).param("id", id).update();
    }

    /**
     * Branch memory, saved when a path-choice Short finishes: what the next episode needs to continue the same world
     * from this one's last frame.
     */
    void saveBranch(UUID id) {
        Map<String, Object> d = dream(id);
        if (!Boolean.TRUE.equals(d.get("path_choice"))) {
            return;
        }
        List<Map<String, Object>> shots = shots(id);
        Map<String, Object> last = shots.isEmpty() ? Map.of() : shots.getLast();
        DreamWorld w = d.get("world_json") == null ? null : json.readValue(d.get("world_json").toString(), DreamWorld.class);
        Map<String, Object> b = new java.util.LinkedHashMap<>();
        b.put("title", d.get("title"));
        if (w != null) {
            b.put("world", w.world());
            b.put("palette", w.palette());
            b.put("light", w.light());
            b.put("hero", w.hero());
            b.put("cameraPath", w.cameraPath());
        }
        b.put("lastScene", last.get("scene"));
        b.put("lastClipAssetId", last.get("clip_asset_id") == null ? null : last.get("clip_asset_id").toString());
        b.put("seed", sceneSeed(id, d));
        b.put("look", d.get("look"));
        b.put("chainShots", d.get("chain_shots"));
        b.put("chainGroup", d.get("chain_group"));
        b.put("chainLocked", d.get("chain_locked"));
        b.put("chainBridge", d.get("chain_bridge"));
        b.put("pathA", d.get("path_a"));
        b.put("pathB", d.get("path_b"));
        b.put("episode", d.get("episode"));
        jdbc.sql("UPDATE dream_short SET branch_json = CAST(:b AS jsonb) WHERE id = :id")
                .param("b", json.writeValueAsString(b)).param("id", id).update();
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s.strip();
    }

    /**
     * The owner edits the hook title (in the review or manual step); it applies at the next finish ("Redo from Finish",
     * free) and a rewrite by the writer never replaces it. Null {@code color} keeps the colour; blank {@code text} clears it.
     */
    public void setHook(UUID id, String text, String color) {
        jdbc.sql("""
                        UPDATE dream_short SET hook_text = :t, hook_color = COALESCE(:c, hook_color), hook_locked = true,
                               updated_at = now() WHERE id = :id
                        """).param("t", blankToNull(text)).param("c", blankToNull(color)).param("id", id).update();
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
        saveBranch(id);
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
        if (!qualityCheck || unfiltered(d) || d.get("final_asset_id") == null) { // the checks judge "no people, feel-good": not the owner's own prompt
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
        if (!qualityCheck || unfiltered(d) || pictures.isEmpty()) {
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
    void route(UUID id, String stage, String capability, String provider, BigDecimal inr) {
        var choice = router.choose(null, stage, capability, List.of(new Router.Candidate(provider, inr)), provider, true);
        tag("routing_decision", choice.decisionId(), id);
    }

    void tag(String table, UUID rowId, UUID dreamId) {
        if (rowId != null) {
            jdbc.sql("UPDATE " + table + " SET dream_short_id = :d WHERE id = :id").param("d", dreamId).param("id", rowId).update();
        }
    }

    void cost(UUID id, String provider, BigDecimal inr, double units, String unitType, boolean estimated, String note) {
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
        // Every status change is kept: the dashboard's step timeline and typical step times come from these.
        evidence.event(id, step, "status", null, message, Map.of("status", status));
        if ("FAILED_WAITING".equals(status)) {
            evidence.event(id, step, "failure", null, message, null);
        }
        releaseGpuIfDone(id, step, status);
        releaseGpuIfNobodyWaits();
    }

    /**
     * Step:status pairs that will use the shared pod soon by themselves: working on (or queued for) a GPU step, on the
     * free steps before the pictures, or in a review window that goes on to a GPU step by itself. Anything waiting for
     * the owner (Manual, cost OK, Retry) or past the clips is not: the pod is not kept on for it.
     */
    static final java.time.Duration COST_OK_GRACE = java.time.Duration.ofMinutes(1);

    /** Every 30 s: a cost OK that has waited past its grace ends the pod even when no status changes. */
    @org.springframework.scheduling.annotation.Scheduled(initialDelay = 60_000, fixedDelay = 30_000)
    void releaseGpuIfNobodyWaitsScheduled() {
        try {
            releaseGpuIfNobodyWaits();
        } catch (RuntimeException e) {
            log.warn("GPU empty check failed: {}", e.getMessage());
        }
    }

    static final List<String> GPU_SOON = List.of("SCOUT:RUNNING", "SCOUT:WAITING_REVIEW", "WRITE:RUNNING",
            "WRITE:WAITING_REVIEW", "PICTURES:RUNNING", "PICTURES:WAITING_BATCH", "PICTURES:WAITING_REVIEW",
            "CLIPS:RUNNING", "CLIPS:WAITING_BATCH");

    /**
     * Shut down at empty (21 Nov 2026): when no Short will use the shared pod soon, it ends now instead of sitting
     * through review windows and the in-pod idle tail (that guard stays the safety net, rule 8). GPU power Manual on
     * any Short using it keeps it on, as before.
     */
    void releaseGpuIfNobodyWaits() {
        if (gpu == null || gpu.usersOnPod() == 0) {
            return;
        }
        // Clips waiting for the owner's cost OK keep the pod on for COST_OK_GRACE (owner, 21 Nov 2026: 1 min max).
        int soon = jdbc.sql("""
                        SELECT count(*) FROM dream_short
                        WHERE ((step || ':' || status) IN (:k) AND updated_at > now() - interval '2 hours')
                           OR (step = 'CLIPS' AND status = 'WAITING_COST' AND updated_at > now() - make_interval(secs => :grace))
                        """).param("k", GPU_SOON).param("grace", COST_OK_GRACE.toSeconds()).query(Integer.class).single();
        if (soon == 0) {
            gpu.releaseAllUnlessHeld("no Short is waiting for GPU work");
        }
    }

    public void startClipBatch(UUID id) {
        clipProgress.startBatch(id);
    }

    /** Steps that come before a Short's pictures (free) and its clips (pictures, on the same pod). */
    private static final List<String> FREE_STEPS = List.of("SCOUT", "WRITE");
    private static final List<String> BEFORE_CLIPS = List.of("SCOUT", "WRITE", "PICTURES");

    /** See {@link DreamActivities#gpuBatchAhead}. Only Shorts that are working right now count (one waiting for the owner never holds others up). */
    public int gpuBatchAhead(UUID id, String step) {
        if (gpu == null) {
            return 0;
        }
        List<String> ahead;
        if ("CLIPS".equals(step)) {
            ahead = BEFORE_CLIPS;
        } else if ("PICTURES".equals(step) && gpu.usersOnPod() == 0) {
            ahead = FREE_STEPS;
        } else {
            return 0;
        }
        return jdbc.sql("""
                        SELECT count(*) FROM dream_short
                        WHERE id <> :id AND status = 'RUNNING' AND step IN (:steps) AND updated_at > now() - interval '2 hours'
                        """).param("id", id).param("steps", ahead).query(Integer.class).single();
    }

    /** Whether another Short is still on its free steps: then the pod is rented when the batch reaches its pictures, not now. */
    private boolean othersStillWriting(UUID id) {
        return jdbc.sql("""
                        SELECT count(*) FROM dream_short
                        WHERE id <> :id AND status = 'RUNNING' AND step IN (:steps) AND updated_at > now() - interval '2 hours'
                        """).param("id", id).param("steps", FREE_STEPS).query(Integer.class).single() > 0;
    }
}
