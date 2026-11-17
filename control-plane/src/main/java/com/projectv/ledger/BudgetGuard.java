package com.projectv.ledger;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;

/**
 * Hard monthly cap on paid Gemini spend (HANDOVER §11.5). Google Cloud budgets only alert; this actually stops.
 * The month is the calendar month in UTC.
 */
@Component
public class BudgetGuard {

    public static final String GEMINI = "gemini_api";

    private final JdbcClient jdbc;
    private final Pricing pricing;

    public BudgetGuard(JdbcClient jdbc, Pricing pricing) {
        this.jdbc = jdbc;
        this.pricing = pricing;
    }

    public BigDecimal geminiSpentThisMonth() {
        return jdbc.sql("""
                        SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry
                        WHERE provider_id = :p AND ts >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                        """)
                .param("p", GEMINI).query(BigDecimal.class).single();
    }

    public BigDecimal geminiRemaining() {
        return pricing.geminiMonthlyCapInr().subtract(geminiSpentThisMonth());
    }

    /** True if an estimated Gemini spend still fits under the cap. */
    public boolean canSpend(BigDecimal estimateInr) {
        return geminiRemaining().compareTo(estimateInr) >= 0;
    }

    /** Throws a non-retryable failure when the estimate would break the cap; the stage then waits for the owner. */
    public void require(BigDecimal estimateInr, String what) {
        if (!canSpend(estimateInr)) {
            throw new BudgetExceededException("Monthly Gemini cap of ₹" + pricing.geminiMonthlyCapInr()
                    + " would be exceeded by " + what + " (≈₹" + estimateInr + "; ₹" + geminiRemaining().max(BigDecimal.ZERO)
                    + " left). Raise the cap in config/providers.yaml or switch this stage to Manual.");
        }
    }

    // ---- Claude (Anthropic): owner-funded, its own monthly cap; over it, callers fall back to Gemini

    public static final String CLAUDE = "anthropic";

    public BigDecimal claudeSpentThisMonth() {
        return jdbc.sql("""
                        SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry
                        WHERE provider_id = :p AND ts >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                        """).param("p", CLAUDE).query(BigDecimal.class).single();
    }

    public boolean canSpendClaude(BigDecimal estimateInr) {
        return pricing.claudeMonthlyCapInr().subtract(claudeSpentThisMonth()).compareTo(estimateInr) >= 0;
    }

    // ---- GPU (Modal): its own monthly cap, the free credit by default (owner rule: ask before spending money)

    public static final String GPU_PREFIX = "modal_";

    public BigDecimal gpuSpentThisMonth() {
        return jdbc.sql("""
                        SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry
                        WHERE left(provider_id, 6) = 'modal_'
                          AND ts >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                        """).query(BigDecimal.class).single();
    }

    public BigDecimal gpuRemaining() {
        return pricing.gpuMonthlyCapInr().subtract(gpuSpentThisMonth());
    }

    /** Throws (non-retryable) when a GPU job's estimate would break the monthly GPU cap. */
    public void requireGpu(BigDecimal estimateInr, String what) {
        if (gpuRemaining().compareTo(estimateInr) < 0) {
            throw new BudgetExceededException("Monthly GPU cap of ₹" + pricing.gpuMonthlyCapInr().setScale(0, java.math.RoundingMode.HALF_UP)
                    + " would be exceeded by " + what + " (≈₹" + estimateInr.setScale(0, java.math.RoundingMode.HALF_UP) + "; ₹"
                    + gpuRemaining().max(BigDecimal.ZERO).setScale(0, java.math.RoundingMode.HALF_UP)
                    + " left). Raise modal.monthlyCapUsd in config/providers.yaml to allow paid GPU time.");
        }
    }

    public static class BudgetExceededException extends RuntimeException {
        public BudgetExceededException(String message) {
            super(message);
        }
    }
}
