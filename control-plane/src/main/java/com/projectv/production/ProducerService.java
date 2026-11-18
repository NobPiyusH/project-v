package com.projectv.production;

import com.projectv.ledger.BudgetGuard;
import com.projectv.media.MediaTypes.ProducerAdvice;
import com.projectv.media.MediaTypes.ProducerItem;
import com.projectv.media.MediaTypes.ProducerReviewRequest;
import com.projectv.media.MediaTypes.ProducerReviewResult;
import com.projectv.production.ProductionService.StoredLine;
import com.projectv.production.ProductionService.StoredScript;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * AI Producer, P1 (docs/plans/ai-producer.md, owner 18 Nov 2026): in PRODUCER mode a multimodal model reviews a
 * stage's output the way a producer would (reads the script, LISTENS to every voice line, looks at the keyframes,
 * WATCHES the clips and the master) and recommends approve / redo / ask. P1 is advise-only: the owner still decides,
 * with the recommendation in front of them, and every owner decision is compared with it (agreement score) and kept
 * as a lesson. Rules the Producer learns (P2) and the owner's recent notes go into every review.
 */
@Service
public class ProducerService extends StageSupport {

    static final String RUBRIC_BASE = """
            You are the producer of a premium, privately owned documentary studio. Quality first, cost second.
            Judge each item strictly but fairly. score 0-10 (7+ = good enough to publish); ok = score >= 7.
            problem: the main issue in a few words, or "" if none. fix: a PRECISE instruction for a redo (a rewritten
            prompt or delivery note), or "" if ok. verdict: approve (all ok), redo (specific items are not ok and a redo
            would fix them), ask (unsure, taste call, or the issue needs the owner). confidence 0-1. redoNotes: the
            precise notes for the redo, naming the items. summary: 1-2 plain sentences for the owner.""";

    static final Map<String, String> RUBRICS = Map.of(
            "SCRIPT", "Script: strong hook in the first line, true facts, one idea per line, fits the duration, covers what the owner asked for, ends well.",
            "NARRATION", "Narration audio: LISTEN to each line. Natural human delivery (not robotic), emotion and pace match the line and its delivery note, every word clear and exactly as written, no mispronunciations, no instructions read aloud, no glitches, clicks or long silences.",
            "KEYFRAMES", "Keyframes: each image matches its shot description, strong composition for vertical video, no text or letters, no artefacts or deformities, consistent look across shots.",
            "DRAFT_GEN", "Draft clips: WATCH each clip. It must really move (no near-still or slideshow), match the shot and its motion prompt, no glitches, morphing, black frames or flicker.",
            "FINAL_GEN", "Final clips: WATCH each clip. Real, smooth motion matching the shot, no glitches, morphing, black frames or flicker; premium look.",
            "ASSEMBLY", "Finished video: WATCH and LISTEN. Hook in the first 2 seconds, good pacing, every shot moves, captions readable and in sync, voice clear over music, no black or broken moments, strong ending.");

    private final String model;
    private final com.projectv.agents.Llm llm;

    public ProducerService(ProductionDeps d, String model) {
        this(d, model, null);
    }

    @org.springframework.beans.factory.annotation.Autowired
    public ProducerService(ProductionDeps d, @Value("${projectv.producer.model:gemini-3.6-flash}") String model,
                           com.projectv.agents.Llm llm) {
        super(d);
        this.model = model;
        this.llm = llm;
    }

    /** The stages the Producer reviews in P1. */
    public static boolean reviews(String stage) {
        return RUBRICS.containsKey(stage);
    }

    // ------------------------------------------------------------------------------------------- review request

