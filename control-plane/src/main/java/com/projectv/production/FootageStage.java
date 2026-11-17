package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.media.MediaTypes.FootageCandidate;
import com.projectv.media.MediaTypes.FootageFetchRequest;
import com.projectv.media.MediaTypes.FootageFetchResult;
import com.projectv.media.MediaTypes.FootageQuery;
import com.projectv.media.MediaTypes.FootageSearchRequest;
import com.projectv.media.MediaTypes.FootageSearchResponse;
import com.projectv.media.MediaTypes.FootageSearchResult;
import com.projectv.planner.VideoPlan;
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
 * Free real footage (NASA Image and Video Library) for shots where it shows the line better than a generated clip:
 * the real Sun, Earth from orbit, launches, telescope views. Quality first: the studio only SUGGESTS candidates (the
 * Director proposes a search for suitable shots; the Library match stage runs it for free); the owner picks. A picked
 * clip replaces that shot's generated clip, so no GPU time is spent on it.
 */
@Component
public class FootageStage extends StageSupport {

    public FootageStage(ProductionDeps d) {
        super(d);
    }

    /** Searches for every shot the Director marked with a footage query (or only {@code shotIds}). */
    public FootageSearchRequest searchRequest(UUID videoId, List<UUID> shotIds, String overrideQuery) {
        List<FootageQuery> queries = new ArrayList<>();
        for (Map<String, Object> s : shots(videoId)) {
            UUID id = (UUID) s.get("id");
            if (shotIds != null && !shotIds.contains(id)) {
                continue;
            }
            String q = overrideQuery != null && !overrideQuery.isBlank() ? overrideQuery.strip()
                    : java.util.Objects.toString(prompt(s).get("footageQuery"), "").strip();
            if (!q.isEmpty()) {
                queries.add(new FootageQuery(id.toString(), q));
            }
        }
        return new FootageSearchRequest(queries);
    }

    /** Keeps the candidates on each shot (shown on the dashboard). Free: nothing is downloaded or used yet. */
    @Transactional
    public StageResult saveCandidates(UUID videoId, FootageSearchResponse r) {
        int shotsWith = 0;
        int total = 0;
        for (FootageSearchResult res : r.results()) {
            Map<String, Object> shot = shot(videoId, UUID.fromString(res.shotId()));
            if (shot == null) {
                continue;
            }
            Map<String, Object> p = prompt(shot);
            p.put("footageQuery", res.query());
            p.put("footageCandidates", res.candidates() == null ? List.of() : res.candidates());
            if (res.error() != null) {
                p.put("footageError", res.error());
            } else {
                p.remove("footageError");
            }
            savePrompt(shot, p);
            int n = res.candidates() == null ? 0 : res.candidates().size();
            total += n;
            shotsWith += n > 0 ? 1 : 0;
        }
        String summary = r.results().isEmpty() ? "No shots asked for real footage."
                : "Found %d free real clip%s (NASA, Wikimedia Commons) for %d of %d shots that could use real footage. Pick any to use it instead of a generated clip (free; no GPU)."
                .formatted(total, total == 1 ? "" : "s", shotsWith, r.results().size());
        return StageResult.of(summary, json.writeValueAsString(Map.of("shots", r.results().size(), "candidates", total)));
    }

    public FootageFetchRequest fetchRequest(UUID videoId, UUID shotId, String nasaId, Double startSec) {
        Map<String, Object> shot = shot(videoId, shotId);
        if (shot == null) {
            throw new IllegalStateException("That shot is not part of this video");
        }
        VideoPlan plan = plan(videoId);
        double seconds = shot.get("duration_target_sec") instanceof Number n ? n.doubleValue() : 4.0;
        return new FootageFetchRequest(videoId.toString(), shotId.toString(), ((Number) shot.get("ord")).intValue() + 1,
                nasaId, startSec, seconds, plan.aspectRatio());
    }

    @Transactional
    public StageResult saveFootage(UUID videoId, FootageFetchRequest req, FootageFetchResult r) {
        Map<String, Object> shot = shot(videoId, UUID.fromString(req.shotId()));
        FootageCandidate c = candidates(shot).stream().filter(x -> req.nasaId().equals(x.nasaId())).findFirst().orElse(null);
        boolean commons = req.nasaId().startsWith("commons:");
        String source = commons ? "Wikimedia Commons" : "NASA";
        String credit = commons ? (c == null || c.credit() == null ? "Wikimedia Commons" : c.credit())
                : "NASA" + (c == null || c.credit() == null || c.credit().isBlank() ? "" : " / " + c.credit());
        // Commons: the file's own licence (public domain, CC0 or CC BY) and author are kept in the credit line.
        UUID assetId = assets.create(new NewAsset(videoId, "video", r.uri(), "video/mp4", r.width(), r.height(),
                r.durationSec(), commons ? "wikimedia" : "nasa", source + " " + req.nasaId(),
                (c == null ? req.nasaId() : c.title()) + " | credit: " + credit + " | from " + r.startSec() + "s of " + r.sourceUrl(),
                null, commons ? AssetStore.LICENCE_COMMONS : AssetStore.LICENCE_NASA, false));
        jdbc.sql("UPDATE asset SET shot_id = :s, clip_kind = 'footage' WHERE id = :a")
                .param("s", UUID.fromString(req.shotId())).param("a", assetId).update();
        jdbc.sql("UPDATE shot SET footage_asset_id = :a, clip_status = 'READY', clip_error = NULL, updated_at = now() WHERE id = :s")
                .param("a", assetId).param("s", UUID.fromString(req.shotId())).update();
        String soft = r.upscale() != null && r.upscale() > 1.5
                ? " Note: the source is %dx%d, so this crop is enlarged %.1fx and may look soft.".formatted(r.sourceWidth(),
                r.sourceHeight(), r.upscale()) : "";
        return StageResult.of("Shot %d now uses real %s footage (%.1fs, credit: %s).%s".formatted(req.shotNumber(),
                source, r.durationSec(), credit, soft), json.writeValueAsString(r));
    }

    /** Back to the generated clip for this shot. */
    public void clearFootage(UUID shotId) {
        jdbc.sql("UPDATE shot SET footage_asset_id = NULL, updated_at = now() WHERE id = :s").param("s", shotId).update();
    }

    private Map<String, Object> shot(UUID videoId, UUID shotId) {
        return shots(videoId).stream().filter(s -> shotId.equals(s.get("id"))).findFirst().orElse(null);
    }

    private List<FootageCandidate> candidates(Map<String, Object> shot) {
        if (shot == null) {
            return List.of();
        }
        Object c = prompt(shot).get("footageCandidates");
        return c == null ? List.of() : json.convertValue(c, new TypeReference<List<FootageCandidate>>() {
        });
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
