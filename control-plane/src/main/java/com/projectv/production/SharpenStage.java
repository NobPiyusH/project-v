package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.media.MediaTypes.ClipEstimate;
import com.projectv.media.MediaTypes.SmoothJob;
import com.projectv.media.MediaTypes.SmoothOutput;
import com.projectv.media.MediaTypes.SmoothRequest;
import com.projectv.media.MediaTypes.SmoothResult;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * M2c part 2b, sharpness: every shot's clip in use (its fresh smoothed clip, else its chosen final/draft) is restored to
 * a 1080 px short side by SeedVR2 7B on one H100 (projectv-upscale). NASA footage is left alone. Like smoothing, a
 * sharpened clip remembers the exact clip it was made from, so the edit uses it only while that clip is still in use.
 * Requests and results reuse the smoothing records: same jobs, same per-clip outputs.
 */
@Component
public class SharpenStage extends StageSupport {

    /** H100 seconds per 2-4 s clip in one-batch "fit" mode (18 Nov A/B: 2 clips in 136 s incl. one model load; the
     * old 61-frame batches with padding took ~80-95 s per clip), ~30 s to load the model per group of 8 clips, plus
     * container start and the 30 s idle before it shuts down. Re-check against the next real video. */
    static final double SECONDS_PER_CLIP = 60;
    static final double SECONDS_PER_GROUP = 30;
    static final double START_SECONDS = 90;
    static final int GROUP = 8;

    private final FinishStage finish;
    private final String publicUrl;
    private final BigDecimal autoApproveInr;

    public SharpenStage(ProductionDeps d, FinishStage finish,
                        @Value("${projectv.public-url:http://127.0.0.1:8080}") String publicUrl,
                        @Value("${projectv.video.auto-approve-inr:0}") BigDecimal autoApproveInr) {
        super(d);
        this.finish = finish;
        this.publicUrl = publicUrl;
        this.autoApproveInr = autoApproveInr;
    }

    List<SmoothJob> jobs(UUID videoId) {
        List<SmoothJob> jobs = new ArrayList<>();
        for (Map<String, Object> s : jdbc.sql("""
                        SELECT s.id, s.ord, s.footage_asset_id, s.prompt_json::text AS prompt_json, c.id AS chosen, c.uri_hot AS chosen_uri, c.fps AS chosen_fps,
                               sm.id AS smooth, sm.uri_hot AS smooth_uri, sm.derived_from AS smoothed_from,
                               sh.derived_from AS sharpened_from
                        FROM shot s JOIN scene sc ON sc.id = s.scene_id
                        LEFT JOIN asset c ON c.id = COALESCE(s.final_clip_asset_id, s.draft_clip_asset_id)
                        LEFT JOIN asset sm ON sm.id = s.smooth_clip_asset_id
                        LEFT JOIN asset sh ON sh.id = s.sharp_clip_asset_id
                        WHERE sc.video_id = :v ORDER BY s.ord
                        """).param("v", videoId).query().listOfRows()) {
            Object chosen = s.get("chosen");
            if (s.get("footage_asset_id") != null || diagramShot(s) || chosen == null || s.get("chosen_uri") == null) {
                continue; // real footage and drawn diagrams (native vector render) are left as they are; no clip yet = nothing to sharpen
            }
            boolean smoothFresh = s.get("smooth") != null && chosen.equals(s.get("smoothed_from")) && s.get("smooth_uri") != null;
            Object source = smoothFresh ? s.get("smooth") : chosen;
            if (source.equals(s.get("sharpened_from"))) {
                continue; // already sharpened from exactly the clip in use
            }
            jobs.add(new SmoothJob(String.valueOf(s.get("id")), ((Number) s.get("ord")).intValue() + 1, String.valueOf(source),
                    (String) (smoothFresh ? s.get("smooth_uri") : s.get("chosen_uri")), smoothFresh ? FinishStage.TARGET_FPS
                    : s.get("chosen_fps") instanceof Number f ? f.doubleValue() : 16.0));
        }
        return jobs;
    }

    static double gpuSeconds(int clips) {
        return clips == 0 ? 0 : START_SECONDS + clips * SECONDS_PER_CLIP + Math.ceil(clips / (double) GROUP) * SECONDS_PER_GROUP;
    }