    /** What to review for this stage, or null when the stage has nothing the Producer judges. */
    public ProducerReviewRequest reviewRequest(UUID videoId, String stage) {
        if (!reviews(stage)) {
            return null;
        }
        List<ProducerItem> items = switch (stage) {
            case "SCRIPT" -> scriptItems(videoId);
            case "NARRATION" -> narrationItems(videoId);
            case "KEYFRAMES" -> shotItems(videoId, "keyframe_asset_id", "image");
            case "DRAFT_GEN" -> shotItems(videoId, "draft_clip_asset_id", "video");
            case "FINAL_GEN" -> shotItems(videoId, "final_clip_asset_id", "video");
            case "ASSEMBLY" -> masterItems(videoId);
            default -> List.of();
        };
        if (items.isEmpty()) {
            return null;
        }
        String context = "Video: " + title(videoId) + "\nOwner's prompt: " + prompt(videoId)
                + latestScript(videoId).map(s -> "\nNarrator: " + s.voiceName() + ", style: " + s.voiceStyle()).orElse("");
        return new ProducerReviewRequest(videoId.toString(), stage, model, RUBRIC_BASE + "\n" + RUBRICS.get(stage),
                context, rules(stage), items);
    }

    List<ProducerItem> scriptItems(UUID videoId) {
        return latestScript(videoId).map(s -> {
            StringBuilder b = new StringBuilder();
            for (StoredLine l : s.lines()) {
                b.append(l.index() + 1).append(". ").append(l.text())
                        .append(l.delivery() == null ? "" : "  [delivery: " + l.delivery() + "]").append('\n');
            }
            return List.of(new ProducerItem("script", "Script", "text", null, b.toString(),
                    "A script for: " + prompt(videoId) + " (" + plan(videoId).durationSec() + " s)"));
        }).orElse(List.of());
    }

    List<ProducerItem> narrationItems(UUID videoId) {
        List<ProducerItem> out = new ArrayList<>();
        StoredScript s = latestScript(videoId).orElse(null);
        if (s == null) {
            return out;
        }
        for (StoredLine l : s.lines()) {
            if (l.spoken() && l.audioUri() != null) {
                out.add(new ProducerItem("line-" + l.index(), "Line " + (l.index() + 1), "audio", l.audioUri(), null,
                        "Says exactly: \"" + l.text() + "\". Delivery: "
                                + (l.delivery() != null ? l.delivery() : Objects.toString(s.voiceStyle(), "natural narrator"))));
            }
        }
        return out;
    }

    List<ProducerItem> shotItems(UUID videoId, String assetColumn, String kind) {
        if (!List.of("keyframe_asset_id", "draft_clip_asset_id", "final_clip_asset_id").contains(assetColumn)) {
            throw new IllegalArgumentException(assetColumn);
        }
        List<ProducerItem> out = new ArrayList<>();
        for (Map<String, Object> r : jdbc.sql("""
                        SELECT s.id, s.ord, s.description, s.prompt_json->>'keyframePrompt' AS kp,
                               s.prompt_json->>'motionPrompt' AS mp, a.uri_hot
                        FROM shot s JOIN scene sc ON sc.id = s.scene_id JOIN asset a ON a.id = s.%s
                        WHERE sc.video_id = :v AND s.footage_asset_id IS NULL ORDER BY s.ord
                        """.formatted(assetColumn)).param("v", videoId).query().listOfRows()) {
            int n = ((Number) r.get("ord")).intValue() + 1;
            String expect = "image".equals(kind)
                    ? "Shot " + n + ": " + r.get("description") + ". Prompt: " + r.get("kp")
                    : "Shot " + n + ": " + r.get("description") + ". Motion: " + r.get("mp");
            out.add(new ProducerItem("shot-" + n, "Shot " + n, kind, (String) r.get("uri_hot"), null, expect));
        }
        return out;
    }

    List<ProducerItem> masterItems(UUID videoId) {
        return jdbc.sql("""
                        SELECT a.uri_hot FROM video v JOIN asset a ON a.id = v.master_asset_id WHERE v.id = :v
                        """).param("v", videoId).query(String.class).optional()
                .map(uri -> List.of(new ProducerItem("master", "Finished video", "video", uri, null,
                        "The finished video for: " + prompt(videoId))))
                .orElse(List.of());
    }

