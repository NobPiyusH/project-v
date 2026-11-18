package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.ledger.BudgetGuard;
import com.projectv.media.MediaTypes.MusicRequest;
import com.projectv.media.MediaTypes.MusicResult;
import com.projectv.media.MediaTypes.MusicTake;
import com.projectv.planner.VideoPlan;
import com.projectv.routing.Router;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import com.projectv.production.ProductionService.*;

/**
 * Score request and storing the beat-mapped music cue. Lyria (Gemini API) by default; ACE-Step 1.5 on a Modal L4 is
 * an A/B candidate (19 Nov 2026), off until the owner's listening test says it is as good.
 */
@Service
public class MusicStage extends StageSupport {

    static final String LYRIA = "gemini_lyria";
    static final String ACESTEP = "modal_acestep";
    static final String ACESTEP_MODEL = "ACE-Step/Ace-Step1.5 (acestep-v15-turbo + acestep-5Hz-lm-1.7B)";
    static final int ACESTEP_TAKES = 3;

    private final Router router;
    private final String preferredProvider;

    public MusicStage(ProductionDeps d) {
        this(d, null, "lyria");
    }

    @Autowired
    public MusicStage(ProductionDeps d, Router router, @Value("${projectv.music.provider:lyria}") String preferredProvider) {
        super(d);
        this.router = router;
        this.preferredProvider = preferredProvider;
    }

    public MusicRequest musicRequest(UUID videoId, String ownerNotes) {
        VideoPlan plan = plan(videoId);
        StoredScript script = latestScript(videoId).orElse(null);
        double target = plan.durationSec();
        int maxSegments = (int) Math.min(6, Math.ceil(target / 55.0) + 1);
        String brief = script != null && script.musicBrief() != null ? script.musicBrief() : "Cinematic score matching the tone: " + plan.tone();
        String prompt = """
                Instrumental film score, no vocals, no lyrics. About %d seconds long.
                Mood and style: %s
                Video: %s (%s). It must leave room for a narrator's voice: avoid busy melodies in the mid range.
                %s""".formatted(Math.round(target), brief, plan.title(), plan.tone(),
                ownerNotes == null || ownerNotes.isBlank() ? "" : "Owner's notes: " + ownerNotes);
        if (ACESTEP.equals(route(videoId, maxSegments))) {
            budget.requireGpu(pricing.acestepInr(ACESTEP_TAKES), "MUSIC (ACE-Step, " + ACESTEP_TAKES + " takes)");
            // ACE-Step takes a short caption (512 characters at most): the brief first, our fixed rules after.
            String caption = "Instrumental film score, no vocals. " + brief
                    + ". Leaves room for a narrator: no busy mid-range melody."
                    + (ownerNotes == null || ownerNotes.isBlank() ? "" : " " + ownerNotes);
            return new MusicRequest(videoId.toString(), ACESTEP_MODEL, caption, target, 1, ACESTEP, ACESTEP_TAKES,
                    energyCurve(videoId, target));
        }
        budget.require(pricing.musicInr(maxSegments), "MUSIC (" + maxSegments + " Lyria generations at most)");
        return new MusicRequest(videoId.toString(), musicModel, prompt, target, maxSegments);
    }

    /** The router picks the music provider (logged); without a router (tests), Lyria. */
    String route(UUID videoId, int maxSegments) {
        if (router == null) {
            return LYRIA;
        }
        return router.choose(videoId, "MUSIC", "music.score",
                List.of(new Router.Candidate(LYRIA, pricing.musicInr(maxSegments)),
                        new Router.Candidate(ACESTEP, pricing.acestepInr(ACESTEP_TAKES))),
                "acestep".equalsIgnoreCase(preferredProvider) ? ACESTEP : LYRIA, true).providerId();
    }

    /**
     * Shot energy plan, one value 0..1 per second: faster cuts, bigger camera moves and wider shots mean more energy.
     * The shot plan's target durations are stretched to the video's length. Empty when there are no shots yet (music
     * runs before SHOT_PLAN on a first pass; a redo from MUSIC has them).
     */
    List<Double> energyCurve(UUID videoId, double targetSec) {
        List<Map<String, Object>> shots = jdbc.sql("""
                        SELECT sh.duration_target_sec AS dur, sh.camera_move AS move, sh.shot_type AS type
                        FROM shot sh JOIN scene sc ON sc.id = sh.scene_id
                        WHERE sc.video_id = :v ORDER BY sc.ord, sh.ord
                        """).param("v", videoId).query().listOfRows();
        return energyCurve(shots, targetSec);
    }

