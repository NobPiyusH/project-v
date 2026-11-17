package com.projectv.production;

import com.projectv.agents.AgentTypes.ActCritique;
import com.projectv.agents.AgentTypes.Concept;
import com.projectv.agents.AgentTypes.CreativeReview;
import com.projectv.agents.AgentTypes.ShotCritique;
import com.projectv.agents.AgentTypes.VideoCritique;
import com.projectv.agents.CreativeDirectorAgent;
import com.projectv.ledger.BudgetGuard;
import com.projectv.media.MediaTypes.Finding;
import com.projectv.media.MediaTypes.ImageReviewRequest;
import com.projectv.media.MediaTypes.ImageReviewResult;
import com.projectv.media.MediaTypes.ReviewLine;
import com.projectv.media.MediaTypes.ReviewShot;
import com.projectv.planner.StylePresets;
import com.projectv.planner.VideoPlan;
import com.projectv.production.ProductionService.StoredLine;
import com.projectv.production.ProductionService.StoredScript;
import com.projectv.workflow.Stage;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.core.type.TypeReference;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * The Creative Director: checks that the pictures tell the story, at three levels (each shot against the meaning of
 * its line, each paragraph/act as a sequence, the whole video as an arc). Runs twice before any video money is spent:
 * <ul>
 *   <li>after the SHOT_PLAN (text only): weak shot prompts are rewritten on the spot (free); a paragraph or
 *       whole-video problem sends the shot plan back once with the reasons;</li>
 *   <li>after the KEYFRAMES (a vision model looks at every image with its line, paragraph and neighbours): weak
 *       images get a better prompt and only those shots are redone; still weak after that = the owner decides.</li>
 * </ul>
 * Its findings flow through the Inspector, so FAIL handling, storage and the dashboard work the same way.
 */
@Component
public class CreativeDirector extends StageSupport {

    /** Scores at or below this are a FAIL; one above is a WARN. */
    static final int FAIL_AT = 2;
    static final int WARN_AT = 3;

    private final CreativeDirectorAgent agent;
    private final String visionModel;

    public CreativeDirector(ProductionDeps d, CreativeDirectorAgent agent,
                            @Value("${projectv.models.vision}") String visionModel) {
        super(d);
        this.agent = agent;
        this.visionModel = visionModel;
    }

    // ------------------------------------------------------------------------------------------- shot plan (text)

    /** Reviews the shot plan, rewrites weak shot prompts in place, stores the report card, returns findings. */
    public List<Finding> reviewShotPlan(UUID videoId, int attempt) {
        List<Map<String, Object>> shots = shots(videoId);
        if (shots.isEmpty()) {
            return List.of();
        }
        CreativeReview review = agent.reviewShotPlan(videoId, context(videoId, shots));
        store(videoId, Stage.SHOT_PLAN, attempt, textModel.split(",")[0].strip(), review);
        Map<Integer, Map<String, Object>> byN = byNumber(shots);
        Set<Integer> rewritten = new HashSet<>();
        for (ShotCritique c : safe(review.shots())) {
            Map<String, Object> shot = byN.get(c.shot());
            if (shot != null && needsFix(c) && !blank(c.fixPrompt()) && rewritePrompt(shot, c.fixPrompt(), false)) {
                rewritten.add(c.shot());
            }
        }
        return findings(review, byN, false, rewritten);
    }

    // ------------------------------------------------------------------------------------------- images (vision)

    /** What the media worker's vision review needs: every image with its line, paragraph and timing. */
    public ImageReviewRequest imageReviewRequest(UUID videoId) {
        // A review sends ~25 small images + text: well under ₹5 on Gemini Flash. Refuse only when the cap is hit.
        budget.require(BigDecimal.valueOf(5), "CREATIVE_REVIEW images");
        VideoPlan plan = plan(videoId);
        StylePresets.Preset preset = presets.byIdOrDefault(plan.stylePreset());
        Concept concept = latestConcept(videoId).orElse(null);
        StoredScript script = latestScript(videoId).orElse(null);
        Map<Integer, StoredLine> lines = lines(script);
        Map<String, Double> starts = script == null || script.lineStarts() == null ? Map.of() : script.lineStarts();
        List<ReviewLine> reviewLines = lines.values().stream()
                .map(l -> new ReviewLine(l.index(), l.act(), l.text(), starts.getOrDefault(String.valueOf(l.index()), 0.0)))
                .toList();
        List<ReviewShot> reviewShots = new ArrayList<>();
        for (Map<String, Object> s : shots(videoId)) {
            if (s.get("keyframe_uri") == null) {
                continue;
            }
            Map<String, Object> p = prompt(s);
            Integer li = p.get("lineIndex") instanceof Number n ? n.intValue() : null;
            reviewShots.add(new ReviewShot(n(s), String.valueOf(s.get("id")), li,
                    li != null && lines.containsKey(li) ? lines.get(li).act() : null,
                    p.get("startSec") instanceof Number n2 ? n2.doubleValue() : 0, num(s.get("duration_target_sec")),
                    (String) s.get("description"), (String) p.get("keyframePrompt"), (String) s.get("priority"),
                    (String) s.get("keyframe_uri")));
        }
        return new ImageReviewRequest(videoId.toString(), visionModel, CreativeDirectorAgent.RUBRIC, plan.title(),
                plan.aspectRatio(),
                preset.visualStyle(), concept == null ? null : concept.logline(), concept == null ? null : concept.bigIdea(),
                concept == null ? null : concept.emotionalArc(), script == null ? null : script.musicBrief(),
                reviewLines, reviewShots);
    }