    /** The owner's learned rules for this stage plus their most recent notes: the Producer follows the owner's taste. */
    List<String> rules(String stage) {
        List<String> out = new ArrayList<>(jdbc.sql("""
                        SELECT rule_text FROM producer_rule WHERE enabled AND (stage IS NULL OR stage = :s)
                        ORDER BY evidence DESC, updated_at DESC LIMIT 20
                        """).param("s", stage).query(String.class).list());
        out.addAll(jdbc.sql("""
                        SELECT 'Owner feedback on ' || stage || ': ' || notes FROM approval
                        WHERE notes IS NOT NULL AND btrim(notes) <> '' AND actor = 'owner' AND (stage = :s OR stage = 'ALL')
                        ORDER BY ts DESC LIMIT 8
                        """).param("s", stage).query(String.class).list());
        return out;
    }

    private String title(UUID videoId) {
        return jdbc.sql("SELECT COALESCE(title, '') FROM video WHERE id = :v").param("v", videoId).query(String.class).single();
    }

    // ------------------------------------------------------------------------------------------- save + advice

    @Transactional
    public ProducerAdvice saveReview(UUID videoId, String stage, int attempt, ProducerReviewResult r) {
        BigDecimal cost = r.usage() == null ? BigDecimal.ZERO : pricing.textInr(r.usage());
        if (cost.signum() > 0) {
            ledger.record(videoId, BudgetGuard.GEMINI, cost, BigDecimal.valueOf(r.usage().totalTokens()), "tokens", true,
                    "PRODUCER review of " + stage + " (" + r.model() + ")");
        }
        UUID id = UUID.randomUUID();
        String verdict = r.verdict() == null ? "ask" : r.verdict().toLowerCase(java.util.Locale.ROOT);
        jdbc.sql("""
                        INSERT INTO producer_decision (id, video_id, stage, attempt, verdict, confidence, summary, redo_notes,
                                                       items_json, model, cost_inr)
                        VALUES (:id, :v, :s, :a, :verdict, :c, :sum, :notes, CAST(:items AS jsonb), :m, :cost)
                        """)
                .param("id", id).param("v", videoId).param("s", stage).param("a", attempt).param("verdict", verdict)
                .param("c", BigDecimal.valueOf(r.confidence()).setScale(3, RoundingMode.HALF_UP)).param("sum", r.summary())
                .param("notes", r.redoNotes()).param("items", json.writeValueAsString(r.items() == null ? List.of() : r.items()))
                .param("m", r.model()).param("cost", cost).update();
        long weak = r.items() == null ? 0 : r.items().stream().filter(i -> !i.ok()).count();
        String what = switch (verdict) {
            case "approve" -> "recommends APPROVE";
            case "redo" -> "recommends REDO of " + weak + " item" + (weak == 1 ? "" : "s");
            default -> "recommends you DECIDE (unsure)";
        };
        String text = "AI Producer %s (%.0f%% sure): %s%s".formatted(what, r.confidence() * 100,
                Objects.toString(r.summary(), ""), r.redoNotes() == null || r.redoNotes().isBlank() ? ""
                        : " Suggested redo: " + r.redoNotes());
        List<String> weakItems = r.items() == null ? List.of()
                : r.items().stream().filter(i -> !i.ok()).map(com.projectv.media.MediaTypes.ProducerItemVerdict::id).toList();
        return new ProducerAdvice(verdict, r.confidence(), text, r.redoNotes(), id.toString(), weakItems);
    }

    // ------------------------------------------------------------------------------------------- P2: acting

    /** The owner's trust switch: auto (decide once agreement is high enough), advise, or decide. */
    public String trust() {
        return jdbc.sql("SELECT value FROM producer_setting WHERE key = 'trust'").query(String.class).optional().orElse("auto");
    }

    public void setTrust(String trust) {
        if (!List.of("auto", "advise", "decide").contains(trust)) {
            throw new IllegalArgumentException("trust must be auto, advise or decide");
        }
        jdbc.sql("""
                        INSERT INTO producer_setting (key, value) VALUES ('trust', :t)
                        ON CONFLICT (key) DO UPDATE SET value = :t, updated_at = now()
                        """).param("t", trust).update();
    }

