package com.projectv.production;

import com.projectv.agents.AgentTypes.Concept;
import com.projectv.agents.AgentTypes.PlannedShot;
import com.projectv.agents.AgentTypes.ShotPlan;
import com.projectv.agents.DirectorAgent.TimedLine;
import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.ledger.BudgetGuard;
import com.projectv.media.MediaTypes.KeyframeJob;
import com.projectv.media.MediaTypes.KeyframeOutput;
import com.projectv.media.MediaTypes.KeyframeRequest;
import com.projectv.media.MediaTypes.KeyframeResult;
import com.projectv.planner.StylePresets;
import com.projectv.planner.VideoPlan;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.core.type.TypeReference;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import com.projectv.production.ProductionService.*;

/** Shot plan (narration-anchored timing), keyframes, and per-shot redo/versions. */
@Service
public class ShotStage extends StageSupport {

    /** Image and video models can't spell (a planned "text overlay" came out as "NO KO"); words a viewer must read are
     * overlayText, drawn by the edit. Every keyframe prompt ends with this. */
    static final String NO_WRITING = ". No text, letters, numbers, logos or writing anywhere in the picture";

    public ShotStage(ProductionDeps d) {
        super(d);
    }

    @Transactional
    public StageResult shotPlan(UUID videoId, String ownerNotes) {
        VideoPlan plan = plan(videoId);
        Concept concept = latestConcept(videoId).orElseThrow(() -> new IllegalStateException("No concept yet"));
        StoredScript script = latestScript(videoId).orElseThrow(() -> new IllegalStateException("No script yet"));
        List<Double> beats = beatTimes(videoId);
        double bpm = bpm(videoId);
        double total = plan.durationSec();
        List<TimedLine> timed = script.lines().stream()
                .map(l -> new TimedLine(l.index(), l.text(), l.spoken(), l.durationSec() != null ? l.durationSec() : 6.0))
                .toList();
        ShotPlan sp = director.plan(videoId, plan, concept, timed, bpm, total, ownerNotes);
        if (sp.shots() == null || sp.shots().isEmpty()) {
            throw new IllegalStateException("The director returned no shots");
        }
        boolean isShort = "short".equals(plan.format());

        // Narration drives the timeline: fixed start per line from the real voice lengths (no drift, nothing cut).
        NarrationTiming.Result timing = NarrationTiming.plan(script.lines().stream()
                .map(l -> new NarrationTiming.Line(l.index(), l.spoken() && l.durationSec() != null,
                        l.durationSec() == null ? 0 : l.durationSec()))
                .toList(), total, isShort);
        total = timing.totalSec();
        java.util.SortedMap<Integer, Double> anchors = new java.util.TreeMap<>();
        java.util.Set<Integer> seenLines = new java.util.HashSet<>();
        for (int i = 0; i < sp.shots().size(); i++) {
            Integer li = sp.shots().get(i).lineIndex();
            if (li != null && seenLines.add(li) && timing.starts().containsKey(li)) {
                // Cut to the line's shot a moment before its first word.
                anchors.put(i, Math.max(0, timing.starts().get(li) - NarrationTiming.LEAD));
            }
        }
        List<double[]> times = ShotTiming.fitAnchored(sp.shots().stream().map(PlannedShot::durationSec).toList(), anchors,
                total, beats, isShort ? 1.2 : 2.5);
        StoredScript locked = lockScript(videoId);
        Map<String, Double> starts = new LinkedHashMap<>();
        timing.starts().forEach((k, v) -> starts.put(String.valueOf(k), v));
        writeScript(videoId, new StoredScript(locked.version(), locked.lines(), locked.voiceName(), locked.voiceStyle(),
                locked.musicBrief(), starts, timing.tempo()));

        // Hero shots get the premium (paid) image model; models over-assign them, so enforce ≤20% in code.
        // The opening and the final shot keep hero status first, then the director's picks in order.
        int heroCap = Math.max(1, (int) Math.round(sp.shots().size() * 0.20));
        java.util.Set<Integer> heroIdx = new java.util.LinkedHashSet<>();
        int last = sp.shots().size() - 1;
        for (int i : new int[]{0, last}) {
            if ("hero".equals(sp.shots().get(i).priority()) && heroIdx.size() < heroCap) {
                heroIdx.add(i);
            }
        }
        for (int i = 0; i < sp.shots().size() && heroIdx.size() < heroCap; i++) {
            if ("hero".equals(sp.shots().get(i).priority())) {
                heroIdx.add(i);
            }
        }

        jdbc.sql("DELETE FROM scene WHERE video_id = :v").param("v", videoId).update();
        UUID sceneId = null;
        String sceneTitle = null;
        int sceneOrd = 0;
        int heroes = 0;
        for (int i = 0; i < sp.shots().size(); i++) {
            PlannedShot s = sp.shots().get(i);
            String title = s.sceneTitle() == null || s.sceneTitle().isBlank() ? "Scene" : s.sceneTitle();
            if (sceneId == null || !title.equals(sceneTitle)) {
                sceneId = UUID.randomUUID();
                sceneTitle = title;
                jdbc.sql("INSERT INTO scene (id, video_id, ord, title) VALUES (:id, :v, :o, :t)")
                        .param("id", sceneId).param("v", videoId).param("o", sceneOrd++).param("t", title).update();
            }
            String priority = heroIdx.contains(i) ? "hero" : "standard";
            if (priority.equals("hero")) {
                heroes++;
            }
            Map<String, Object> prompt = new LinkedHashMap<>();
            prompt.put("keyframePrompt", s.keyframePrompt());
            prompt.put("motionPrompt", s.motionPrompt());
            prompt.put("lineIndex", s.lineIndex());
            if (s.footageQuery() != null && !s.footageQuery().isBlank()) {
                prompt.put("footageQuery", s.footageQuery().strip());
            }
            prompt.put("startSec", round(times.get(i)[0]));
            jdbc.sql("""
                            INSERT INTO shot (id, scene_id, ord, duration_target_sec, shot_type, camera_move, description,
                                              prompt_json, priority, overlay_text)
                            VALUES (:id, :scene, :ord, :dur, :type, :move, :desc, CAST(:prompt AS jsonb), :prio, :overlay)
                            """)
                    .param("id", UUID.randomUUID()).param("scene", sceneId).param("ord", i)
                    .param("dur", round(times.get(i)[1])).param("type", s.shotType()).param("move", s.cameraMove())
                    .param("desc", s.description()).param("prompt", json.writeValueAsString(prompt))
                    .param("prio", priority).param("overlay", s.overlayText() == null || s.overlayText().isBlank() ? null : s.overlayText())
                    .update();
        }
        return StageResult.of("%d shots in %d scenes over %.0fs (%d hero); every narration line starts on its own shot, other cuts on the %.0f BPM beat grid.%s"
                .formatted(sp.shots().size(), sceneOrd, total, heroes, bpm,
                        timing.warning() == null ? "" : " " + timing.warning()), json.writeValueAsString(sp));
    }