    BigDecimal estimateInr(int clips) {
        return pricing.modalGpuInr("H100", gpuSeconds(clips)).setScale(2, RoundingMode.HALF_UP);
    }

    /**
     * Before smoothing runs, a shot about to be smoothed will need sharpening too (its clip in use changes), so the
     * estimate counts those as well; the request, made after smoothing, sees the real clips.
     */
    public ClipEstimate estimate(UUID videoId) {
        Set<String> shots = new HashSet<>();
        jobs(videoId).forEach(j -> shots.add(j.shotId()));
        finish.jobs(videoId).forEach(j -> shots.add(j.shotId()));
        int n = shots.size();
        BigDecimal inr = estimateInr(n);
        String summary = n == 0 ? "Every clip is already sharp at 1080p (or is real footage)."
                : "Ready to sharpen %d clip%s to 1080p with SeedVR2 on one Modal H100: about ₹%s and %d min. ₹%s of this month's GPU cap left."
                .formatted(n, n == 1 ? "" : "s", inr.setScale(0, RoundingMode.UP), Math.max(1, Math.round(gpuSeconds(n) / 60)),
                        budget.gpuRemaining().max(BigDecimal.ZERO).setScale(0, RoundingMode.HALF_UP));
        return new ClipEstimate("sharp", n, gpuSeconds(n), inr, inr.doubleValue() / pricing.fx(), gpuSeconds(n) / 60,
                n > 0 && inr.compareTo(autoApproveInr) > 0, summary);
    }

    public SmoothRequest request(UUID videoId) {
        List<SmoothJob> jobs = jobs(videoId);
        BigDecimal inr = estimateInr(jobs.size());
        budget.requireGpu(inr, jobs.size() + " clips to sharpen");
        return new SmoothRequest(videoId.toString(), publicUrl, FinishStage.TARGET_FPS, jobs, inr);
    }

    @Transactional
    public StageResult save(UUID videoId, SmoothRequest req, SmoothResult r) {
        int ok = 0;
        double gpu = 0;
        List<String> errors = new ArrayList<>();
        for (SmoothOutput o : r.clips()) {
            gpu += o.gpuSeconds();
            if (o.uri() == null) {
                if (!"stopped".equals(o.error())) {
                    errors.add(o.error());
                }
                continue;
            }
            UUID shot = UUID.fromString(o.shotId());
            UUID asset = assets.create(new NewAsset(videoId, "video", o.uri(), "video/mp4", null, null,
                    o.fps() > 0 ? o.frames() / o.fps() : null, "modal_seedvr2", "SeedVR2 7B fp16 restore to 1080p", null, null,
                    AssetStore.LICENCE_SEEDVR2, true));
            jdbc.sql("UPDATE asset SET shot_id = :s, clip_kind = 'sharp', derived_from = :d WHERE id = :a")
                    .param("s", shot).param("d", UUID.fromString(o.sourceAssetId())).param("a", asset).update();
            jdbc.sql("UPDATE shot SET sharp_clip_asset_id = :a, updated_at = now() WHERE id = :s")
                    .param("a", asset).param("s", shot).update();
            ok++;
        }
        // Group times already include each group's model load; add the container start and the idle tail.
        double billed = gpu + (r.containers() > 0 ? START_SECONDS : 0);
        if (billed > 0) {
            ledger.record(videoId, "modal_h100", pricing.modalGpuInr("H100", billed), BigDecimal.valueOf(billed), "gpu_seconds",
                    true, "SHARPEN %d clips to 1080p (SeedVR2, incl. estimated start-up)".formatted(ok));
        }
        if (ok == 0 && !req.jobs().isEmpty() && !r.stopped()) {
            throw new IllegalStateException("No clips were sharpened: " + String.join(" | ", errors));
        }
        return StageResult.of("%d/%d clips sharpened to 1080p (SeedVR2) in %.0f s of H100 time%s.".formatted(
                ok, req.jobs().size(), gpu, errors.isEmpty() ? "" : "; " + errors.size() + " failed: " + errors.getFirst()),
                json.writeValueAsString(Map.of("ok", ok, "failed", errors)));
    }
}