    /** May the Producer act on its own right now? decide: yes; advise: no; auto: once it agrees with the owner enough. */
    public boolean deciding() {
        String t = trust();
        if ("decide".equals(t)) {
            return true;
        }
        if ("advise".equals(t)) {
            return false;
        }
        Map<String, Object> a = agreement();
        return ((Number) a.get("decided")).longValue() >= pricing.producerSetting("trustAfterDecisions")
                && ((Number) a.get("rate")).longValue() >= pricing.producerSetting("trustAgreementPct");
    }

    /** Records what the Producer did by itself: approved | redo | asked. */
    public void markActed(UUID decisionId, String acted) {
        jdbc.sql("UPDATE producer_decision SET acted = :a WHERE id = :id").param("a", acted).param("id", decisionId).update();
    }

    /** A redo is allowed while the stage has rounds left and the video is inside the Producer budget. */
    public boolean mayRedo(UUID videoId, int round) {
        if (round > pricing.producerSetting("roundsPerStage")) {
            return false;
        }
        BigDecimal spent = jdbc.sql("SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry WHERE video_id = :v")
                .param("v", videoId).query(BigDecimal.class).single();
        return spent.compareTo(pricing.producerBudgetInr(plan(videoId).durationSec() <= 180)) < 0;
    }

    /** Shot ids for the weak items of a shot stage ("shot-N" = the shot at position N). */
    public List<String> shotIdsFor(UUID videoId, List<String> itemIds) {
        List<Integer> ords = itemIds.stream().filter(i -> i.startsWith("shot-"))
                .map(i -> Integer.parseInt(i.substring(5)) - 1).toList();
        if (ords.isEmpty()) {
            return List.of();
        }
        return jdbc.sql("""
                        SELECT s.id::text FROM shot s JOIN scene sc ON sc.id = s.scene_id
                        WHERE sc.video_id = :v AND s.ord IN (:ords) ORDER BY s.ord
                        """).param("v", videoId).param("ords", ords).query(String.class).list();
    }

    /** The narration request for a Producer redo: its precise notes become the reading style (lines keep their delivery). */
    public static com.projectv.media.MediaTypes.NarrationRequest withStyle(com.projectv.media.MediaTypes.NarrationRequest base,
                                                                         String notes) {
        if (notes == null || notes.isBlank()) {
            return base;
        }
        String[] w = notes.strip().split("\\s+");
        String style = String.join(" ", java.util.Arrays.copyOf(w, Math.min(w.length, 40)));
        return new com.projectv.media.MediaTypes.NarrationRequest(base.videoId(), base.model(), base.checkModel(),
                base.voiceName(), style, base.language(), base.lines(), base.voiceProvider());
    }

    /** P2: the Producer's own call on a money step, recorded so it shows in the report. */
    @Transactional
    public com.projectv.media.MediaTypes.SpendDecision spendDecision(UUID videoId, String stage, BigDecimal estimateInr) {
        String text = spendAdvice(videoId, estimateInr);
        boolean approve = deciding() && text.contains("would APPROVE");
        UUID id = UUID.randomUUID();
        jdbc.sql("""
                        INSERT INTO producer_decision (id, video_id, stage, attempt, verdict, confidence, summary, acted, cost_inr)
                        VALUES (:id, :v, :s, 0, :verdict, 1, :sum, :acted, 0)
                        """).param("id", id).param("v", videoId).param("s", stage)
                .param("verdict", approve ? "approve" : "ask").param("sum", text)
                .param("acted", approve ? "approved_spend" : "asked").update();
        return new com.projectv.media.MediaTypes.SpendDecision(approve,
                approve ? text.replace("would APPROVE this", "APPROVED this by itself") : text);
    }

    // ------------------------------------------------------------------------------------------- P2: learning

    public record LearnedRules(List<String> rules) {
    }