    public KeyframeRequest keyframeRequest(UUID videoId, String ownerNotes) {
        return buildKeyframeRequest(videoId, null, ownerNotes, ImagePolicy.AUTO);
    }

    /** Per-shot redo: only the given shots, with the owner's notes and image policy. */
    public KeyframeRequest shotKeyframeRequest(UUID videoId, List<UUID> shotIds, String ownerNotes, ImagePolicy policy) {
        KeyframeRequest r = buildKeyframeRequest(videoId, new java.util.HashSet<>(shotIds), ownerNotes, policy);
        long premium = r.jobs().stream().filter(j -> "gemini_api".equals(j.provider())).count();
        budget.require(pricing.imageInr("1K").multiply(BigDecimal.valueOf(premium)), "SHOT REDO (" + premium + " premium images)");
        return r;
    }

    private KeyframeRequest buildKeyframeRequest(UUID videoId, java.util.Set<UUID> only, String ownerNotes, ImagePolicy policy) {
        VideoPlan plan = plan(videoId);
        StylePresets.Preset preset = presets.byIdOrDefault(plan.stylePreset());
        boolean vertical = "9:16".equals(plan.aspectRatio());
        int width = vertical ? 768 : 1344;
        int height = vertical ? 1344 : 768;
        BigDecimal perImage = pricing.imageInr("1K");
        BigDecimal available = budget.geminiRemaining().subtract(pricing.geminiReserveInr());
        List<KeyframeJob> jobs = new ArrayList<>();
        int attempt = nextKeyframeAttempt(videoId);
        for (Map<String, Object> shot : shots(videoId)) {
            UUID id = (UUID) shot.get("id");
            if (only != null && !only.contains(id)) {
                continue;
            }
            Map<String, Object> p = json.readValue((String) shot.get("prompt_json"), new TypeReference<>() {
            });
            String prompt = p.get("keyframePrompt") + ". Visual style: " + preset.visualStyle() + NO_WRITING
                    + (ownerNotes == null || ownerNotes.isBlank() ? "" : ". Director's notes: " + ownerNotes);
            boolean wantPremium = switch (policy) {
                case PREMIUM -> true;
                case FREE -> false;
                case AUTO -> "hero".equals(shot.get("priority"));
            };
            String provider = "modal_zimage";
            if (wantPremium && (policy == ImagePolicy.PREMIUM || available.compareTo(perImage) >= 0)) {
                provider = "gemini_api";
                available = available.subtract(perImage);
            }
            String shotId = id.toString();
            jobs.add(new KeyframeJob(shotId, attempt, provider, prompt, width, height, plan.aspectRatio(), "1K",
                    Math.abs((long) shotId.hashCode()) + attempt));
        }
        if (jobs.isEmpty()) {
            throw new IllegalStateException(only == null ? "No shots to render; run the shot plan first" : "None of those shots belong to this video");
        }
        return new KeyframeRequest(videoId.toString(), imageModel, modalApp, modalClass, jobs);
    }