    static List<Double> energyCurve(List<Map<String, Object>> shots, double targetSec) {
        double total = 0;
        for (Map<String, Object> s : shots) {
            total += dur(s);
        }
        int seconds = (int) Math.round(targetSec);
        if (shots.isEmpty() || total <= 0 || seconds <= 0) {
            return List.of();
        }
        double stretch = targetSec / total;
        double[] curve = new double[seconds];
        double t = 0;
        for (Map<String, Object> s : shots) {
            double d = dur(s);
            double pace = clamp((5.0 - d) / 3.5);  // a 1.5 s cut is full pace, 5 s or longer is calm
            double e = 0.4 * pace + 0.3 * move(String.valueOf(s.get("move"))) + 0.3 * scale(String.valueOf(s.get("type")));
            double end = t + d * stretch;
            for (int i = (int) Math.floor(t); i < Math.min(seconds, (int) Math.ceil(end)); i++) {
                curve[i] = Math.max(curve[i], e);
            }
            t = end;
        }
        List<Double> out = new ArrayList<>(seconds);
        for (double v : curve) {
            out.add(Math.round(v * 1000) / 1000.0);
        }
        return out;
    }

    private static double dur(Map<String, Object> s) {
        Object d = s.get("dur");
        return d instanceof Number n ? n.doubleValue() : 3.0;
    }

    private static double move(String m) {
        return switch (m) {
            case "static" -> 0.1;
            case "drift_left", "drift_right" -> 0.3;
            case "push_in", "pull_out" -> 0.5;
            case "orbit" -> 0.6;
            case "crane_up", "crane_down" -> 0.8;
            case "flythrough" -> 0.9;
            default -> 0.4;
        };
    }

    private static double scale(String type) {
        return switch (type) {
            case "macro", "close_up", "extreme_close_up" -> 0.3;
            case "medium" -> 0.4;
            case "wide" -> 0.7;
            case "extreme_wide" -> 0.9;
            default -> 0.5;
        };
    }

    private static double clamp(double v) {
        return Math.max(0, Math.min(1, v));
    }

    @Transactional
    public StageResult saveMusic(UUID videoId, MusicResult r) {
        boolean ace = ACESTEP.equals(r.provider());
        UUID assetId;
        String what;
        if (ace) {
            int takes = r.takes() == null ? 1 : r.takes().size();
            ledger.record(videoId, ACESTEP, pricing.modalGpuInr("L4", r.gpuSeconds()), BigDecimal.valueOf(r.gpuSeconds()),
                    "gpu_seconds", true, "MUSIC ACE-Step 1.5 on Modal L4 (" + takes + " takes, best energy match; free Modal credit)");
            assetId = assets.create(new NewAsset(videoId, "audio", r.uri(), "audio/wav", null, null, r.durationSec(),
                    ACESTEP, r.model(), null, r.seed(), AssetStore.LICENCE_ACESTEP, true));
            // the other takes are generated assets too (CLAUDE.md rule 6): kept for listening, never used as the cue
            if (r.takes() != null) {
                for (MusicTake t : r.takes()) {
                    if (!t.chosen() && t.uri() != null) {
                        assets.create(new NewAsset(videoId, "audio", t.uri(), "audio/wav", null, null, null, ACESTEP,
                                r.model(), null, t.seed(), AssetStore.LICENCE_ACESTEP, true));
                    }
                }
            }
            what = "ACE-Step, best of %d takes, %.0f s on the GPU".formatted(takes, r.gpuSeconds());
        } else {
            int songs = r.usage() == null ? r.segments() : r.usage().size();
            ledger.record(videoId, BudgetGuard.GEMINI, pricing.musicInr(songs), BigDecimal.valueOf(songs), "song", true,
                    "MUSIC (" + r.model() + ")");
            assetId = assets.create(new NewAsset(videoId, "audio", r.uri(), "audio/wav", null, null, r.durationSec(),
                    "gemini_api", r.model(), null, null, AssetStore.LICENCE_GEMINI, true));
            what = "%d Lyria generation%s".formatted(songs, songs == 1 ? "" : "s");
        }
        if (router != null) {
            router.reportSuccess(ace ? ACESTEP : LYRIA);
        }
        jdbc.sql("DELETE FROM music_cue WHERE video_id = :v").param("v", videoId).update();
        jdbc.sql("""
                        INSERT INTO music_cue (id, video_id, asset_id, bpm, beat_times_json, sections_json)
                        VALUES (:id, :v, :a, :bpm, CAST(:beats AS jsonb), CAST(:sections AS jsonb))
                        """)
                .param("id", UUID.randomUUID()).param("v", videoId).param("a", assetId).param("bpm", r.bpm())
                .param("beats", json.writeValueAsString(r.beatTimes()))
                .param("sections", json.writeValueAsString(Map.of("downbeats", r.downbeatTimes())))
                .update();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("assetId", assetId);
        out.put("bpm", r.bpm());
        out.put("durationSec", r.durationSec());
        out.put("provider", ace ? ACESTEP : LYRIA);
        if (ace && r.takes() != null) {
            out.put("takes", r.takes());
        }
        return StageResult.of("Score %.0fs at %.0f BPM, %d beats (%s).".formatted(r.durationSec(), r.bpm(),
                r.beatTimes().size(), what), json.writeValueAsString(out));
    }
}
