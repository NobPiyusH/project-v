package com.projectv.production;

import com.projectv.media.MediaTypes.Finding;
import com.projectv.media.MediaTypes.InspectImage;
import com.projectv.media.MediaTypes.InspectLine;
import com.projectv.media.MediaTypes.MediaInspectRequest;
import com.projectv.media.MediaTypes.MediaInspectResult;
import com.projectv.planner.VideoPlan;
import com.projectv.production.ProductionService.StoredLine;
import com.projectv.production.ProductionService.StoredScript;
import com.projectv.workflow.Stage;
import com.projectv.workflow.WorkflowTypes.InspectionReport;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collection;
import java.util.EnumSet;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * The Inspector: a second, independent level of checking after every stage (owner, 16 Nov 2026). A stage checks its
 * own work; the Inspector checks the stored result again with different means: structural rules here (script length,
 * timing, missing pieces) and file checks on the media worker (Whisper, FFmpeg, image statistics). It never changes
 * anything; the workflow decides what a FAIL means (redo once with the findings as notes, then ask the owner).
 */
@Component
public class Inspector extends StageSupport {

    /** Stages the Inspector looks at. PLAN and CONCEPT have no hard rules yet; finishing stages arrive with M2c. */
    public static final Set<Stage> INSPECTED = EnumSet.of(Stage.RESEARCH, Stage.SCRIPT, Stage.MUSIC, Stage.NARRATION,
            Stage.SHOT_PLAN, Stage.KEYFRAMES, Stage.ANIMATIC, Stage.DRAFT_GEN, Stage.FINAL_GEN, Stage.ASSEMBLY);

    /** Stages whose findings name shots, so a FAIL can be fixed by redoing only those shots. */
    public static final Set<Stage> PER_SHOT = EnumSet.of(Stage.KEYFRAMES, Stage.DRAFT_GEN, Stage.FINAL_GEN);

    /** Silence between two narration lines longer than this reads as dead air. */
    static final double MAX_GAP_SEC = 2.5;

    private static final Set<Stage> MEDIA = EnumSet.of(Stage.NARRATION, Stage.MUSIC, Stage.KEYFRAMES, Stage.ANIMATIC,
            Stage.DRAFT_GEN, Stage.FINAL_GEN, Stage.ASSEMBLY);

    public Inspector(ProductionDeps d) {
        super(d);
    }

    /** What the media worker should look at, or null when this stage has no file checks. */
    public MediaInspectRequest mediaRequest(UUID videoId, Stage stage) {
        if (!MEDIA.contains(stage)) {
            return null;
        }
        VideoPlan plan = plan(videoId);
        boolean isShort = "short".equals(plan.format());
        StoredScript script = latestScript(videoId).orElse(null);
        List<InspectLine> lines = new ArrayList<>();
        List<InspectImage> images = new ArrayList<>();
        String musicUri = null;
        String videoUri = null;
        Double speechEnd = null;
        switch (stage) {
            case NARRATION -> {
                for (StoredLine l : script == null ? List.<StoredLine>of() : script.lines()) {
                    if (l.spoken() && l.audioUri() != null) {
                        lines.add(new InspectLine(l.index(), l.text(), l.audioUri()));
                    }
                }
            }
            case MUSIC -> musicUri = jdbc.sql("""
                            SELECT a.uri_hot FROM music_cue m JOIN asset a ON a.id = m.asset_id WHERE m.video_id = :v
                            """).param("v", videoId).query(String.class).optional().orElse(null);
            case KEYFRAMES -> {
                for (Map<String, Object> s : shots(videoId)) {
                    if (s.get("keyframe_uri") != null) {
                        images.add(new InspectImage(shotRef(s), (String) s.get("keyframe_uri")));
                    }
                }
            }
            case ANIMATIC -> {
                videoUri = jdbc.sql("""
                                SELECT a.uri_hot FROM video v JOIN asset a ON a.id = v.animatic_asset_id WHERE v.id = :v
                                """).param("v", videoId).query(String.class).optional().orElse(null);
                speechEnd = script == null ? null : speechEnd(script);
            }
            case ASSEMBLY -> {
                videoUri = jdbc.sql("""
                                SELECT a.uri_hot FROM video v JOIN asset a ON a.id = v.master_asset_id WHERE v.id = :v
                                """).param("v", videoId).query(String.class).optional().orElse(null);
                speechEnd = script == null ? null : speechEnd(script);
            }
            case DRAFT_GEN, FINAL_GEN -> {
                // The worker's clip checks read the clips from "images" (ref + uri), like keyframes.
                for (Map<String, Object> s : clipShots(videoId)) {
                    Object uri = s.get(stage == Stage.DRAFT_GEN ? "draft_uri" : "final_uri");
                    if (uri != null) {
                        images.add(new InspectImage(shotRef(s), (String) uri));
                    }
                }
            }
            default -> {
            }
        }
        return new MediaInspectRequest(videoId.toString(), stage.name(), plan.aspectRatio(), isShort, plan.durationSec(),
                lines, musicUri, images, videoUri, speechEnd);
    }

