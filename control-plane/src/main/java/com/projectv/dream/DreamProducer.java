package com.projectv.dream;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.ProducerAdvice;
import com.projectv.media.MediaTypes.ProducerItem;
import com.projectv.media.MediaTypes.ProducerItemVerdict;
import com.projectv.media.MediaTypes.ProducerReviewRequest;
import com.projectv.media.MediaTypes.ProducerReviewResult;
import com.projectv.media.MediaTypes.SpendDecision;
import com.projectv.production.ProducerService;
import com.projectv.production.RedoStopRule;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * AI Producer for Dream Shorts (autopilot, owner 19 Nov 2026). Same reviewer as the documentary pipeline (Gemini
 * reads, looks, watches, listens), with a rubric for music-only surreal Shorts. Its decisions go to the same
 * {@code producer_decision} table (linked to the Short), so the agreement score and learned rules are shared.
 * Limits are the owner's: ₹100 per Short, ask above ₹50 per decision, 3 redo rounds per step; on an autopilot Short
 * also the daily cap.
 */
@Component
public class DreamProducer implements DreamProducerActivities {

    static final String RUBRIC_BASE = """
            You are the producer of a premium, privately owned YouTube Shorts channel (Dreamscapes): short surreal,
            dreamlike vertical videos with music only, no voice. Quality first, cost second. Judge each item strictly but
            fairly. score 0-10 (7+ = good enough to publish); ok = score >= 7. problem: the main issue in a few words, or
            "" if none. fix: a PRECISE instruction for a redo (a rewritten prompt), or "" if ok. verdict: approve (all
            ok), redo (specific items are not ok and a redo would fix them), ask (unsure, taste call, or needs the
            owner). confidence 0-1. redoNotes: the precise notes for the redo, naming the items. summary: 1-2 plain
            sentences for the owner.""";

    static final Map<String, String> RUBRICS = Map.of(
            "WRITE", "Plan: one believable dream world shared by every shot, a scroll-stopping first shot, each shot a clear "
                    + "visual moment with a real camera move (human, not effects), feels calm and wondrous, not generic AI cliché.",
            "PICTURES", "Pictures: each matches its scene, strong vertical composition, one consistent world and palette, "
                    + "no text or letters, no artefacts, deformities or melted objects, looks real not like an effect.",
            "CLIPS", "Clips: WATCH each clip. It must really move like a real handheld/drone camera (no near-still or "
                    + "slideshow), match its scene and motion, no morphing, glitches, flicker or black frames.",
            "MUSIC", "Music: LISTEN. It fits the Short's mood (dreamy, calm, wondrous), starts strong, no quiet stretch, "
                    + "no abrupt end, no clipping.",
            "FINISH", "Finished Short: WATCH and LISTEN. Hook in the first second, cuts land on the music, every shot moves, "
                    + "one consistent look, no black or broken moments, music level right, a satisfying ending or loop.");

    private final JdbcClient jdbc;
    private final ObjectMapper json;
    private final DreamService dreams;
    private final ProducerService producer;
    private final Pricing pricing;
    private final String model;

    public DreamProducer(JdbcClient jdbc, ObjectMapper json, DreamService dreams, ProducerService producer, Pricing pricing,
                         @Value("${projectv.producer.model:gemini-3.6-flash}") String model) {
        this.jdbc = jdbc;
        this.json = json;
        this.dreams = dreams;
        this.producer = producer;
        this.pricing = pricing;
        this.model = model;
    }

    // ------------------------------------------------------------------------------------------- review

