package com.projectv.routing;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.BudgetGuard.BudgetExceededException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * M3a router core (docs/plans/M3-router.md): ONE place that decides which provider runs a paid job, and why.
 * <ol>
 *   <li>candidates come from the stage (each with its own cost estimate for this job);</li>
 *   <li>filter: in the registry, enabled, has the capability, not cooling down after failures, fits its budget
 *       bucket (Gemini / GPU / Claude caps);</li>
 *   <li>choose: the owner's preferred provider when it is eligible. Otherwise (owner, 18 Nov 2026) only providers
 *       reaching the job's quality floor compete, scored {@code min(quality, enough) - lambda x cost} with lambda
 *       growing as that provider's budget runs low; when none reaches the floor, the best-scoring one is a noted
 *       fallback;</li>
 *   <li>none eligible: the job is deferred with a non-retryable budget failure, so the stage waits for the owner
 *       with the reasons;</li>
 *   <li>every decision is logged in {@code routing_decision} (CLAUDE.md rule 6).</li>
 * </ol>
 * Runs inside activities only (workflow determinism): workflows just see the chosen provider as data.
 */
@Component
public class Router {

    /** One provider that could run the job, with its estimated cost for it. */
    public record Candidate(String providerId, BigDecimal estimateInr) {
    }

    /** The decision. {@code decisionId} is null when the decision was not logged (an estimate preview). */
    public record Choice(String providerId, String reason, UUID decisionId, List<Map<String, Object>> considered) {
    }

    static final int MAX_COOLDOWN_SEC = 30 * 60;

    private final JdbcClient jdbc;
    private final ObjectMapper json;
    private final BudgetGuard budget;
    private final com.projectv.ledger.Pricing pricing;

    public Router(JdbcClient jdbc, ObjectMapper json, BudgetGuard budget, com.projectv.ledger.Pricing pricing) {
        this.jdbc = jdbc;
        this.json = json;
        this.budget = budget;
        this.pricing = pricing;
    }

    /** min(quality, enough) - lambda x cost; lambda = lambdaPerInr / max(0.1, share of the budget left). */
    double score(int quality, BigDecimal costInr, String bucket, double enough) {
        double lambda = pricing.routingLambdaPerInr() / Math.max(0.1, budget.remainingShare(bucket));
        return Math.min(quality, enough) - lambda * costInr.doubleValue();
    }

