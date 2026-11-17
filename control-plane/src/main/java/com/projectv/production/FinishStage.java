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
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * M2c part 2a, smooth motion: every shot's chosen generated clip (final, else draft) is re-timed from Wan's 16 fps to
 * a true 24 fps with Practical-RIFE in-between frames, on one L4 (projectv-finish). NASA footage is already smooth and
 * is left alone. A smoothed clip remembers the exact clip it was made from, so the edit only uses it while that clip
 * is still the one in use; switching takes or redoing a clip simply makes it stale (redo the stage to refresh).
 */
@Component
public class FinishStage extends StageSupport {

    public static final double TARGET_FPS = 24.0;
    /** L4 seconds per output frame (RIFE v4.25 + encode at 480-720p) and the container start, both estimates. */
    static final double SECONDS_PER_FRAME = 0.05;
    static final double START_SECONDS = 45;

    private final String publicUrl;
    private final BigDecimal autoApproveInr;

    public FinishStage(ProductionDeps d, @Value("${projectv.public-url:http://127.0.0.1:8080}") String publicUrl,
                       @Value("${projectv.video.auto-approve-inr:0}") BigDecimal autoApproveInr) {
        super(d);
        this.publicUrl = publicUrl;
        this.autoApproveInr = autoApproveInr;
    }

    List<SmoothJob> jobs(UUID videoId) {
        List<SmoothJob> jobs = new ArrayList<>();
        for (Map<String, Object> s : jdbc.sql("""
                        SELECT s.id, s.ord, s.duration_target_sec, s.footage_asset_id,
                               COALESCE(s.final_clip_asset_id, s.draft_clip_asset_id) AS chosen,
                               c.uri_hot AS chosen_uri, sm.derived_from AS smoothed_from
                        FROM shot s JOIN scene sc ON sc.id = s.scene_id
                        LEFT JOIN asset c ON c.id = COALESCE(s.final_clip_asset_id, s.draft_clip_asset_id)
                        LEFT JOIN asset sm ON sm.id = s.smooth_clip_asset_id
                        WHERE sc.video_id = :v ORDER BY s.ord
                        """).param("v", videoId).query().listOfRows()) {
            Object chosen = s.get("chosen");
            if (s.get("footage_asset_id") != null || chosen == null || s.get("chosen_uri") == null) {
                continue; // real footage is already smooth; no clip yet = nothing to smooth
            }
            if (chosen.equals(s.get("smoothed_from"))) {
                continue; // already smoothed from exactly this clip
            }
            jobs.add(new SmoothJob(String.valueOf(s.get("id")), ((Number) s.get("ord")).intValue() + 1, String.valueOf(chosen),
                    (String) s.get("chosen_uri"), 16.0));
        }
        return jobs;
    }

    BigDecimal estimateInr(List<SmoothJob> jobs) {
        if (jobs.isEmpty()) {
            return BigDecimal.ZERO;
        }
        // Output frames ≈ 5 s × 24 per clip at most; drafts are usually shorter.
        double seconds = START_SECONDS + jobs.size() * 5 * TARGET_FPS * SECONDS_PER_FRAME;
        return pricing.modalGpuInr("L4", seconds).setScale(2, RoundingMode.HALF_UP);
    }

    public ClipEstimate estimate(UUID videoId) {
        List<SmoothJob> jobs = jobs(videoId);
        BigDecimal inr = estimateInr(jobs);
        String summary = jobs.isEmpty() ? "Every clip is already smooth (or is real footage)."
                : "Ready to smooth %d clip%s to 24 fps on one Modal L4: about ₹%s and %d min. ₹%s of this month's GPU cap left."
                .formatted(jobs.size(), jobs.size() == 1 ? "" : "s", inr.setScale(0, RoundingMode.UP),
                        Math.max(1, Math.round((START_SECONDS + jobs.size() * 6) / 60)),
                        budget.gpuRemaining().max(BigDecimal.ZERO).setScale(0, RoundingMode.HALF_UP));
        return new ClipEstimate("smooth", jobs.size(), 0, inr, inr.doubleValue() / pricing.fx(), 0,
                !jobs.isEmpty() && inr.compareTo(autoApproveInr) > 0, summary);
    }

    public SmoothRequest request(UUID videoId) {
        List<SmoothJob> jobs = jobs(videoId);
        BigDecimal inr = estimateInr(jobs);
        budget.requireGpu(inr, jobs.size() + " clips to smooth");
        return new SmoothRequest(videoId.toString(), publicUrl, TARGET_FPS, jobs, inr);
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
                    o.fps() > 0 ? o.frames() / o.fps() : null, "modal_rife", "Practical-RIFE v4.25 16->24 fps", null, null,
                    AssetStore.LICENCE_RIFE, true));
            jdbc.sql("UPDATE asset SET shot_id = :s, clip_kind = 'smooth', derived_from = :d WHERE id = :a")
                    .param("s", shot).param("d", UUID.fromString(o.sourceAssetId())).param("a", asset).update();
            jdbc.sql("UPDATE shot SET smooth_clip_asset_id = :a, updated_at = now() WHERE id = :s")
                    .param("a", asset).param("s", shot).update();
            ok++;
        }
        double billed = gpu + (r.containers() > 0 ? START_SECONDS : 0);
        if (billed > 0) {
            ledger.record(videoId, "modal_l4", pricing.modalGpuInr("L4", billed), BigDecimal.valueOf(billed), "gpu_seconds",
                    true, "SMOOTH %d clips to 24 fps (RIFE, incl. estimated start-up)".formatted(ok));
        }
        if (ok == 0 && !req.jobs().isEmpty() && !r.stopped()) {
            throw new IllegalStateException("No clips were smoothed: " + String.join(" | ", errors));
        }
        return StageResult.of("%d/%d clips smoothed to a true 24 fps (RIFE in-between frames) in %.0f s of L4 time%s.".formatted(
                ok, req.jobs().size(), gpu, errors.isEmpty() ? "" : "; " + errors.size() + " failed: " + errors.getFirst()),
                json.writeValueAsString(Map.of("ok", ok, "failed", errors)));
    }
}