    /** Status shown on each shot card while a redo runs (REDOING / READY / FAILED). */
    @Transactional
    public void markShots(UUID videoId, List<UUID> shotIds, String status, String error) {
        for (UUID s : shotIds) {
            jdbc.sql("""
                            UPDATE shot SET status = :st, error = :err, updated_at = now()
                            WHERE id = :s AND scene_id IN (SELECT id FROM scene WHERE video_id = :v)
                            """)
                    .param("st", status).param("err", error).param("s", s).param("v", videoId).update();
        }
    }

    /** Owner edits a shot's image prompt directly; the next redo uses it (no AI rewrite involved). */
    @Transactional
    public void editShotPrompt(UUID shotId, String keyframePrompt) {
        String current = jdbc.sql("SELECT prompt_json::text FROM shot WHERE id = :s").param("s", shotId)
                .query(String.class).optional().orElseThrow(() -> new java.util.NoSuchElementException("No shot " + shotId));
        Map<String, Object> p = json.readValue(current, new TypeReference<>() {
        });
        p.put("keyframePrompt", keyframePrompt.strip());
        p.put("editedByOwner", true);
        jdbc.sql("UPDATE shot SET prompt_json = CAST(:p AS jsonb), updated_at = now() WHERE id = :s")
                .param("p", json.writeValueAsString(p)).param("s", shotId).update();
    }

    /** All keyframe versions ever made for a shot, newest first. */
    public List<Map<String, Object>> shotVersions(UUID shotId) {
        return jdbc.sql("""
                        SELECT a.id, a.created_at, p.provider, p.model, (a.id = s.keyframe_asset_id) AS active
                        FROM asset a JOIN shot s ON s.id = a.shot_id LEFT JOIN provenance p ON p.id = a.provenance_id
                        WHERE a.shot_id = :s AND a.kind = 'image' ORDER BY a.created_at DESC
                        """).param("s", shotId).query().listOfRows();
    }