    /** Records the review's cost, stores the report card, keeps suggested prompts on weak shots, returns findings. */
    @Transactional
    public List<Finding> saveImageReview(UUID videoId, int attempt, ImageReviewResult r) {
        if (r == null || r.review() == null) {
            return List.of(new Finding("WARN", "CREATIVE_REVIEW_UNAVAILABLE",
                    "The Creative Director could not review the images this time.", "images"));
        }
        if (r.usage() != null && r.usage().totalTokens() > 0) {
            ledger.record(videoId, BudgetGuard.GEMINI, pricing.textInr(r.usage()), BigDecimal.valueOf(r.usage().totalTokens()),
                    "tokens", true, "CREATIVE_REVIEW images (" + r.model() + ", " + r.imagesSent() + " images)");
        }
        store(videoId, Stage.KEYFRAMES, attempt, r.model(), r.review());
        Map<Integer, Map<String, Object>> byN = byNumber(shots(videoId));
        for (ShotCritique c : safe(r.review().shots())) {
            Map<String, Object> shot = byN.get(c.shot());
            if (shot != null && needsFix(c) && !blank(c.fixPrompt())) {
                rewritePrompt(shot, c.fixPrompt(), true); // kept as a suggestion until the shot is redone
            }
        }
        return findings(r.review(), byN, true, Set.of());
    }

    /** Before a targeted redo: the suggested prompts become the shots' prompts (owner-edited prompts are kept). */
    @Transactional
    public void applySuggestedPrompts(UUID videoId, List<UUID> shotIds) {
        Set<String> ids = new HashSet<>(shotIds.stream().map(UUID::toString).toList());
        for (Map<String, Object> s : shots(videoId)) {
            if (!ids.contains(String.valueOf(s.get("id")))) {
                continue;
            }
            Map<String, Object> p = prompt(s);
            Object suggested = p.remove("suggestedPrompt");
            if (suggested instanceof String fix && !fix.isBlank() && !Boolean.TRUE.equals(p.get("editedByOwner"))) {
                p.putIfAbsent("originalPrompt", p.get("keyframePrompt"));
                p.put("keyframePrompt", fix);
                p.put("revisedBy", "creative_director");
                savePrompt(s, p);
            }
        }
    }

    /** Latest report card per reviewed stage, for the dashboard. */
    public List<Map<String, Object>> reports(UUID videoId) {
        return jdbc.sql("""
                        SELECT DISTINCT ON (stage) stage, attempt, model, review_json::text AS review, created_at
                        FROM creative_review WHERE video_id = :v ORDER BY stage, attempt DESC
                        """).param("v", videoId).query().listOfRows().stream().map(r -> {
                    Map<String, Object> m = new LinkedHashMap<>(r);
                    m.put("review", json.readValue((String) r.get("review"), Map.class));
                    return m;
                }).toList();
    }

    // ------------------------------------------------------------------------------------------- scores -> findings

