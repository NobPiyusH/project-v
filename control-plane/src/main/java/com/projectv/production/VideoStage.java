package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.media.MediaTypes.ClipEstimate;
import com.projectv.media.MediaTypes.ClipJob;
import com.projectv.media.MediaTypes.ClipOutput;
import com.projectv.media.MediaTypes.ClipRequest;
import com.projectv.media.MediaTypes.ClipResult;
import com.projectv.planner.StylePresets;
import com.projectv.planner.VideoPlan;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.core.type.TypeReference;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * M2b: real moving video, one Wan 2.2 image-to-video clip per shot, started from the shot's approved keyframe.
 * <ul>
 *   <li><b>draft</b>: fast mode (4 steps) at 480p for every shot: cheap, to judge motion;</li>
 *   <li><b>final</b>: hybrid mode (12 steps, full model on the composition expert) at 720p, only for shots whose
 *       draft is approved and not marked "keep the draft". 4K upscaling and 24 fps come in M2c.</li>
 * </ul>
 * Every batch is estimated first; the owner approves it unless it is under the auto-approve amount, and the monthly
 * GPU cap stops it either way. Every clip is an asset with provenance + licence, and every clip a cost entry.
 */
@Component
public class VideoStage extends StageSupport {

    public static final String DRAFT = "draft";
    public static final String FINAL = "final";
    static final int MAX_FRAMES = 81;   // Wan 2.2: ~5 s at 16 fps
    static final int MIN_FRAMES = 33;   // ~2 s: shorter clips lose motion quality
    static final int FPS = 16;
    static final int MAX_CONTAINERS = 4; // = max_containers in workers/modal_apps/wan.py

    private final String wanApp;
    private final String wanClass;
    private final String publicUrl;
    private final BigDecimal autoApproveInr;
    private final int heroTakes;

    public VideoStage(ProductionDeps d,
                      @Value("${projectv.video.modal-app:projectv-wan}") String wanApp,
                      @Value("${projectv.video.modal-class:Wan}") String wanClass,
                      @Value("${projectv.public-url:http://127.0.0.1:8080}") String publicUrl,
                      @Value("${projectv.video.auto-approve-inr:0}") BigDecimal autoApproveInr,
                      @Value("${projectv.video.hero-draft-takes:2}") int heroTakes) {
        super(d);
        this.wanApp = wanApp;
        this.wanClass = wanClass;
        this.publicUrl = publicUrl;
        this.autoApproveInr = autoApproveInr;
        this.heroTakes = Math.max(1, Math.min(4, heroTakes));
    }

    // ------------------------------------------------------------------------------------------- estimate & request

    public ClipEstimate estimate(UUID videoId, String kind, List<UUID> only) {
        List<ClipJob> jobs = jobs(videoId, kind, only);
        double gpu = gpuSeconds(jobs, kind);
        BigDecimal inr = pricing.modalGpuInr("H100", gpu).setScale(2, RoundingMode.HALF_UP);
        double usd = inr.doubleValue() / pricing.fx();
        int containers = containers(kind, jobs.size());
        double wall = jobs.isEmpty() ? 0 : (pricing.gpuContainerStartSeconds() + (gpu - containers * pricing.gpuContainerStartSeconds()) / containers) / 60;
        String what = DRAFT.equals(kind) ? "draft clips (fast, 480p)" : "final clips (hybrid, 720p)";
        String summary = jobs.isEmpty() ? "No shots need " + what + "."
                : "Ready to make %d %s on Modal H100: about ₹%s ($%.2f) of GPU and %.0f min. ₹%s of this month's GPU cap left."
                .formatted(jobs.size(), what, inr.setScale(0, RoundingMode.HALF_UP), usd, Math.ceil(wall),
                        budget.gpuRemaining().max(BigDecimal.ZERO).setScale(0, RoundingMode.HALF_UP));
        return new ClipEstimate(kind, jobs.size(), gpu, inr, usd, wall, !jobs.isEmpty() && inr.compareTo(autoApproveInr) > 0,
                summary);
    }

