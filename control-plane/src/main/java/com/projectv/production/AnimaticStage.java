package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.media.MediaTypes.AnimaticLine;
import com.projectv.media.MediaTypes.AnimaticRequest;
import com.projectv.media.MediaTypes.AnimaticResult;
import com.projectv.media.MediaTypes.AnimaticShot;
import com.projectv.planner.VideoPlan;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.core.type.TypeReference;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import com.projectv.production.ProductionService.*;

/** Animatic request (keyframes + voice + music on the planned timeline) and result. */
@Service
public class AnimaticStage extends StageSupport {

    public AnimaticStage(ProductionDeps d) {
        super(d);
    }

    public AnimaticRequest animaticRequest(UUID videoId) {
        VideoPlan plan = plan(videoId);
        List<Map<String, Object>> shotRows = shots(videoId);
        List<AnimaticShot> shots = new ArrayList<>();
        Map<Integer, Double> lineStart = new LinkedHashMap<>();
        String lastImage = null;
        double end = 0;
        for (int i = 0; i < shotRows.size(); i++) {
            Map<String, Object> s = shotRows.get(i);
            Map<String, Object> p = json.readValue((String) s.get("prompt_json"), new TypeReference<>() {
            });
            double start = ((Number) p.get("startSec")).doubleValue();
            double dur = ((Number) s.get("duration_target_sec")).doubleValue();
            String image = s.get("keyframe_uri") == null ? lastImage : (String) s.get("keyframe_uri");
            if (image == null) {
                continue; // no keyframe at all yet for the opening shots
            }
            lastImage = image;
            shots.add(new AnimaticShot(i + 1, start, dur, image, (String) s.get("overlay_text")));
            if (p.get("lineIndex") instanceof Number n) {
                lineStart.putIfAbsent(n.intValue(), start);
            }
            end = Math.max(end, start + dur);
        }
        if (shots.isEmpty()) {
            throw new IllegalStateException("No keyframes to build an animatic from");
        }
        List<AnimaticLine> narration = new ArrayList<>();
        StoredScript script = latestScript(videoId).orElse(null);
        double tempo = script == null || script.tempo() == null ? 1.0 : script.tempo();
        double prevEnd = 0;
        for (NarrationTiming.Placed p : NarrationTiming.place(script, lineStart)) {
            narration.add(new AnimaticLine(p.line().audioUri(), round(p.startSec()), tempo));
            prevEnd = p.endSec();
        }
        // Never cut words: if the voice runs past the pictures, hold the last shot longer.
        double needed = prevEnd + NarrationTiming.TAIL;
        if (needed > end && !shots.isEmpty()) {
            AnimaticShot lastShot = shots.removeLast();
            shots.add(new AnimaticShot(lastShot.index(), lastShot.startSec(), lastShot.durationSec() + (needed - end),
                    lastShot.imageUri(), lastShot.overlayText()));
            end = needed;
        }
        String musicUri = jdbc.sql("""
                        SELECT a.uri_hot FROM music_cue m JOIN asset a ON a.id = m.asset_id WHERE m.video_id = :v
                        """).param("v", videoId).query(String.class).optional().orElse(null);
        return new AnimaticRequest(videoId.toString(), plan.title(), shots, narration, musicUri, Math.max(end, prevEnd),
                plan.aspectRatio());
    }

    @Transactional
    public StageResult saveAnimatic(UUID videoId, AnimaticResult r) {
        boolean vertical = "9:16".equals(plan(videoId).aspectRatio());
        UUID assetId = assets.create(new NewAsset(videoId, "video", r.uri(), "video/mp4", vertical ? 1080 : 1920,
                vertical ? 1920 : 1080, r.durationSec(),
                "projectv", "ffmpeg animatic", null, null, AssetStore.LICENCE_OWN_RENDER, false));
        jdbc.sql("UPDATE video SET animatic_asset_id = :a, updated_at = now() WHERE id = :v")
                .param("a", assetId).param("v", videoId).update();
        return StageResult.of("Animatic ready: %.0fs, 1080p, keyframes + narration + music. Story and pacing check before any video spend."
                .formatted(r.durationSec()), json.writeValueAsString(Map.of("assetId", assetId, "durationSec", r.durationSec())));
    }
}
