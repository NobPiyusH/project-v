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

    public static class BudgetExceededException extends RuntimeException {
        public BudgetExceededException(String message) {
            super(message);
        }
    }
}