    /** Runs the structural checks, merges the worker's file findings, stores the report and returns the verdict. */
    public InspectionReport evaluate(UUID videoId, Stage stage, int attempt, MediaInspectResult media) {
        List<Finding> findings = new ArrayList<>(structural(videoId, stage));
        List<String> checked = new ArrayList<>();
        if (media != null) {
            findings.addAll(media.findings() == null ? List.of() : media.findings());
            checked.addAll(media.checked() == null ? List.of() : media.checked());
        }
        if (PER_SHOT.contains(stage)) {
            findings = withShotTargets(findings, shots(videoId));
        }
        InspectionReport report = report(findings, checked.size());
        jdbc.sql("""
                        INSERT INTO inspection (video_id, stage, attempt, verdict, findings_json, checked_json)
                        VALUES (:v, :s, :a, :verdict, CAST(:f AS jsonb), CAST(:c AS jsonb))
                        ON CONFLICT (video_id, stage, attempt) DO UPDATE
                        SET verdict = EXCLUDED.verdict, findings_json = EXCLUDED.findings_json,
                            checked_json = EXCLUDED.checked_json, created_at = now()
                        """)
                .param("v", videoId).param("s", stage.name()).param("a", attempt).param("verdict", report.verdict())
                .param("f", json.writeValueAsString(findings)).param("c", json.writeValueAsString(checked))
                .update();
        return report;
    }

    /** Every stored report for a video, newest attempt first per stage (for the dashboard). */
    public List<Map<String, Object>> reports(UUID videoId) {
        return jdbc.sql("""
                        SELECT stage, attempt, verdict, findings_json::text AS findings, checked_json::text AS checked,
                               created_at
                        FROM inspection WHERE video_id = :v ORDER BY stage, attempt DESC
                        """).param("v", videoId).query().listOfRows().stream().map(r -> {
                    Map<String, Object> m = new java.util.LinkedHashMap<>(r);
                    m.put("findings", json.readValue((String) r.get("findings"), List.class));
                    m.put("checked", json.readValue((String) r.get("checked"), List.class));
                    return m;
                }).toList();
    }

    /** Links "shot N" findings from the worker to their shot ids, so a FAIL can be fixed on that shot alone. */
    static List<Finding> withShotTargets(List<Finding> findings, List<Map<String, Object>> shots) {
        Map<String, String> idByRef = new java.util.HashMap<>();
        for (Map<String, Object> s : shots) {
            idByRef.put(shotRef(s), String.valueOf(s.get("id")));
        }
        return findings.stream().map(f -> f.targetId() == null && f.ref() != null && idByRef.containsKey(f.ref())
                ? new Finding(f.severity(), f.code(), f.message(), f.ref(), idByRef.get(f.ref())) : f).toList();
    }

    static InspectionReport report(List<Finding> findings, int filesChecked) {
        List<Finding> fails = findings.stream().filter(f -> "FAIL".equals(f.severity())).toList();
        List<Finding> warns = findings.stream().filter(f -> "WARN".equals(f.severity())).toList();
        String verdict = !fails.isEmpty() ? "FAIL" : !warns.isEmpty() ? "WARN" : "PASS";
        String checked = filesChecked > 0 ? " (" + filesChecked + " files checked)" : "";
        String summary = switch (verdict) {
            case "FAIL" -> "Inspector: FAIL, %d problem%s%s: %s".formatted(fails.size(), fails.size() == 1 ? "" : "s",
                    checked, describe(fails, 3));
            case "WARN" -> "Inspector: passed with %d warning%s%s: %s".formatted(warns.size(),
                    warns.size() == 1 ? "" : "s", checked, describe(warns, 2));
            default -> "Inspector: all checks passed" + checked + ".";
        };
        String notes = fails.isEmpty() ? null
                : "The Inspector rejected the previous attempt. Fix these problems:\n- " + String.join("\n- ",
                fails.stream().map(f -> (f.ref() == null ? "" : f.ref() + ": ") + f.message()).toList());
        // A targeted redo (only the broken shots) is possible when every problem names its shot.
        List<String> targets = !fails.isEmpty() && fails.stream().allMatch(f -> f.targetId() != null)
                ? fails.stream().map(Finding::targetId).distinct().toList() : List.of();
        return new InspectionReport(verdict, summary, notes, targets);
    }