    @Override
    public ProducerReviewRequest reviewRequest(String dreamId, String step) {
        if (!RUBRICS.containsKey(step)) {
            return null;
        }
        UUID id = UUID.fromString(dreamId);
        Map<String, Object> d = dreams.dream(id);
        if (DreamLooks.unfiltered(d)) {
            return null; // the Producer judges "feel-good, no people": not the owner's own, unfiltered prompt
        }
        List<Map<String, Object>> shots = dreams.shots(id);
        List<ProducerItem> items = resolve(items(step, d, shots));
        if (items.isEmpty()) {
            return null;
        }
        String context = "Short: " + Objects.toString(d.get("title"), "(untitled)") + "\nOwner's idea: " + d.get("idea")
                + "\nLook: " + d.get("look") + ", " + shots.size() + " shots, vertical 9:16, music only.";
        return new ProducerReviewRequest(dreamId, "DREAM_" + step, model, RUBRIC_BASE + "\n" + RUBRICS.get(step), context,
                rules(step), items);
    }

    static List<ProducerItem> items(String step, Map<String, Object> d, List<Map<String, Object>> shots) {
        List<ProducerItem> out = new ArrayList<>();
        switch (step) {
            case "WRITE" -> {
                StringBuilder b = new StringBuilder("Title: ").append(Objects.toString(d.get("title"), "")).append('\n');
                for (Map<String, Object> s : shots) {
                    b.append(n(s)).append(". ").append(s.get("scene")).append("  [camera: ").append(s.get("motion_prompt")).append("]\n");
                }
                if (!shots.isEmpty()) {
                    out.add(new ProducerItem("plan", "Plan", "text", null, b.toString(), "A Dream Short for: " + d.get("idea")));
                }
            }
            case "PICTURES", "CLIPS" -> {
                String col = "PICTURES".equals(step) ? "picture_uri" : "clip_uri";
                for (Map<String, Object> s : shots) {
                    if (s.get(col) != null) {
                        out.add(new ProducerItem("shot-" + n(s), "Shot " + n(s), "PICTURES".equals(step) ? "image" : "video",
                                (String) s.get(col), null, "Shot " + n(s) + ": " + s.get("scene")
                                + ("CLIPS".equals(step) ? ". Motion: " + s.get("motion_prompt") : "")));
                    }
                }
            }
            case "MUSIC", "FINISH" -> {
                Object asset = d.get("MUSIC".equals(step) ? "music_asset_id" : "final_asset_id");
                if (asset instanceof UUID a) {
                    out.add(new ProducerItem("MUSIC".equals(step) ? "music" : "final", "MUSIC".equals(step) ? "Music" : "Finished Short",
                            "MUSIC".equals(step) ? "audio" : "video", "@" + a, null, "For: " + d.get("idea")));
                }
            }
            default -> {
            }
        }
        return out;
    }

    private static int n(Map<String, Object> shot) {
        return ((Number) shot.get("ord")).intValue() + 1;
    }

    /** "@assetId" placeholders from {@link #items} become the asset's storage URI. */
    private List<ProducerItem> resolve(List<ProducerItem> items) {
        return items.stream().map(i -> i.uri() != null && i.uri().startsWith("@")
                ? new ProducerItem(i.id(), i.label(), i.kind(), jdbc.sql("SELECT uri_hot FROM asset WHERE id = :a")
                .param("a", UUID.fromString(i.uri().substring(1))).query(String.class).single(), i.text(), i.expect())
                : i).toList();
    }

    private List<String> rules(String step) {
        return jdbc.sql("""
                        SELECT rule_text FROM producer_rule WHERE enabled AND (stage IS NULL OR stage = :s)
                        ORDER BY evidence DESC, updated_at DESC LIMIT 20
                        """).param("s", "DREAM_" + step).query(String.class).list();
    }