    /**
     * Learns from the owner's notes: distils the owner's recent feedback on a stage (and where they disagreed with the
     * Producer) into at most 6 short rules, replacing the stage's earlier learned rules. Owner-written rules are kept.
     */
    public List<String> learn(UUID videoId, String stage) {
        if (llm == null) {
            return List.of();
        }
        List<String> notes = jdbc.sql("""
                        SELECT decision || ': ' || notes FROM approval
                        WHERE stage = :s AND actor = 'owner' AND notes IS NOT NULL AND btrim(notes) <> ''
                        ORDER BY ts DESC LIMIT 15
                        """).param("s", stage).query(String.class).list();
        List<String> disagreements = jdbc.sql("""
                        SELECT 'Producer said ' || verdict || ' (' || COALESCE(summary, '') || '); owner chose ' || owner_decision
                               || COALESCE(' with notes: ' || owner_notes, '')
                        FROM producer_decision WHERE stage = :s AND agreed = false ORDER BY decided_at DESC LIMIT 8
                        """).param("s", stage).query(String.class).list();
        if (notes.isEmpty() && disagreements.isEmpty()) {
            return List.of();
        }
        List<String> owner = jdbc.sql("SELECT rule_text FROM producer_rule WHERE stage = :s AND source = 'owner' AND enabled")
                .param("s", stage).query(String.class).list();
        String system = """
                You keep the taste rules of a video studio's AI producer. From the owner's feedback, write at most 6 short,
                concrete rules (max 15 words each) that capture what the owner consistently wants or rejects for this
                stage. Only rules the feedback supports; no duplicates of the owner's own rules. Return JSON {"rules": [...]}.""";
        String user = "Stage: " + stage + "\nOwner's own rules: " + owner + "\nOwner feedback (newest first):\n- "
                + String.join("\n- ", notes) + "\nDisagreements with the Producer:\n- " + String.join("\n- ", disagreements);
        LearnedRules out = llm.call(videoId, "PRODUCER learn rules " + stage, system, user, LearnedRules.class);
        List<String> rules = out == null || out.rules() == null ? List.of()
                : out.rules().stream().filter(r -> r != null && !r.isBlank()).limit(6).toList();
        jdbc.sql("DELETE FROM producer_rule WHERE stage = :s AND source = 'learned'").param("s", stage).update();
        for (String r : rules) {
            jdbc.sql("INSERT INTO producer_rule (id, stage, rule_text, source, evidence) VALUES (:id, :s, :t, 'learned', :e)")
                    .param("id", UUID.randomUUID()).param("s", stage).param("t", r.strip()).param("e", notes.size()).update();
        }
        return rules;
    }

    public List<Map<String, Object>> rulesList() {
        return jdbc.sql("SELECT id, stage, rule_text, source, enabled, evidence, updated_at FROM producer_rule ORDER BY stage, source DESC, evidence DESC")
                .query().listOfRows();
    }

    public void addRule(String stage, String text) {
        jdbc.sql("INSERT INTO producer_rule (id, stage, rule_text, source) VALUES (:id, :s, :t, 'owner')")
                .param("id", UUID.randomUUID()).param("s", stage == null || stage.isBlank() ? null : stage).param("t", text.strip()).update();
    }

    public void updateRule(UUID id, String text, Boolean enabled) {
        jdbc.sql("""
                        UPDATE producer_rule SET rule_text = COALESCE(CAST(:t AS text), rule_text),
                               enabled = COALESCE(CAST(:e AS boolean), enabled),
                               source = CASE WHEN CAST(:t AS text) IS NULL THEN source ELSE 'owner' END, updated_at = now()
                        WHERE id = :id
                        """).param("t", text).param("e", enabled).param("id", id).update();
    }

    public void deleteRule(UUID id) {
        jdbc.sql("DELETE FROM producer_rule WHERE id = :id").param("id", id).update();
    }

    /** End-of-video summary: what the Producer did by itself, and what it spent on that video. */
    public Map<String, Object> videoSummary(UUID videoId) {
        Map<String, Object> m = jdbc.sql("""
                        SELECT count(*) FILTER (WHERE acted = 'approved') AS approved,
                               count(*) FILTER (WHERE acted = 'redo') AS redone,
                               count(*) FILTER (WHERE acted = 'asked') AS asked,
                               count(*) FILTER (WHERE acted = 'approved_spend') AS spend_approved,
                               COALESCE(sum(cost_inr), 0) AS review_cost
                        FROM producer_decision WHERE video_id = :v
                        """).param("v", videoId).query().singleRow();
        BigDecimal spent = jdbc.sql("SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry WHERE video_id = :v")
                .param("v", videoId).query(BigDecimal.class).single();
        Map<String, Object> out = new java.util.LinkedHashMap<>(m);
        out.put("spentInr", spent);
        out.put("budgetInr", pricing.producerBudgetInr(plan(videoId).durationSec() <= 180));
        out.put("trust", trust());
        out.put("deciding", deciding());
        return out;
    }

