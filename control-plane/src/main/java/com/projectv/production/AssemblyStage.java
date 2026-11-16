package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.media.MediaTypes.AssemblyLine;
import com.projectv.media.MediaTypes.AssemblyRequest;
import com.projectv.media.MediaTypes.AssemblyResult;
import com.projectv.media.MediaTypes.AssemblyShot;
import com.projectv.planner.VideoPlan;
import com.projectv.production.ProductionService.StoredScript;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.core.type.TypeReference;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * M2c part 1, the edit: every shot's chosen clip on the narration timeline, voice + ducked music mastered to -14 LUFS,
 * word-timed captions, and a Premiere package. Runs on the local worker (free). Every shot must have real moving
 * video (owner-picked footage, else its final, else its draft): a shot without one stops the stage before anything is
 * rendered, because a panned still is never acceptable output (CLAUDE.md rule 10).
 */
@Component
public class AssemblyStage extends StageSupport {

    public AssemblyStage(ProductionDeps d) {
        super(d);
    }

    public AssemblyRequest assemblyRequest(UUID videoId) {
        VideoPlan plan = plan(videoId);
        List<Map<String, Object>> rows = shots(videoId);
        if (rows.isEmpty()) {
            throw new IllegalStateException("There is no shot plan yet");
        }
        Map<String, String> uris = clipUris(videoId);
        List<AssemblyShot> shots = new ArrayList<>();
        List<String> missing = new ArrayList<>();
        Map<Integer, Double> lineStart = new LinkedHashMap<>();
        double end = 0;
        for (Map<String, Object> s : rows) {
            int n = ((Number) s.get("ord")).intValue() + 1;
            Map<String, Object> p = json.readValue((String) s.get("prompt_json"), new TypeReference<Map<String, Object>>() {
            });
            double start = p.get("startSec") instanceof Number x ? x.doubleValue() : end;
            double dur = s.get("duration_target_sec") instanceof Number x ? x.doubleValue() : 3.0;
            String kind = s.get("footage_asset_id") != null ? "footage"
                    : s.get("final_clip_asset_id") != null ? "final" : s.get("draft_clip_asset_id") != null ? "draft" : null;
            String assetId = kind == null ? null : String.valueOf(s.get(switch (kind) {
                case "footage" -> "footage_asset_id";
                case "final" -> "final_clip_asset_id";
                default -> "draft_clip_asset_id";
            }));
            if (kind == null || uris.get(assetId) == null) {
                missing.add("shot " + n);
                continue;
            }
            shots.add(new AssemblyShot(n, uris.get(assetId), kind, round(start), round(dur), (String) s.get("overlay_text")));
            if (p.get("lineIndex") instanceof Number li) {
                lineStart.putIfAbsent(li.intValue(), start);
            }
            end = Math.max(end, start + dur);
        }
        if (!missing.isEmpty()) {
            throw new IllegalStateException(missing.size() + " shot" + (missing.size() == 1 ? " has" : "s have")
                    + " no moving video yet (" + String.join(", ", missing.subList(0, Math.min(8, missing.size())))
                    + (missing.size() > 8 ? ", ..." : "") + "). Make their drafts (or pick real footage) first.");
        }
        StoredScript script = latestScript(videoId).orElse(null);
        double tempo = script == null || script.tempo() == null ? 1.0 : script.tempo();
        List<AssemblyLine> narration = new ArrayList<>();
        double speechEnd = 0;
        for (NarrationTiming.Placed p : NarrationTiming.place(script, lineStart)) {
            narration.add(new AssemblyLine(p.line().audioUri(), p.startSec(), tempo, p.line().text(),
                    p.line().durationSec() == null ? 0 : p.line().durationSec()));
            speechEnd = p.endSec();
        }
        // Never cut words: if the voice runs past the pictures, the last shot holds longer.
        double needed = speechEnd + NarrationTiming.TAIL;
        if (needed > end && !shots.isEmpty()) {
            AssemblyShot last = shots.removeLast();
            shots.add(new AssemblyShot(last.n(), last.uri(), last.kind(), last.startSec(),
                    round(last.durationSec() + needed - end), last.overlayText()));
            end = needed;
        }
        String musicUri = jdbc.sql("""
                        SELECT a.uri_hot FROM music_cue m JOIN asset a ON a.id = m.asset_id WHERE m.video_id = :v
                        """).param("v", videoId).query(String.class).optional().orElse(null);
        return new AssemblyRequest(videoId.toString(), plan.title(), plan.aspectRatio(), plan.language(),
                !"none".equals(plan.narration()), shots, narration, musicUri, round(end));
    }

    @Transactional
    public StageResult saveMaster(UUID videoId, AssemblyResult r) {
        UUID master = assets.create(new NewAsset(videoId, "video", r.masterUri(), "video/mp4", r.width(), r.height(),
                r.durationSec(), "projectv", "ffmpeg assembly (rough cut)", null, null, AssetStore.LICENCE_OWN_RENDER, false));
        UUID pack = assets.create(new NewAsset(videoId, "timeline", r.packageUri(), "application/zip", null, null, null,
                "projectv", "OpenTimelineIO FCP7 XML + media", null, null, AssetStore.LICENCE_OWN_RENDER, false));
        UUID captions = r.srtUri() == null ? null : assets.create(new NewAsset(videoId, "subtitle", r.srtUri(),
                "application/x-subrip", null, null, null, "projectv", "Whisper word timings + script text", null, null,
                AssetStore.LICENCE_OWN_RENDER, false));
        jdbc.sql("""
                        UPDATE video SET master_asset_id = :m, master_package_asset_id = :p, master_captions_asset_id = :c,
                                         updated_at = now() WHERE id = :v
                        """).param("m", master).param("p", pack).param("c", captions).param("v", videoId).update();
        long slowed = r.fits().stream().filter(f -> String.valueOf(f.get("fit")).startsWith("slowed")).count();
        long slowMo = r.fits().stream().filter(f -> String.valueOf(f.get("fit")).contains("slow-motion")).count();
        String fits = slowed == 0 ? "" : "; %d shot%s slowed to fill %s slot%s".formatted(slowed, slowed == 1 ? "" : "s",
                slowed == 1 ? "its" : "their", slowed == 1 ? "" : "s") + (slowMo > 0 ? " (" + slowMo + " noticeably)" : "");
        return StageResult.of(("Master ready: %.0fs, %dx%d @ %d fps, voice and music mastered to -14 LUFS, %d caption%s "
                + "(%d lines word-timed by Whisper)%s. Premiere package and SRT included. Part 2 (GPU) adds smooth "
                + "motion and upscaling.").formatted(r.durationSec(), r.width(), r.height(), r.fps(), r.captionChunks(),
                r.captionChunks() == 1 ? "" : "s", r.linesTimedByWhisper(), fits),
                json.writeValueAsString(Map.of("masterAssetId", master, "packageAssetId", pack, "fits", r.fits())));
    }

    private Map<String, String> clipUris(UUID videoId) {
        Map<String, String> out = new LinkedHashMap<>();
        jdbc.sql("SELECT id, uri_hot FROM asset WHERE video_id = :v AND kind = 'video' AND shot_id IS NOT NULL")
                .param("v", videoId).query().listOfRows()
                .forEach(r -> out.put(String.valueOf(r.get("id")), (String) r.get("uri_hot")));
        return out;
    }
}
