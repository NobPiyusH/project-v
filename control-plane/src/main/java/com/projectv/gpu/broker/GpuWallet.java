package com.projectv.gpu.broker;

import com.projectv.ledger.Pricing;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.util.function.ToDoubleFunction;

/**
 * Budget wallet for rented GPUs, kept in our own cost ledger: spent = this month's cost_entry rows of the provider.
 * A provider with no cap in config (or 0) is blocked. Amounts are USD; the ledger stores INR, converted by fx.
 */
@Component
public class GpuWallet {

    private final BrokerConfig config;
    private final ToDoubleFunction<String> spentUsdThisMonth;

    @Autowired
    public GpuWallet(BrokerConfig config, JdbcClient jdbc, Pricing pricing) {
        this(config, provider -> jdbc.sql("""
                        SELECT COALESCE(sum(amount_inr), 0) FROM cost_entry
                        WHERE provider_id = :p AND ts >= date_trunc('month', now())
                        """).param("p", provider).query(Double.class).single() / pricing.fx());
    }

    /** For tests and other ledgers. */
    public GpuWallet(BrokerConfig config, ToDoubleFunction<String> spentUsdThisMonth) {
        this.config = config;
        this.spentUsdThisMonth = spentUsdThisMonth;
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
        return null;
    }
}