    private static String describe(List<Finding> list, int max) {
        List<String> parts = list.stream().limit(max).map(f -> (f.ref() == null ? "" : f.ref() + ": ") + f.message()).toList();
        return String.join("; ", parts) + (list.size() > max ? "; and " + (list.size() - max) + " more." : "");
    }

    // ------------------------------------------------------------------------------------------- structural checks

    List<Finding> structural(UUID videoId, Stage stage) {
        VideoPlan plan = plan(videoId);
        StoredScript script = latestScript(videoId).orElse(null);
        return switch (stage) {
            case RESEARCH -> research(plan, facts(videoId));
            case SCRIPT -> script(plan, script);
            case NARRATION -> narration(script);
            case MUSIC -> "none".equals(plan.music()) ? List.of() : music(videoId);
            case SHOT_PLAN -> shotPlan(plan, script, shots(videoId));
            case KEYFRAMES -> keyframes(shots(videoId));
            case ANIMATIC -> animatic(videoId, script);
            case ASSEMBLY -> jdbc.sql("SELECT master_asset_id IS NOT NULL FROM video WHERE id = :v").param("v", videoId)
                    .query(Boolean.class).single() ? List.of() : List.of(fail("NO_MASTER", "No master was stored.", "video"));
            case DRAFT_GEN -> missingClips(clipShots(videoId), true);
            case FINAL_GEN -> missingClips(clipShots(videoId), false);
            default -> List.of();
        };
    }

    static List<Finding> research(VideoPlan plan, List<com.projectv.media.MediaTypes.Fact> facts) {
        List<Finding> out = new ArrayList<>();
        if (plan.needsResearch() && facts.isEmpty()) {
            out.add(fail("NO_FACTS", "Research found no facts for a factual video.", "research"));
            return out;
        }
        if (plan.needsResearch() && facts.size() < 3) {
            out.add(warn("FEW_FACTS", "Only " + facts.size() + " facts: the script may lean on unchecked claims.",
                    "research"));
        }
        long unsourced = facts.stream().filter(f -> f.sources() == null || f.sources().isEmpty()).count();
        if (unsourced > 0) {
            out.add(warn("UNSOURCED", unsourced + " fact" + (unsourced == 1 ? " has" : "s have") + " no source.",
                    "research"));
        }
        return out;
    }

    static List<Finding> script(VideoPlan plan, StoredScript script) {
        List<Finding> out = new ArrayList<>();
        if (script == null || script.lines() == null || script.lines().isEmpty()) {
            out.add(fail("NO_SCRIPT", "The script has no lines.", "script"));
            return out;
        }
        List<StoredLine> spoken = script.lines().stream().filter(StoredLine::spoken).toList();
        for (StoredLine l : spoken) {
            if (l.text() == null || l.text().isBlank()) {
                out.add(fail("EMPTY_LINE", "A spoken line has no words.", "line " + (l.index() + 1)));
            }
        }
        int words = spoken.stream().mapToInt(l -> wordCount(l.text())).sum();
        boolean isShort = "short".equals(plan.format());
        // Same budget the Writer is given (WriterAgent: 2.1 words/s, 78% of the time spoken; sparse narration 75% of that).
        double cap = plan.durationSec() * 2.1 * 0.78 * ("sparse".equals(plan.narration()) ? 0.75 : 1.0);
        if (!"none".equals(plan.narration()) && words > cap * 1.15) {
            out.add(fail("SCRIPT_TOO_LONG", "%d words for a %ds video (budget about %.0f): the voice won't fit."
                    .formatted(words, plan.durationSec(), cap), "script"));
        }
        if (isShort && words / 2.6 > 58) {
            out.add(fail("SHORT_TOO_LONG", "About %.0fs of speech: a Short must stay under 60s.".formatted(words / 2.6),
                    "script"));
        }
        Set<String> seen = new HashSet<>();
        for (StoredLine l : spoken) {
            String key = normalise(l.text());
            if (!key.isEmpty() && !seen.add(key)) {
                out.add(warn("REPEATED_LINE", "This line repeats an earlier one.", "line " + (l.index() + 1)));
            }
        }
        String all = normalise(String.join(" ", spoken.stream().map(StoredLine::text).toList()));
        for (String item : plan.mustInclude() == null ? List.<String>of() : plan.mustInclude()) {
            if (!mentions(all, item)) {
                out.add(warn("MISSING_MUST_INCLUDE", "The script doesn't mention “" + item + "”, which the "
                        + "prompt asked for.", "script"));
            }
        }
        return out;
    }