    /**
     * @param preferred the owner's preferred provider for this job (config or per-video), or null
     * @param log       false for a preview (cost estimate shown before approval): nothing is written
     */
    public Choice choose(UUID videoId, String stage, String capability, List<Candidate> candidates, String preferred,
                         boolean log) {
        List<Map<String, Object>> considered = new ArrayList<>();
        double[] q = pricing.routingQuality(capability);
        double floor = q[0];
        double enough = q[1];
        Candidate best = null;          // best score among providers reaching the quality floor
        double bestScore = Double.NEGATIVE_INFINITY;
        Candidate fallback = null;      // best score among eligible providers below the floor
        double fallbackScore = Double.NEGATIVE_INFINITY;
        Candidate pref = null;
        for (Candidate c : candidates) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("provider", c.providerId());
            row.put("estimateInr", c.estimateInr().setScale(2, RoundingMode.HALF_UP));
            String why = ineligible(c, capability);
            Map<String, Object> p = provider(c.providerId());
            int quality = p == null ? 0 : intOf(p.get("quality"), 5);
            row.put("quality", quality);
            row.put("eligible", why == null);
            if (why != null) {
                row.put("why", why);
            } else {
                if (c.providerId().equals(preferred)) {
                    pref = c;
                }
                double s = score(quality, c.estimateInr(), String.valueOf(p.get("budget")), enough);
                row.put("score", Math.round(s * 100) / 100.0);
                if (quality >= floor) {
                    if (s > bestScore) {
                        best = c;
                        bestScore = s;
                    }
                } else {
                    row.put("belowFloor", floor);
                    if (s > fallbackScore) {
                        fallback = c;
                        fallbackScore = s;
                    }
                }
            }
            considered.add(row);
        }
        Candidate chosen = pref != null ? pref : best != null ? best : fallback;
        String reason;
        if (chosen == null) {
            reason = "No provider can run " + capability + " now: " + String.join("; ", considered.stream()
                    .map(r -> r.get("provider") + " (" + r.get("why") + ")").toList());
        } else if (chosen == pref) {
            reason = chosen.providerId() + ": the owner's preference, and it fits its budget";
        } else {
            reason = chosen.providerId() + (chosen == best ? ": best score at or above the quality floor ("
                    + (int) floor + ")" : ": fallback BELOW the quality floor (" + (int) floor + "), nothing better fits")
                    + (preferred != null ? " (preferred " + preferred + " is not available: "
                    + considered.stream().filter(r -> preferred.equals(r.get("provider"))).map(r -> String.valueOf(r.get("why")))
                    .findFirst().orElse("not a candidate") + ")" : "");
        }
        UUID id = log ? logDecision(videoId, stage, capability, considered, chosen, reason) : null;
        if (chosen == null) {
            // deferred: the same non-retryable failure as a budget stop, so the stage waits for the owner
            throw new BudgetExceededException(reason + ". Raise a cap, wait for a cooldown, or switch this stage to Manual.");
        }
        return new Choice(chosen.providerId(), reason, id, considered);
    }

    /** Why this candidate can't run the job now, or null when it can. */
    String ineligible(Candidate c, String capability) {
        Map<String, Object> p = provider(c.providerId());
        if (p == null) {
            return "not in the provider registry";
        }
        if (!Boolean.TRUE.equals(p.get("enabled"))) {
            return "disabled";
        }
        if (Boolean.TRUE.equals(p.get("owner_disabled"))) {
            return "switched off by the owner";
        }
        List<String> caps = json.readValue((String) p.get("capabilities"), new TypeReference<List<String>>() {
        });
        if (!caps.contains(capability)) {
            return "can't do " + capability;
        }
        Instant cooling = cooldownUntil(c.providerId());
        if (cooling != null && cooling.isAfter(Instant.now())) {
            return "cooling down after failures until " + cooling;
        }
        String bucket = String.valueOf(p.get("budget"));
        boolean fits = switch (bucket) {
            case "gemini" -> budget.canSpend(c.estimateInr());
            case "gpu" -> budget.gpuRemaining().compareTo(c.estimateInr()) >= 0;
            case "claude" -> budget.canSpendClaude(c.estimateInr());
            default -> true; // free / local / manual
        };
        return fits ? null : "≈₹" + c.estimateInr().setScale(0, RoundingMode.HALF_UP) + " would break the monthly " + bucket + " cap";
    }

    // ------------------------------------------------------------------------------------------- health

    /** A provider failed (not the job's own fault): cool it down, doubling up to 30 min. */
    public void reportFailure(String providerId, String error) {
        int fails = jdbc.sql("""
                        INSERT INTO provider_health (provider_id, fails, last_error) VALUES (:p, 1, :e)
                        ON CONFLICT (provider_id) DO UPDATE SET fails = provider_health.fails + 1, last_error = :e, updated_at = now()
                        RETURNING fails
                        """).param("p", providerId).param("e", error == null ? null : error.substring(0, Math.min(500, error.length())))
                .query(Integer.class).single();
        long sec = Math.min(MAX_COOLDOWN_SEC, 60L << Math.min(fails - 1, 10));
        jdbc.sql("UPDATE provider_health SET cooldown_until = :u WHERE provider_id = :p")
                .param("u", OffsetDateTime.now().plus(Duration.ofSeconds(sec))).param("p", providerId).update();
    }

    /**
     * A finished batch: any success clears the provider's cooldown; a batch where EVERY job failed cools it down.
     * Its own transaction, so it is kept even when the caller then fails and rolls back.
     */
    @org.springframework.transaction.annotation.Transactional(
            propagation = org.springframework.transaction.annotation.Propagation.REQUIRES_NEW)
    public void reportBatch(String providerId, int ok, int failed, String sampleError) {
        if (ok > 0) {
            reportSuccess(providerId);
        } else if (failed > 0) {
            reportFailure(providerId, sampleError);
        }
    }

    public void reportSuccess(String providerId) {
        jdbc.sql("UPDATE provider_health SET fails = 0, cooldown_until = NULL, updated_at = now() WHERE provider_id = :p AND fails > 0")
                .param("p", providerId).update();
    }

    Instant cooldownUntil(String providerId) {
        return jdbc.sql("SELECT cooldown_until FROM provider_health WHERE provider_id = :p").param("p", providerId)
                .query(OffsetDateTime.class).optional().map(OffsetDateTime::toInstant).orElse(null);
    }

    // ------------------------------------------------------------------------------------------- owner controls (M3b)

    /** Switch a provider off (or back on) for routing; survives restarts. */
    public void setOwnerDisabled(String providerId, boolean off) {
        int n = jdbc.sql("UPDATE provider SET owner_disabled = :off, updated_at = now() WHERE id = :p")
                .param("off", off).param("p", providerId).update();
        if (n == 0) {
            throw new IllegalArgumentException("Unknown provider " + providerId);
        }
    }

    /** Clear a cooldown by hand (the owner knows the provider is back). */
    public void resetHealth(String providerId) {
        jdbc.sql("DELETE FROM provider_health WHERE provider_id = :p").param("p", providerId).update();
    }

    /** The router's providers (those with a quality tier) with health and this month's decision counts. */
    public List<Map<String, Object>> providers() {
        return jdbc.sql("""
                        SELECT p.id, p.name, p.kind, p.enabled, p.owner_disabled, p.capabilities_json::text AS capabilities,
                               (p.config_json->>'quality')::int AS quality, COALESCE(p.config_json->>'budget', 'none') AS budget,
                               h.fails, h.cooldown_until, h.last_error,
                               (SELECT count(*) FROM routing_decision d WHERE d.chosen_provider_id = p.id
                                  AND d.created_at >= date_trunc('month', now())) AS chosen_this_month
                        FROM provider p LEFT JOIN provider_health h ON h.provider_id = p.id
                        WHERE p.config_json->>'quality' IS NOT NULL ORDER BY p.config_json->>'budget', p.id
                        """).query().listOfRows();
    }

    /** Each budget bucket the router scores against: cap, spent, share left (what makes lambda grow). */
    public List<Map<String, Object>> budgets() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (String b : List.of("gemini", "gpu", "claude")) {
            out.add(Map.of("bucket", b, "shareLeft", Math.round(budget.remainingShare(b) * 1000) / 1000.0,
                    "lambda", Math.round(pricing.routingLambdaPerInr() / Math.max(0.1, budget.remainingShare(b)) * 10000) / 10000.0));
        }
        return out;
    }

    // ------------------------------------------------------------------------------------------- read model

    /** Decisions of one video, newest first (for the dashboard's "why this provider?"). */
    public List<Map<String, Object>> decisions(UUID videoId) {
        return jdbc.sql("""
                        SELECT id, stage, capability, chosen_provider_id, reason_text, candidates_json::text AS candidates,
                               overridden_by_owner, created_at
                        FROM routing_decision WHERE video_id = :v ORDER BY created_at DESC
                        """).param("v", videoId).query().listOfRows();
    }

    private Map<String, Object> provider(String id) {
        return jdbc.sql("""
                        SELECT enabled, owner_disabled, capabilities_json::text AS capabilities, config_json->>'quality' AS quality,
                               COALESCE(config_json->>'budget', 'none') AS budget
                        FROM provider WHERE id = :id
                        """).param("id", id).query().listOfRows().stream().findFirst().orElse(null);
    }

    /** A decision made elsewhere (the media worker: voice engine, laptop vs cloud smoothing), logged the same way. */
    public UUID recordExternal(UUID videoId, String stage, String capability, String chosen, String reason,
                               List<Map<String, Object>> candidates) {
        UUID id = UUID.randomUUID();
        jdbc.sql("""
                        INSERT INTO routing_decision (id, video_id, stage, capability, candidates_json, chosen_provider_id, reason_text)
                        VALUES (:id, :v, :s, :c, CAST(:cands AS jsonb), :chosen, :r)
                        """)
                .param("id", id).param("v", videoId).param("s", stage).param("c", capability)
                .param("cands", json.writeValueAsString(candidates == null ? List.of() : candidates))
                .param("chosen", chosen).param("r", reason).update();
        return id;
    }

    private UUID logDecision(UUID videoId, String stage, String capability, List<Map<String, Object>> considered,
                             Candidate chosen, String reason) {
        UUID id = UUID.randomUUID();
        jdbc.sql("""
                        INSERT INTO routing_decision (id, video_id, stage, capability, candidates_json, chosen_provider_id, reason_text)
                        VALUES (:id, :v, :s, :c, CAST(:cands AS jsonb), :chosen, :r)
                        """)
                .param("id", id).param("v", videoId).param("s", stage).param("c", capability)
                .param("cands", json.writeValueAsString(considered))
                .param("chosen", chosen == null ? null : chosen.providerId()).param("r", reason).update();
        return id;
    }

    private static int intOf(Object o, int dflt) {
        if (o == null) {
            return dflt;
        }
        try {
            return Integer.parseInt(o.toString().trim());
        } catch (NumberFormatException e) {
            return dflt;
        }
    }
}