    @Override
    @Transactional
    public ProducerAdvice saveReview(String dreamId, String step, int attempt, ProducerReviewResult r) {
        UUID id = UUID.fromString(dreamId);
        BigDecimal cost = r.usage() == null ? BigDecimal.ZERO : pricing.textInr(r.usage());
        if (cost.signum() > 0) {
            jdbc.sql("""
                            INSERT INTO cost_entry (id, video_id, dream_short_id, provider_id, amount_inr, units, unit_type, estimated, note)
                            VALUES (:id, NULL, :d, :p, :amt, :u, 'tokens', true, :note)
                            """).param("id", UUID.randomUUID()).param("d", id).param("p", BudgetGuard.GEMINI).param("amt", cost)
                    .param("u", BigDecimal.valueOf(r.usage().totalTokens()))
                    .param("note", "PRODUCER review of Dream Short " + step + " (" + r.model() + ")").update();
        }
        UUID decision = UUID.randomUUID();
        String verdict = r.verdict() == null ? "ask" : r.verdict().toLowerCase(Locale.ROOT);
        // Stop rule: the same issue twice in a row gets one firm prompt fix, then goes to the owner (not another re-roll).
        RedoStopRule.Outcome stop = RedoStopRule.check(jdbc, json, "dream_short_id", id, "DREAM_" + step, verdict,
                r.items(), r.summary(), r.redoNotes());
        jdbc.sql("""
                        INSERT INTO producer_decision (id, dream_short_id, stage, attempt, verdict, confidence, summary, redo_notes,
                                                       items_json, model, cost_inr, stop_rule, stop_cause)
                        VALUES (:id, :d, :s, :a, :verdict, :c, :sum, :notes, CAST(:items AS jsonb), :m, :cost, :stop, :cause)
                        """)
                .param("id", decision).param("d", id).param("s", "DREAM_" + step).param("a", attempt).param("verdict", verdict)
                .param("c", BigDecimal.valueOf(r.confidence()).setScale(3, RoundingMode.HALF_UP)).param("sum", r.summary())
                .param("notes", stop.redoNotes()).param("items", json.writeValueAsString(r.items() == null ? List.of() : r.items()))
                .param("m", r.model()).param("cost", cost).param("stop", ProducerService.stopRule(stop))
                .param("cause", stop.cause() == null ? null : stop.cause().key()).update();
        List<String> weak = r.items() == null ? List.of() : r.items().stream().filter(i -> !i.ok()).map(ProducerItemVerdict::id).toList();
        String what = switch (verdict) {
            case "approve" -> "recommends APPROVE";
            case "redo" -> "recommends REDO of " + weak.size() + " item" + (weak.size() == 1 ? "" : "s");
            default -> "recommends you DECIDE (unsure)";
        };
        String text = "AI Producer %s (%.0f%% sure): %s%s%s".formatted(what, r.confidence() * 100, Objects.toString(r.summary(), ""),
                stop.note() == null ? "" : " " + stop.note(),
                stop.redoNotes() == null || stop.redoNotes().isBlank() ? "" : " Suggested redo: " + stop.redoNotes());
        dreams.evidence().event(id, step, "producer", null, text, Map.of("verdict", stop.verdict(), "weak", weak));
        return new ProducerAdvice(stop.verdict(), r.confidence(), text, stop.redoNotes(), decision.toString(), weak);
    }

    // ------------------------------------------------------------------------------------------- acting alone

    @Override
    public boolean decides(String dreamId) {
        Boolean own = jdbc.sql("SELECT producer_decides FROM dream_short WHERE id = :d").param("d", UUID.fromString(dreamId))
                .query(Boolean.class).optional().orElse(false);
        return own || producer.deciding();
    }

    /** What this Short has cost so far. */
    BigDecimal spent(UUID id) {
        return jdbc.sql("SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry WHERE dream_short_id = :d").param("d", id)
                .query(BigDecimal.class).single();
    }

    /** The most this Short may cost: the Producer's per-Short budget, and the daily cap when it is an autopilot Short. */
    BigDecimal limit(UUID id) {
        BigDecimal budget = pricing.producerBudgetInr(true);
        BigDecimal daily = jdbc.sql("""
                        SELECT s.daily_cap_inr FROM autopilot_setting s
                        WHERE EXISTS (SELECT 1 FROM autopilot_run r WHERE r.dream_short_id = :d)
                        """).param("d", id).query(BigDecimal.class).optional().orElse(null);
        return daily == null ? budget : budget.min(daily);
    }