    /** Switch a shot back (or forward) to any of its versions. Free: nothing is generated. */
    @Transactional
    public void selectKeyframe(UUID shotId, UUID assetId) {
        int n = jdbc.sql("UPDATE shot SET keyframe_asset_id = :a, status = 'READY', error = NULL, updated_at = now() WHERE id = :s AND EXISTS (SELECT 1 FROM asset WHERE id = :a AND shot_id = :s)")
                .param("a", assetId).param("s", shotId).update();
        if (n == 0) {
            throw new IllegalStateException("That image is not a version of this shot");
        }
    }

    public UUID videoOfShot(UUID shotId) {
        return jdbc.sql("SELECT sc.video_id FROM shot s JOIN scene sc ON sc.id = s.scene_id WHERE s.id = :s")
                .param("s", shotId).query(UUID.class).optional()
                .orElseThrow(() -> new java.util.NoSuchElementException("No shot " + shotId));
    }

    @Transactional
    public StageResult saveKeyframes(UUID videoId, KeyframeRequest request, KeyframeResult r) {
        Map<String, KeyframeJob> jobs = new LinkedHashMap<>();
        request.jobs().forEach(j -> jobs.put(j.shotId(), j));
        int ok = 0;
        int gem = 0;
        List<String> errors = new ArrayList<>();
        for (KeyframeOutput o : r.results()) {
            if (o.uri() == null) {
                errors.add(o.error());
                jdbc.sql("UPDATE shot SET status = 'FAILED', error = :e, updated_at = now() WHERE id = :s")
                        .param("e", o.error()).param("s", UUID.fromString(o.shotId())).update();
                continue;
            }
            KeyframeJob job = jobs.get(o.shotId());
            boolean isGemini = "gemini_api".equals(o.provider());
            UUID assetId = assets.create(new NewAsset(videoId, "image", o.uri(), o.uri().endsWith(".png") ? "image/png" : "image/jpeg",
                    job.width(), job.height(), null, o.provider(), o.model(), job.prompt(), o.seed(),
                    isGemini ? AssetStore.LICENCE_GEMINI : AssetStore.LICENCE_ZIMAGE, true));
            jdbc.sql("UPDATE asset SET shot_id = :s WHERE id = :a").param("s", UUID.fromString(o.shotId())).param("a", assetId).update();
            jdbc.sql("UPDATE shot SET keyframe_asset_id = :a, status = 'READY', error = NULL, updated_at = now() WHERE id = :s")
                    .param("a", assetId).param("s", UUID.fromString(o.shotId())).update();
            if (isGemini) {
                gem++;
                ledger.record(videoId, BudgetGuard.GEMINI, pricing.imageInr("1K"), BigDecimal.ONE, "image", true,
                        "KEYFRAME hero (" + o.model() + ")");
            }
            ok++;
        }
        if (r.modalGpuSeconds() > 0) {
            ledger.record(videoId, "modal_l40s", pricing.modalGpuInr("L40S", r.modalGpuSeconds()),
                    BigDecimal.valueOf(r.modalGpuSeconds()), "gpu_seconds", true,
                    "KEYFRAMES Z-Image (free Modal credit; excludes container start-up)");
        }
        if (ok == 0) {
            throw new IllegalStateException("No keyframes were produced: " + String.join(" | ", errors));
        }
        String summary = "%d/%d keyframes (%d hero via Nano Banana, %d via Z-Image on Modal)%s.".formatted(ok, jobs.size(), gem,
                ok - gem, errors.isEmpty() ? "" : "; " + errors.size() + " failed: " + errors.getFirst());
        return StageResult.of(summary, json.writeValueAsString(Map.of("ok", ok, "failed", errors)));
    }
}