    /** The batch for the media worker. Refused (non-retryable) when it would break the monthly GPU cap. */
    public ClipRequest clipRequest(UUID videoId, String kind, List<UUID> only) {
        List<ClipJob> jobs = jobs(videoId, kind, only);
        if (jobs.isEmpty()) {
            throw new IllegalStateException(DRAFT.equals(kind) ? "No shots with images to animate"
                    : "No approved drafts need a final render");
        }
        double gpu = gpuSeconds(jobs, kind);
        BigDecimal inr = pricing.modalGpuInr("H100", gpu);
        budget.requireGpu(inr, jobs.size() + " " + kind + " clips");
        return new ClipRequest(videoId.toString(), wanApp, wanClass, publicUrl, jobs, containers(kind, jobs.size()), gpu, inr.setScale(2, RoundingMode.HALF_UP));
    }

    List<ClipJob> jobs(UUID videoId, String kind, List<UUID> only) {
        VideoPlan plan = plan(videoId);
        StylePresets.Preset preset = presets.byIdOrDefault(plan.stylePreset());
        boolean vertical = "9:16".equals(plan.aspectRatio());
        boolean draft = DRAFT.equals(kind);
        int longSide = draft ? 832 : 1280;
        int shortSide = draft ? 480 : 720;
        Set<String> wanted = only == null ? null : new HashSet<>(only.stream().map(UUID::toString).toList());
        List<ClipJob> jobs = new ArrayList<>();
        for (Map<String, Object> s : clipShots(videoId)) {
            String id = String.valueOf(s.get("id"));
            if (wanted != null && !wanted.contains(id)) {
                continue;
            }
            if (s.get("keyframe_uri") == null || s.get("footage_asset_id") != null) {
                continue; // no image to animate, or the owner chose real footage for this shot
            }
            if (!draft && wanted == null && (s.get("draft_clip_asset_id") == null || !Boolean.TRUE.equals(s.get("final_wanted")))) {
                continue; // finals only for shots with a draft that the owner didn't mark "keep the draft"
            }
            Map<String, Object> p = json.readValue((String) s.get("prompt_json"), new TypeReference<Map<String, Object>>() {
            });
            String prompt = String.join(". ", List.of(
                    java.util.Objects.toString(p.get("motionPrompt"), "").strip(),
                    java.util.Objects.toString(s.get("description"), "").strip(),
                    "Visual style: " + preset.visualStyle())).replaceAll("^(\\. )+", "");
            int first = ((Number) s.get(draft ? "draft_takes" : "final_takes")).intValue() + 1;
            // Renders vary like seeds (owner, 17 Nov: one FA3 render "had better motion"): hero shots get several draft
            // takes with different seeds, so the best motion can be picked. Finals render the chosen draft once.
            int takes = draft && "hero".equals(s.get("priority")) ? heroTakes : 1;
            for (int take = first; take < first + takes; take++) {
                jobs.add(new ClipJob(id, ((Number) s.get("ord")).intValue() + 1, take, kind, draft ? "fast" : "hybrid",
                        (String) s.get("keyframe_uri"), prompt, vertical ? shortSide : longSide, vertical ? longSide : shortSide,
                        frames(num(s.get("duration_target_sec"))), Math.abs((long) id.hashCode()) + take));
            }
        }
        return jobs;
    }

    /** 4n+1 frames covering the shot at 16 fps, between ~2 s and Wan's ~5 s limit (longer shots are retimed in M2c). */
    static int frames(double seconds) {
        int n = (int) Math.ceil(seconds * FPS / 4.0) * 4 + 1;
        return Math.max(MIN_FRAMES, Math.min(MAX_FRAMES, n));
    }

    double gpuSeconds(List<ClipJob> jobs, String kind) {
        double perClip81 = pricing.wanSecondsPer81Frames(kind);
        double render = jobs.stream().mapToDouble(j -> perClip81 * j.numFrames() / MAX_FRAMES).sum();
        return render + containers(kind, jobs.size()) * pricing.gpuContainerStartSeconds();
    }