    @Override
    public boolean mayRedo(String dreamId, int round) {
        UUID id = UUID.fromString(dreamId);
        return round <= pricing.producerSetting("roundsPerStage") && spent(id).compareTo(limit(id)) < 0;
    }

    @Override
    public List<String> shotIds(String dreamId, List<String> itemIds) {
        List<Integer> ords = itemIds == null ? List.of() : itemIds.stream().filter(i -> i.startsWith("shot-"))
                .map(i -> Integer.parseInt(i.substring(5)) - 1).toList();
        if (ords.isEmpty()) {
            return List.of();
        }
        return jdbc.sql("SELECT id::text FROM dream_shot WHERE dream_short_id = :d AND ord IN (:o) ORDER BY ord")
                .param("d", UUID.fromString(dreamId)).param("o", ords).query(String.class).list();
    }

    /** Within the limits: approve. Pure rule so it can be tested without a database. */
    static boolean fits(BigDecimal estimate, BigDecimal askAbove, BigDecimal spent, BigDecimal limit) {
        return estimate.compareTo(askAbove) <= 0 && spent.add(estimate).compareTo(limit) <= 0;
    }

    @Override
    @Transactional
    public SpendDecision spendDecision(String dreamId, BigDecimal estimateInr) {
        UUID id = UUID.fromString(dreamId);
        BigDecimal askAbove = pricing.producerAskAboveInr();
        BigDecimal spent = spent(id);
        BigDecimal limit = limit(id);
        boolean approve = decides(dreamId) && fits(estimateInr, askAbove, spent, limit);
        String text = (approve ? "AI Producer APPROVED this by itself" : "AI Producer: ASKING YOU")
                + " (≈₹%s; 'ask me above' ₹%s; ₹%s of this Short's ₹%s limit used).".formatted(r0(estimateInr), r0(askAbove),
                r0(spent), r0(limit));
        jdbc.sql("""
                        INSERT INTO producer_decision (id, dream_short_id, stage, attempt, verdict, confidence, summary, acted, cost_inr)
                        VALUES (:id, :d, 'DREAM_CLIPS_SPEND', 0, :v, 1, :sum, :acted, 0)
                        """).param("id", UUID.randomUUID()).param("d", id).param("v", approve ? "approve" : "ask")
                .param("sum", text).param("acted", approve ? "approved_spend" : "asked").update();
        return new SpendDecision(approve, text);
    }

    @Override
    public void markActed(String decisionId, String acted) {
        producer.markActed(UUID.fromString(decisionId), acted);
    }

    /** The owner decided on a step the Producer reviewed: keep it with the latest open review (agreement score). */
    public void recordOwnerDecision(UUID dreamId, String step, String decision, String notes) {
        jdbc.sql("""
                        UPDATE producer_decision SET owner_decision = :dec, owner_notes = :n, decided_at = now(),
                               agreed = CASE WHEN :dec = 'approve' THEN verdict = 'approve' ELSE verdict = 'redo' END
                        WHERE id = (SELECT id FROM producer_decision WHERE dream_short_id = :d AND stage = :s
                                      AND owner_decision IS NULL ORDER BY created_at DESC LIMIT 1)
                        """).param("dec", decision).param("n", notes).param("d", dreamId).param("s", "DREAM_" + step).update();
    }

    public List<Map<String, Object>> decisions(UUID dreamId) {
        return jdbc.sql("""
                        SELECT id, stage, attempt, verdict, confidence, summary, redo_notes, acted, cost_inr, owner_decision, agreed, created_at
                        FROM producer_decision WHERE dream_short_id = :d ORDER BY created_at DESC
                        """).param("d", dreamId).query().listOfRows();
    }

    private static String r0(BigDecimal v) {
        return v.setScale(0, RoundingMode.HALF_UP).toPlainString();
    }
}
