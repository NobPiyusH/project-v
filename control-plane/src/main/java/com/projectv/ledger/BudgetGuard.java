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

    /** Temporal must not retry a budget refusal: activity options name this class (setDoNotRetry). */
    public static final String BUDGET_ERROR = BudgetExceededException.class.getName();

    /** This calendar month's (UTC) spend for providers whose id starts with {@code providerPrefix}. */
    private BigDecimal spentThisMonth(String providerPrefix) {
        return jdbc.sql("""
                        SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry
                        WHERE starts_with(provider_id, :p) AND ts >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
                        """)
                .param("p", providerPrefix).query(BigDecimal.class).single();
    }

    public BigDecimal geminiSpentThisMonth() {
        return spentThisMonth(GEMINI);
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
        return spentThisMonth(CLAUDE);
    }

    public boolean canSpendClaude(BigDecimal estimateInr) {
        return pricing.claudeMonthlyCapInr().subtract(claudeSpentThisMonth()).compareTo(estimateInr) >= 0;
    }

    // ---- AICredits: errors-only text fallback with its own all-time cap (the prepaid wallet)

    public static final String AICREDITS = "aicredits";

    public BigDecimal aicreditsSpent() {
        return jdbc.sql("SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry WHERE provider_id = :p")
                .param("p", AICREDITS).query(BigDecimal.class).single();
    }

    public boolean canSpendAicredits(BigDecimal estimateInr) {
        return pricing.aicreditsCapInr().subtract(aicreditsSpent()).compareTo(estimateInr) >= 0;
    }

    // ---- GPU (Modal): its own monthly cap, the free credit by default (owner rule: ask before spending money)

    public static final String GPU_PREFIX = "modal_";

    public BigDecimal gpuSpentThisMonth() {
        return spentThisMonth(GPU_PREFIX);
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

    /** Share of a budget bucket (gemini | gpu | claude) still left this month, 0..1; 1 for unbudgeted buckets. */
    public double remainingShare(String bucket) {
        BigDecimal cap;
        BigDecimal left;
        switch (bucket) {
            case "gemini" -> {
                cap = pricing.geminiMonthlyCapInr();
                left = geminiRemaining();
            }
            case "gpu" -> {
                cap = pricing.gpuMonthlyCapInr();
                left = gpuRemaining();
            }
            case "claude" -> {
                cap = pricing.claudeMonthlyCapInr();
                left = cap.subtract(claudeSpentThisMonth());
            }
            default -> {
                return 1;
            }
        }
        return cap.signum() <= 0 ? 0 : Math.max(0, Math.min(1, left.doubleValue() / cap.doubleValue()));
    }

    public static class BudgetExceededException extends RuntimeException {
        public BudgetExceededException(String message) {
            super(message);
        }
    }
}