    /**
     * @param imageStage true for the image review: shot problems become targeted FAILs; paragraph problems target the
     *                   shots named to fix them (a whole-video problem can't be fixed by redoing images, so it warns)
     * @param rewritten  shot numbers whose prompts were already improved (shot-plan review), reported as INFO
     */
    static List<Finding> findings(CreativeReview review, Map<Integer, Map<String, Object>> byN, boolean imageStage,
                                  Set<Integer> rewritten) {
        List<Finding> out = new ArrayList<>();
        VideoCritique v = review.video();
        if (v != null) {
            int worst = Math.min(Math.min(v.arc(), v.hook()), Math.min(v.ending(), v.variety()));
            String scores = "arc %d/5, hook %d/5, ending %d/5, variety %d/5".formatted(v.arc(), v.hook(), v.ending(), v.variety());
            if (worst <= WARN_AT) {
                String sev = worst <= FAIL_AT && !imageStage ? "FAIL" : "WARN";
                out.add(new Finding(sev, "CD_VIDEO", scores + ": " + text(v.problem(), v.summary()), "whole video"));
            }
        }
        for (ActCritique a : safe(review.acts())) {
            int worst = Math.min(Math.min(a.flow(), a.keyMoment()), Math.min(a.continuity(), a.mood()));
            if (worst > WARN_AT) {
                continue;
            }
            String ref = "paragraph “" + (blank(a.act()) ? "?" : a.act()) + "”";
            String msg = "flow %d/5, key moment %d/5, continuity %d/5, mood %d/5: %s".formatted(a.flow(), a.keyMoment(),
                    a.continuity(), a.mood(), text(a.problem(), a.summary()));
            List<Integer> fix = safe(a.shotsToFix()).stream().filter(byN::containsKey).toList();
            if (worst > FAIL_AT) {
                out.add(new Finding("WARN", "CD_PARAGRAPH", msg, ref));
            } else if (imageStage && !fix.isEmpty()) {
                for (int n : fix) {
                    out.add(new Finding("FAIL", "CD_PARAGRAPH", msg, "shot " + n, id(byN.get(n))));
                }
            } else if (!imageStage && !fix.isEmpty() && rewritten.containsAll(fix)) {
                out.add(new Finding("INFO", "CD_PARAGRAPH_FIXED", msg + " (fixed by rewriting shots " + fix + ")", ref));
            } else {
                out.add(new Finding(imageStage ? "WARN" : "FAIL", "CD_PARAGRAPH", msg, ref));
            }
        }
        for (ShotCritique c : safe(review.shots())) {
            Map<String, Object> shot = byN.get(c.shot());
            if (shot == null) {
                continue;
            }
            int worst = worst(c);
            boolean literal = c.literal() && c.meaning() <= WARN_AT;
            // Garbled generated lettering is never acceptable output, whatever the other scores.
            boolean must = literal || c.writing();
            if (worst > WARN_AT && !must) {
                continue;
            }
            String ref = "shot " + c.shot();
            String msg = "meaning %d/5, accuracy %d/5, continuity %d/5%s%s: %s".formatted(c.meaning(), c.accuracy(),
                    c.continuity(), c.literal() ? ", shows one word instead of the idea" : "",
                    c.writing() ? ", has generated lettering (it comes out garbled; on-screen words belong in the edit)" : "",
                    text(c.problem(), "weak"));
            if (rewritten.contains(c.shot())) {
                out.add(new Finding("INFO", "CD_PROMPT_IMPROVED", msg + " Prompt rewritten.", ref, id(shot)));
            } else if (worst <= FAIL_AT || must) {
                out.add(new Finding(imageStage && !blank(c.fixPrompt()) ? "FAIL" : imageStage ? "WARN" : "FAIL",
                        "CD_SHOT", msg, ref, imageStage && !blank(c.fixPrompt()) ? id(shot) : null));
            } else {
                out.add(new Finding("WARN", "CD_SHOT", msg, ref));
            }
        }
        return out;
    }

    // ------------------------------------------------------------------------------------------- context & helpers