    /**
     * Drafts render in turn on ONE GPU: start-up + model load (~2 min of H100 time) is paid once, and a draft takes
     * only ~12 s, so parallel GPUs would mostly pay for start-ups. Finals (~8 min each) spread over up to 4 GPUs.
     */
    static int containers(String kind, int clips) {
        return clips == 0 ? 0 : DRAFT.equals(kind) ? 1 : Math.min(MAX_CONTAINERS, clips);
    }

    // ------------------------------------------------------------------------------------------- save results

    @Transactional
    public void markClips(UUID videoId, List<UUID> shotIds, String status, String error) {
        for (UUID s : shotIds) {
            jdbc.sql("""
                            UPDATE shot SET clip_status = :st, clip_error = :err, updated_at = now()
                            WHERE id = :s AND scene_id IN (SELECT id FROM scene WHERE video_id = :v)
                            """)
                    .param("st", status).param("err", error).param("s", s).param("v", videoId).update();
        }
    }

    @Transactional
    public StageResult saveClips(UUID videoId, ClipRequest request, ClipResult r) {
        Map<String, ClipJob> jobs = new LinkedHashMap<>();
        request.jobs().forEach(j -> jobs.put(j.shotId() + ":" + j.take(), j));
        java.util.Set<String> activated = new HashSet<>(); // first good take of each shot becomes the one in use
        int ok = 0;
        double gpu = 0;
        List<String> errors = new ArrayList<>();
        int stopped = 0;
        for (ClipOutput o : r.clips()) {
            ClipJob job = jobs.get(o.shotId() + ":" + o.take());
            if (job == null) {
                continue;
            }
            UUID shotId = UUID.fromString(o.shotId());
            gpu += o.gpuSeconds();
            if (o.gpuSeconds() > 0) {
                ledger.record(videoId, "modal_h100", pricing.modalGpuInr("H100", o.gpuSeconds()),
                        BigDecimal.valueOf(o.gpuSeconds()), "gpu_seconds", true,
                        "%s clip shot %d take %d (Wan %s)".formatted(job.kind().toUpperCase(), job.shotNumber(), job.take(), o.mode()));
            }
            if (o.uri() == null) {
                boolean wasStopped = "stopped".equals(o.error());
                stopped += wasStopped ? 1 : 0;
                if (!wasStopped) {
                    errors.add("shot " + job.shotNumber() + ": " + o.error());
                }
                jdbc.sql("UPDATE shot SET clip_status = :st, clip_error = :e, updated_at = now() WHERE id = :s")
                        .param("st", wasStopped ? "STOPPED" : "FAILED").param("e", wasStopped ? null : o.error())
                        .param("s", shotId).update();
                continue;
            }
            UUID assetId = assets.create(new NewAsset(videoId, "video", o.uri(), "video/mp4", job.width(), job.height(),
                    o.fps() > 0 ? (double) o.frames() / o.fps() : null, "modal_wan", "wan2.2-i2v-a14b " + o.mode(),
                    job.prompt(), job.seed(), AssetStore.LICENCE_WAN, true));
            jdbc.sql("UPDATE asset SET shot_id = :s, clip_kind = :k WHERE id = :a")
                    .param("s", shotId).param("k", job.kind()).param("a", assetId).update();
            if (activated.add(o.shotId())) {
                jdbc.sql("UPDATE shot SET " + (DRAFT.equals(job.kind()) ? "draft_clip_asset_id" : "final_clip_asset_id")
                                + " = :a, clip_status = 'READY', clip_error = NULL, updated_at = now() WHERE id = :s")
                        .param("a", assetId).param("s", shotId).update();
            }
            ok++;
        }
        if (r.containers() > 0) {
            double start = r.containers() * pricing.gpuContainerStartSeconds();
            ledger.record(videoId, "modal_h100", pricing.modalGpuInr("H100", start), BigDecimal.valueOf(start), "gpu_seconds",
                    true, "Wan GPU start-up + model load (%d container%s, estimated)".formatted(r.containers(),
                            r.containers() == 1 ? "" : "s"));
        }
        String kind = request.jobs().isEmpty() ? DRAFT : request.jobs().getFirst().kind();
        if (ok == 0 && stopped == 0) {
            throw new IllegalStateException("No " + kind + " clips were made: " + String.join(" | ", errors));
        }
        long multiTake = request.jobs().stream().collect(java.util.stream.Collectors.groupingBy(ClipJob::shotId,
                java.util.stream.Collectors.counting())).values().stream().filter(n -> n > 1).count();
        String takesNote = multiTake > 0 ? "; %d hero shot%s ha%s takes to compare".formatted(multiTake,
                multiTake == 1 ? "" : "s", multiTake == 1 ? "s" : "ve") : "";
        String summary = "%d/%d %s clips (%s) in %.0f s of GPU render time%s%s%s.".formatted(ok, jobs.size(), kind,
                DRAFT.equals(kind) ? "fast, 480p" : "hybrid, 720p", gpu,
                stopped > 0 ? "; stopped by owner before " + stopped + " finished" : "",
                errors.isEmpty() ? "" : "; " + errors.size() + " failed: " + errors.getFirst(), takesNote);
        return StageResult.of(summary, json.writeValueAsString(Map.of("ok", ok, "failed", errors, "stopped", stopped,
                "gpuSeconds", Math.round(gpu))));
    }