    static List<Finding> narration(StoredScript script) {
        List<Finding> out = new ArrayList<>();
        if (script == null) {
            return List.of(fail("NO_SCRIPT", "There is no script to voice.", "script"));
        }
        for (StoredLine l : script.lines()) {
            if (!l.spoken()) {
                continue;
            }
            String ref = "line " + (l.index() + 1);
            if (l.audioUri() == null) {
                out.add(fail("MISSING_VOICE", "This line has no voice recording.", ref));
            } else if ("FAILED".equals(l.voiceStatus())
                    || (l.check() != null && !l.check().startsWith("coverage") && !l.check().startsWith("only numbers"))) {
                out.add(fail("VOICE_CHECK_FAILED", "The voice stage's own check rejected every take ("
                        + l.check() + ").", ref));
            }
        }
        return out;
    }

    private List<Finding> music(UUID videoId) {
        boolean has = jdbc.sql("SELECT count(*) FROM music_cue WHERE video_id = :v").param("v", videoId)
                .query(Integer.class).single() > 0;
        return has ? List.of() : List.of(fail("NO_MUSIC", "The plan wants music but none was stored.", "music"));
    }

    static List<Finding> shotPlan(VideoPlan plan, StoredScript script, List<Map<String, Object>> shots) {
        List<Finding> out = new ArrayList<>();
        if (shots.isEmpty()) {
            out.add(fail("NO_SHOTS", "The shot plan has no shots.", "shot plan"));
            return out;
        }
        long tiny = shots.stream().filter(s -> num(s.get("duration_target_sec")) < 0.8).count();
        if (tiny > 0) {
            out.add(warn("FLASH_SHOTS", tiny + " shot" + (tiny == 1 ? " is" : "s are") + " under 0.8s: too fast to read.",
                    "shot plan"));
        }
        if (script != null) {
            out.addAll(timing(plan, script));
        }
        return out;
    }

    static List<Finding> keyframes(List<Map<String, Object>> shots) {
        return shots.stream().filter(s -> s.get("keyframe_uri") == null)
                .map(s -> new Finding("FAIL", "MISSING_KEYFRAME", "This shot has no image.", shotRef(s),
                        String.valueOf(s.get("id"))))
                .toList();
    }

    /** Every shot with an image needs a draft; every draft the owner didn't mark "keep" needs a final. */
    static List<Finding> missingClips(List<Map<String, Object>> shots, boolean drafts) {
        return shots.stream()
                .filter(s -> drafts ? s.get("keyframe_uri") != null && s.get("draft_uri") == null
                        : s.get("draft_uri") != null && Boolean.TRUE.equals(s.get("final_wanted")) && s.get("final_uri") == null)
                .filter(s -> !"STOPPED".equals(s.get("clip_status")) && s.get("footage_asset_id") == null)
                .map(s -> new Finding("FAIL", "MISSING_CLIP", "This shot has no " + (drafts ? "draft" : "final") + " clip"
                        + (s.get("clip_error") == null ? "." : ": " + s.get("clip_error")), shotRef(s), String.valueOf(s.get("id"))))
                .toList();
    }

    private List<Map<String, Object>> clipShots(UUID videoId) {
        return jdbc.sql("""
                        SELECT s.id, s.ord, s.final_wanted, s.clip_status, s.clip_error, s.footage_asset_id,
                               k.uri_hot AS keyframe_uri,
                               d.uri_hot AS draft_uri, f.uri_hot AS final_uri
                        FROM shot s JOIN scene sc ON sc.id = s.scene_id
                        LEFT JOIN asset k ON k.id = s.keyframe_asset_id
                        LEFT JOIN asset d ON d.id = s.draft_clip_asset_id
                        LEFT JOIN asset f ON f.id = s.final_clip_asset_id
                        WHERE sc.video_id = :v ORDER BY s.ord
                        """).param("v", videoId).query().listOfRows();
    }