    /** Everything a reviewer needs to judge meaning in context: concept, paragraphs, timed lines and every shot. */
    String context(UUID videoId, List<Map<String, Object>> shots) {
        VideoPlan plan = plan(videoId);
        StylePresets.Preset preset = presets.byIdOrDefault(plan.stylePreset());
        Concept concept = latestConcept(videoId).orElse(null);
        StoredScript script = latestScript(videoId).orElse(null);
        Map<Integer, StoredLine> lines = lines(script);
        Map<String, Double> starts = script == null || script.lineStarts() == null ? Map.of() : script.lineStarts();
        StringBuilder b = new StringBuilder();
        b.append("Video: ").append(plan.title()).append(" (").append(plan.format()).append(", ").append(plan.durationSec())
                .append("s, ").append(plan.aspectRatio()).append(", style: ").append(preset.name()).append(" - ")
                .append(preset.visualStyle()).append(")\n");
        if (concept != null) {
            b.append("Logline: ").append(concept.logline()).append("\nBig idea: ").append(concept.bigIdea())
                    .append("\nEmotional arc: ").append(concept.emotionalArc()).append('\n');
            for (var act : safe(concept.acts())) {
                b.append("Act “").append(act.title()).append("”: ").append(act.purpose()).append('\n');
            }
        }
        if (script != null && !blank(script.musicBrief())) {
            b.append("Music: ").append(script.musicBrief()).append('\n');
        }
        b.append("\nNarration by paragraph (line | starts at | text):\n");
        String act = null;
        for (StoredLine l : lines.values()) {
            if (!java.util.Objects.equals(act, l.act())) {
                act = l.act();
                b.append("## ").append(act == null ? "(no act)" : act).append('\n');
            }
            b.append(l.index()).append(" | ").append(String.format("%.1fs", starts.getOrDefault(String.valueOf(l.index()), 0.0)))
                    .append(" | ").append(l.spoken() ? "" : "[visual beat] ").append(l.text()).append('\n');
        }
        b.append("\nShots in order (shot | line | starts | seconds | priority | type, move | what we see | image prompt):\n");
        for (Map<String, Object> s : shots) {
            Map<String, Object> p = prompt(s);
            b.append(n(s)).append(" | ").append(p.get("lineIndex") == null ? "-" : p.get("lineIndex")).append(" | ")
                    .append(p.get("startSec")).append(" | ").append(s.get("duration_target_sec")).append(" | ")
                    .append(s.get("priority")).append(" | ").append(s.get("shot_type")).append(", ").append(s.get("camera_move"))
                    .append(" | ").append(s.get("description")).append(" | ").append(p.get("keyframePrompt")).append('\n');
        }
        return b.toString();
    }

    private void store(UUID videoId, Stage stage, int attempt, String model, CreativeReview review) {
        jdbc.sql("""
                        INSERT INTO creative_review (video_id, stage, attempt, model, review_json)
                        VALUES (:v, :s, :a, :m, CAST(:r AS jsonb))
                        ON CONFLICT (video_id, stage, attempt) DO UPDATE
                        SET model = EXCLUDED.model, review_json = EXCLUDED.review_json, created_at = now()
                        """)
                .param("v", videoId).param("s", stage.name()).param("a", attempt).param("m", model)
                .param("r", json.writeValueAsString(review)).update();
    }

    /** Writes a better prompt onto a shot (or keeps it as a suggestion). Never overrides the owner's own prompt. */
    private boolean rewritePrompt(Map<String, Object> shot, String fix, boolean suggestionOnly) {
        Map<String, Object> p = prompt(shot);
        if (Boolean.TRUE.equals(p.get("editedByOwner"))) {
            return false;
        }
        if (suggestionOnly) {
            p.put("suggestedPrompt", fix.strip());
        } else {
            p.putIfAbsent("originalPrompt", p.get("keyframePrompt"));
            p.put("keyframePrompt", fix.strip());
            p.put("revisedBy", "creative_director");
        }
        savePrompt(shot, p);
        return true;
    }

    private void savePrompt(Map<String, Object> shot, Map<String, Object> p) {
        jdbc.sql("UPDATE shot SET prompt_json = CAST(:p AS jsonb), updated_at = now() WHERE id = :s")
                .param("p", json.writeValueAsString(p)).param("s", shot.get("id")).update();
    }

    private Map<String, Object> prompt(Map<String, Object> shot) {
        return json.readValue((String) shot.get("prompt_json"), new TypeReference<LinkedHashMap<String, Object>>() {
        });
    }

    private static Map<Integer, StoredLine> lines(StoredScript script) {
        Map<Integer, StoredLine> m = new LinkedHashMap<>();
        if (script != null) {
            script.lines().forEach(l -> m.put(l.index(), l));
        }
        return m;
    }

    static Map<Integer, Map<String, Object>> byNumber(List<Map<String, Object>> shots) {
        Map<Integer, Map<String, Object>> m = new HashMap<>();
        shots.forEach(s -> m.put(n(s), s));
        return m;
    }

    private static int n(Map<String, Object> shot) {
        return ((Number) shot.get("ord")).intValue() + 1;
    }

    private static String id(Map<String, Object> shot) {
        return String.valueOf(shot.get("id"));
    }

    private static boolean needsFix(ShotCritique c) {
        return worst(c) <= WARN_AT || c.writing();
    }

    private static int worst(ShotCritique c) {
        return Math.min(c.meaning(), Math.min(c.accuracy(), c.continuity()));
    }

    private static double num(Object o) {
        return o instanceof Number n ? n.doubleValue() : 0;
    }

    private static boolean blank(String s) {
        return s == null || s.isBlank();
    }

    private static String text(String first, String fallback) {
        return blank(first) ? (blank(fallback) ? "" : fallback) : first;
    }

    private static <T> List<T> safe(List<T> list) {
        return list == null ? List.of() : list;
    }
}