    /**
     * P1 advice on a money step (no AI call): within the per-video Producer budget and below the "ask me above" line,
     * the Producer would approve it; otherwise it would ask the owner. Advise-only: the owner still approves.
     */
    public String spendAdvice(UUID videoId, BigDecimal estimateInr) {
        boolean shortVideo = plan(videoId).durationSec() <= 180;
        BigDecimal budgetInr = pricing.producerBudgetInr(shortVideo);
        BigDecimal askAbove = pricing.producerAskAboveInr();
        BigDecimal spent = jdbc.sql("SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry WHERE video_id = :v")
                .param("v", videoId).query(BigDecimal.class).single();
        boolean fits = estimateInr.compareTo(askAbove) <= 0 && spent.add(estimateInr).compareTo(budgetInr) <= 0;
        return fits ? "AI Producer: would APPROVE this (≈₹%s, under your ₹%s 'ask me' line; ₹%s of the ₹%s video budget used)."
                .formatted(r0(estimateInr), r0(askAbove), r0(spent), r0(budgetInr))
                : "AI Producer: would ASK YOU (≈₹%s; 'ask me above' ₹%s; ₹%s of the ₹%s video budget used)."
                .formatted(r0(estimateInr), r0(askAbove), r0(spent), r0(budgetInr));
    }

    private static String r0(BigDecimal v) {
        return v.setScale(0, RoundingMode.HALF_UP).toPlainString();
    }

    /**
     * The owner decided on a stage the Producer reviewed: keep the decision and notes with its latest open review
     * (a lesson) and whether they agreed (approve = approve; reject = it had recommended a redo).
     */
    public static void recordOwnerDecision(org.springframework.jdbc.core.simple.JdbcClient jdbc, UUID videoId, String stage,
                                           String decision, String notes) {
        jdbc.sql("""
                        UPDATE producer_decision SET owner_decision = :d, owner_notes = :n, decided_at = now(),
                               agreed = CASE WHEN :d = 'approve' THEN verdict = 'approve' ELSE verdict = 'redo' END
                        WHERE id = (SELECT id FROM producer_decision WHERE video_id = :v AND stage = :s
                                      AND owner_decision IS NULL ORDER BY created_at DESC LIMIT 1)
                        """).param("d", decision).param("n", notes).param("v", videoId).param("s", stage).update();
    }

    // ------------------------------------------------------------------------------------------- read model

    /** Every Producer review of a video, newest first, with the owner's decision and agreement. */
    public List<Map<String, Object>> decisions(UUID videoId) {
        return jdbc.sql("""
                        SELECT id, stage, attempt, verdict, confidence, summary, redo_notes, items_json::text AS items, model,
                               cost_inr, owner_decision, owner_notes, agreed, created_at, decided_at
                        FROM producer_decision WHERE video_id = :v ORDER BY created_at DESC
                        """).param("v", videoId).query().listOfRows();
    }

    /** How often the Producer's recommendation matched the owner's decision (all videos). */
    public Map<String, Object> agreement() {
        Map<String, Object> m = jdbc.sql("""
                        SELECT count(*) FILTER (WHERE agreed IS NOT NULL) AS decided,
                               count(*) FILTER (WHERE agreed) AS agreed
                        FROM producer_decision
                        """).query().singleRow();
        long decided = ((Number) m.get("decided")).longValue();
        long agreed = ((Number) m.get("agreed")).longValue();
        return Map.of("decided", decided, "agreed", agreed, "rate", decided == 0 ? 0 : Math.round(agreed * 100.0 / decided));
    }
}
