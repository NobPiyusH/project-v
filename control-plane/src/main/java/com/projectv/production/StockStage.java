package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.media.MediaTypes.StockProvenance;
import com.projectv.media.MediaTypes.StockRequest;
import com.projectv.media.MediaTypes.StockResult;
import com.projectv.planner.VideoPlan;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.core.type.TypeReference;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * Stock footage lane (docs/stock-footage-lane.md), run right after Shot plan so a shot with no match is back on the AI lane
 * before keyframes are made. A found clip is stored like picked NASA footage (shot.footage_asset_id), so keyframes, clip
 * generation, upscale and smoothing skip it and assembly uses it. Everything is off unless projectv.video.stock-lane is true.
 */
@Component
public class StockStage extends StageSupport {

    @Value("${projectv.video.stock-lane:false}")
    private boolean lane;
    @Value("${projectv.video.stock-pexels:false}")
    private boolean pexels;
    @Value("${projectv.video.stock-pixabay:false}")
    private boolean pixabay;
    @Value("${projectv.video.stock-commons:false}")
    private boolean commons;
    @Value("${projectv.video.stock-archive:false}")
    private boolean archive;

    public StockStage(ProductionDeps d) {
        super(d);
    }

    void configureArchive(boolean on) {
        this.archive = on;
    }

    void configure(boolean lane, boolean pexels, boolean pixabay, boolean commons) {
        this.lane = lane;
        this.pexels = pexels;
        this.pixabay = pixabay;
        this.commons = commons;
    }

    public boolean enabled() {
        return lane && !sources().isEmpty();
    }

    public List<String> sources() {
        List<String> s = new ArrayList<>();
        if (pexels) {
            s.add("pexels");
        }
        if (pixabay) {
            s.add("pixabay");
        }
        if (commons) {
            s.add("commons");
        }
        if (archive) {
            s.add("archive");
        }
        return s;
    }

    /** One request per stock shot that has no clip yet; empty when the lane (or every source) is off. */
    public List<StockRequest> requests(UUID videoId) {
        List<StockRequest> out = new ArrayList<>();
        if (!enabled()) {
            return out;
        }
        VideoPlan plan = plan(videoId);
        for (Map<String, Object> s : shots(videoId)) {
            if (!stockShot(s) || s.get("footage_asset_id") != null) {
                continue;
            }
            Map<String, Object> p = prompt(s);
            String text = Objects.toString(p.get("stockQuery"), Objects.toString(s.get("description"), "")).strip();
            double seconds = s.get("duration_target_sec") instanceof Number n ? n.doubleValue() : 4.0;
            out.add(new StockRequest(videoId.toString(), s.get("id").toString(), ((Number) s.get("ord")).intValue() + 1, text,
                    plan.aspectRatio(), seconds, sources(), textModel));
        }
        return out;
    }

    /** A clip: stored with provenance and attached to the shot. No clip: the shot goes back to the AI lane, never fails. */
    @Transactional
    public StageResult save(UUID videoId, StockRequest req, StockResult r) {
        UUID shotId = UUID.fromString(req.shotId());
        Map<String, Object> shot = shots(videoId).stream().filter(s -> shotId.equals(s.get("id"))).findFirst().orElse(null);
        if (shot == null) {
            return StageResult.of("Shot is no longer part of this video.", null);
        }
        Map<String, Object> p = prompt(shot);
        if (r == null || !r.found() || r.uri() == null || r.provenance() == null) {
            String reason = r == null || r.reason() == null ? "no result" : r.reason();
            p.put("lane", "ai_video");
            p.put("stockFallback", reason);
            savePrompt(shot, p);
            return StageResult.of("Shot %d: no stock clip (%s); using the AI lane.".formatted(req.shotNumber(), reason), null);
        }
        StockProvenance pv = r.provenance();
        boolean isCommons = "commons".equals(pv.source());
        UUID licence = isCommons ? AssetStore.LICENCE_COMMONS : "archive".equals(pv.source()) ? AssetStore.LICENCE_ARCHIVE
                : "pexels".equals(pv.source()) ? AssetStore.LICENCE_PEXELS : AssetStore.LICENCE_PIXABAY;
        UUID assetId = assets.create(new NewAsset(videoId, "video", r.uri(), "video/mp4", r.width(), r.height(), r.durationSec(),
                isCommons ? "wikimedia" : "archive".equals(pv.source()) ? "internet_archive" : pv.source(), "stock footage " + pv.source() + " " + pv.sourceId(),
                req.description() + " | queries: " + String.join(", ", r.queries() == null ? List.of() : r.queries()),
                null, licence, false));
        jdbc.sql("""
                        UPDATE provenance SET source_url = :u, source_id = :i, author = :a, attribution_text = :t, credit_required = :c,
                                              fetched_at = CAST(:f AS timestamptz)
                        WHERE asset_id = :asset
                        """).param("u", pv.pageUrl()).param("i", pv.sourceId()).param("a", pv.author()).param("t", pv.attribution())
                .param("c", pv.creditRequired()).param("f", pv.fetchedAt() == null ? OffsetDateTime.now().toString() : pv.fetchedAt())
                .param("asset", assetId).update();
        jdbc.sql("UPDATE asset SET shot_id = :s, clip_kind = 'footage', sha256 = :h WHERE id = :a").param("s", shotId)
                .param("h", pv.sha256() == null || pv.sha256().isBlank() ? null : pv.sha256()).param("a", assetId).update();
        jdbc.sql("UPDATE shot SET footage_asset_id = :a, clip_status = 'READY', clip_error = NULL, updated_at = now() WHERE id = :s")
                .param("a", assetId).param("s", shotId).update();
        p.remove("stockFallback");
        savePrompt(shot, p);
        return StageResult.of("Shot %d: real stock clip from %s (%.1fs).%s".formatted(req.shotNumber(), pv.source(),
                r.durationSec() == null ? 0.0 : r.durationSec(), pv.creditRequired() ? " Credit required (see credits.txt)." : ""), null);
    }

    private Map<String, Object> prompt(Map<String, Object> shot) {
        return json.readValue((String) shot.get("prompt_json"), new TypeReference<LinkedHashMap<String, Object>>() {
        });
    }

    private void savePrompt(Map<String, Object> shot, Map<String, Object> p) {
        jdbc.sql("UPDATE shot SET prompt_json = CAST(:p AS jsonb), updated_at = now() WHERE id = :s")
                .param("p", json.writeValueAsString(p)).param("s", shot.get("id")).update();
    }
}
