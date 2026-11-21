package com.projectv.gpu.broker;

import com.projectv.ledger.Pricing;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.util.function.DoubleSupplier;
import java.util.function.ToDoubleFunction;

/**
 * Budget wallet for rented GPUs, kept in our own cost ledger: spent = this month's cost_entry rows of the provider.
 * A provider with no cap in config (or 0) is blocked. Amounts are USD; the ledger stores INR, converted by fx.
 */
@Component
public class GpuWallet {

    private final BrokerConfig config;
    private final ToDoubleFunction<String> spentUsdThisMonth;
    private final DoubleSupplier spentUsdToday;

    /** Starts with this text when the daily cap refuses a rental (the broker turns it into the Short's message). */
    public static final String DAILY_CAP = "daily GPU cap";

    @Autowired
    public GpuWallet(BrokerConfig config, JdbcClient jdbc, Pricing pricing) {
        this(config, provider -> jdbc.sql("""
                        SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry
                        WHERE provider_id = :p AND ts >= date_trunc('month', now())
                        """).param("p", provider).query(Double.class).single() / pricing.fx(),
                // Every GPU the studio paid for today (UTC): the broker's rented pods and Modal's GPU seconds.
                () -> jdbc.sql("""
                        SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry
                        WHERE (provider_id IN (:ps) OR provider_id LIKE 'modal%')
                          AND ts >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                        """).param("ps", providerIds(config)).query(Double.class).single() / pricing.fx());
    }

    /** For tests and other ledgers (no spend today). */
    public GpuWallet(BrokerConfig config, ToDoubleFunction<String> spentUsdThisMonth) {
        this(config, spentUsdThisMonth, () -> 0);
    }

    public GpuWallet(BrokerConfig config, ToDoubleFunction<String> spentUsdThisMonth, DoubleSupplier spentUsdToday) {
        this.config = config;
        this.spentUsdThisMonth = spentUsdThisMonth;
        this.spentUsdToday = spentUsdToday;
    }

    private static java.util.List<String> providerIds(BrokerConfig config) {
        var ids = new java.util.LinkedHashSet<>(config.order());
        ids.addAll(config.monthlyCapUsd().keySet());
        return ids.isEmpty() ? java.util.List.of("-") : java.util.List.copyOf(ids);
    }

    /** Booked GPU spend today (UTC), USD. */
    public double spentTodayUsd() {
        return spentUsdToday.getAsDouble();
    }

    /** The studio's daily GPU cap (USD, 0 = none). */
    public double dailyCapUsd() {
        return config.dailyCapUsd();
    }

    /**
     * Null when a rental of about {@code estimateUsd} fits under today's cap, else the reason. {@code runningUsd}: what the
     * pod that is on now has cost so far (not booked yet).
     */
    public String dailyRefusal(double estimateUsd, double runningUsd) {
        double cap = config.dailyCapUsd();
        if (cap <= 0) {
            return null;
        }
        double spent = spentTodayUsd() + Math.max(0, runningUsd);
        if (spent + estimateUsd > cap) {
            return DAILY_CAP + ": $%.2f spent today (UTC) of the $%.2f cap, this rental needs about $%.2f"
                    .formatted(spent, cap, estimateUsd);
        }
        return null;
    }

    public double remainingUsd(String provider) {
        double left = config.capUsd(provider) - spentUsdThisMonth.applyAsDouble(provider);
        if (config.totalCapUsd() > 0) {
            double totalSpent = config.monthlyCapUsd().keySet().stream().mapToDouble(spentUsdThisMonth).sum();
            left = Math.min(left, config.totalCapUsd() - totalSpent);
        }
        return left;
    }

    /** Null when the estimate fits, else the reason (shown in the routing decision). */
    public String refusal(String provider, double estimateUsd) {
        double cap = config.capUsd(provider);
        if (cap <= 0) {
            return "blocked: no monthly cap set (pricing.gpuBroker.providers." + provider + ".monthlyCapUsd is 0)";
        }
        double left = remainingUsd(provider);
        if (left < estimateUsd) {
            return "wallet: $%.2f left of the $%.2f cap, this rental needs about $%.2f".formatted(Math.max(0, left), cap, estimateUsd);
        }
        return dailyRefusal(estimateUsd, 0); // a new rental only starts when no pod is on, so nothing is running unbooked
    }
}