    // ------------------------------------------------------------------------------------------- owner actions

    /** false = keep the draft as this shot's clip (no final render). */
    public void setFinalWanted(UUID shotId, boolean wanted) {
        jdbc.sql("UPDATE shot SET final_wanted = :w, updated_at = now() WHERE id = :s").param("w", wanted).param("s", shotId).update();
    }

    /** Switch a shot to any of its clip takes (draft or final). Free: nothing is generated. */
    @Transactional
    public void selectClip(UUID shotId, UUID assetId) {
        String kind = jdbc.sql("SELECT clip_kind FROM asset WHERE id = :a AND shot_id = :s AND kind = 'video'")
                .param("a", assetId).param("s", shotId).query(String.class).optional()
                .orElseThrow(() -> new IllegalStateException("That clip is not a take of this shot"));
        jdbc.sql("UPDATE shot SET " + (FINAL.equals(kind) ? "final_clip_asset_id" : "draft_clip_asset_id")
                + " = :a, updated_at = now() WHERE id = :s").param("a", assetId).param("s", shotId).update();
    }

    /** Every clip take of a shot, newest first. */
    public List<Map<String, Object>> clipVersions(UUID shotId) {
        return jdbc.sql("""
                        SELECT a.id, a.clip_kind AS kind, a.created_at, p.model,
                               (a.id = s.draft_clip_asset_id OR a.id = s.final_clip_asset_id) AS active
                        FROM asset a JOIN shot s ON s.id = a.shot_id LEFT JOIN provenance p ON p.id = a.provenance_id
                        WHERE a.shot_id = :s AND a.kind = 'video' ORDER BY a.created_at DESC
                        """).param("s", shotId).query().listOfRows();
    }

    private List<Map<String, Object>> clipShots(UUID videoId) {
        return jdbc.sql("""
                        SELECT s.id, s.ord, s.duration_target_sec, s.description, s.prompt_json::text AS prompt_json, s.priority,
                               s.draft_clip_asset_id, s.final_clip_asset_id, s.final_wanted, s.footage_asset_id,
                               a.uri_hot AS keyframe_uri,
                               (SELECT count(*) FROM asset x WHERE x.shot_id = s.id AND x.kind = 'video' AND x.clip_kind = 'draft') AS draft_takes,
                               (SELECT count(*) FROM asset x WHERE x.shot_id = s.id AND x.kind = 'video' AND x.clip_kind = 'final') AS final_takes
                        FROM shot s JOIN scene sc ON sc.id = s.scene_id LEFT JOIN asset a ON a.id = s.keyframe_asset_id
                        WHERE sc.video_id = :v ORDER BY s.ord
                        """).param("v", videoId).query().listOfRows();
    }

    private static double num(Object o) {
        return o instanceof Number n ? n.doubleValue() : 0;
    }
}