    private List<Finding> animatic(UUID videoId, StoredScript script) {
        boolean has = jdbc.sql("SELECT animatic_asset_id IS NOT NULL FROM video WHERE id = :v").param("v", videoId)
                .query(Boolean.class).single();
        List<Finding> out = new ArrayList<>();
        if (!has) {
            out.add(fail("NO_ANIMATIC", "No animatic was stored.", "video"));
        }
        if (script != null) {
            out.addAll(gaps(script));
        }
        return out;
    }

    /** Short length and dead air, from the planned line starts and the real voice lengths. */
    static List<Finding> timing(VideoPlan plan, StoredScript script) {
        List<Finding> out = new ArrayList<>();
        double end = speechEnd(script);
        if ("short".equals(plan.format()) && end + NarrationTiming.TAIL > NarrationTiming.SHORT_MAX + 0.05) {
            out.add(fail("SHORT_TOO_LONG", "The voice ends at %.1fs: with the closing beat the Short runs past 60s."
                    .formatted(end), "shot plan"));
        }
        out.addAll(gaps(script));
        return out;
    }

    static List<Finding> gaps(StoredScript script) {
        List<Finding> out = new ArrayList<>();
        if (script.lineStarts() == null || script.lineStarts().isEmpty()) {
            return out;
        }
        double tempo = script.tempo() == null ? 1.0 : script.tempo();
        StoredLine prev = null;
        double prevEnd = 0;
        for (StoredLine l : script.lines()) {
            if (!l.spoken() || l.durationSec() == null) {
                continue;
            }
            Double start = script.lineStarts().get(String.valueOf(l.index()));
            if (start == null) {
                out.add(warn("UNTIMED_LINE", "This line has no planned start: it may drift.", "line " + (l.index() + 1)));
                continue;
            }
            if (prev != null && start - prevEnd > MAX_GAP_SEC) {
                out.add(warn("DEAD_AIR", ("%.1fs of silence after this line (the voice is shorter than the shots "
                        + "planned for it). Redo the shot plan to retime.").formatted(start - prevEnd),
                        "line " + (prev.index() + 1)));
            }
            prev = l;
            prevEnd = Math.max(start, prevEnd) + l.durationSec() / tempo;
        }
        return out;
    }

    /** When the last word is spoken, using the same placement rules as the animatic. */
    static double speechEnd(StoredScript script) {
        double tempo = script.tempo() == null ? 1.0 : script.tempo();
        Map<String, Double> starts = script.lineStarts() == null ? Map.of() : script.lineStarts();
        double prevEnd = 0;
        for (StoredLine l : script.lines()) {
            if (!l.spoken() || l.durationSec() == null) {
                continue;
            }
            Double want = starts.get(String.valueOf(l.index()));
            double start = want != null ? Math.max(want, prevEnd + 0.15) : prevEnd + 0.4;
            prevEnd = start + l.durationSec() / tempo;
        }
        return prevEnd;
    }

    // ------------------------------------------------------------------------------------------- helpers

    private static String shotRef(Map<String, Object> s) {
        return "shot " + (((Number) s.get("ord")).intValue() + 1);
    }

    private static double num(Object o) {
        return o instanceof Number n ? n.doubleValue() : 0;
    }

    static int wordCount(String text) {
        return text == null || text.isBlank() ? 0 : text.strip().split("\\s+").length;
    }

    static String normalise(String text) {
        return text == null ? "" : text.toLowerCase(Locale.ROOT).replaceAll("[^\\p{L}\\p{N}]+", " ").strip();
    }

    /** True when every significant word (4+ letters) of {@code item} appears in the text. */
    static boolean mentions(String normalisedText, String item) {
        Collection<String> words = List.of(normalise(item).split(" "));
        List<String> significant = words.stream().filter(w -> w.length() >= 4).toList();
        List<String> check = significant.isEmpty() ? words.stream().filter(w -> !w.isEmpty()).toList() : significant;
        // Light stemming: "paradox" matches "paradoxes", "expansion" matches "expanding" via the first 6 letters.
        return check.stream().allMatch(w -> normalisedText.contains(w.length() > 6 ? w.substring(0, 6) : w));
    }

    private static Finding fail(String code, String message, String ref) {
        return new Finding("FAIL", code, message, ref);
    }

    private static Finding warn(String code, String message, String ref) {
        return new Finding("WARN", code, message, ref);
    }
}
