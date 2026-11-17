package com.projectv.ledger;

import com.projectv.media.MediaTypes.Usage;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.io.InputStream;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

/** Prices from config/providers.yaml (`pricing:`). Converts usage into rupees for the cost ledger. */
@Component
public class Pricing {

    private final Map<String, Object> pricing;

    public Pricing(@Value("${projectv.config-dir}") String configDir) throws IOException {
        try (InputStream in = Files.newInputStream(Path.of(configDir, "providers.yaml"))) {
            Map<String, Object> root = new Yaml().load(in);
            @SuppressWarnings("unchecked")
            Map<String, Object> p = (Map<String, Object>) root.get("pricing");
            if (p == null) {
                throw new IllegalStateException("providers.yaml has no pricing section");
            }
            this.pricing = p;
        }
    }

    public double fx() {
        return num(pricing, "fxInrPerUsd");
    }

    public BigDecimal geminiMonthlyCapInr() {
        return BigDecimal.valueOf(num(gemini(), "monthlyCapInr"));
    }

    public BigDecimal geminiReserveInr() {
        return BigDecimal.valueOf(num(gemini(), "reserveInr"));
    }

    public BigDecimal textInr(Usage u) {
        return tokensInr(map(gemini(), "text"), u);
    }

    public BigDecimal ttsInr(Usage u) {
        return tokensInr(map(gemini(), "tts"), u);
    }

    public BigDecimal imageInr(String size) {
        Map<String, Object> per = map(map(gemini(), "image"), "perImageUsd");
        Object v = per.getOrDefault(size, per.get("1K"));
        return inr(((Number) v).doubleValue());
    }

    public BigDecimal musicInr(int songs) {
        return inr(num(map(gemini(), "music"), "perSongUsd") * songs);
    }

    public BigDecimal groundingInr(int queries) {
        return inr(num(gemini(), "groundingPerQueryUsd") * queries);
    }

    public BigDecimal modalGpuInr(String gpu, double seconds) {
        Map<String, Object> modal = map(pricing, "modal");
        return inr(num(map(modal, "gpuPerSecondUsd"), gpu) * seconds);
    }

    /** Hard monthly cap on Claude (owner-funded), in rupees. */
    public BigDecimal claudeMonthlyCapInr() {
        return inr(num(map(pricing, "anthropic"), "monthlyCapUsd"));
    }

    /** Claude Sonnet cost; thinking tokens are billed as output. */
    public BigDecimal claudeInr(long inputTokens, long outputTokens) {
        Map<String, Object> rates = map(map(pricing, "anthropic"), "sonnet");
        return inr(inputTokens / 1e6 * num(rates, "inputPerMTokUsd") + outputTokens / 1e6 * num(rates, "outputPerMTokUsd"));
    }

    /** Claude's server-side web search, billed per search. */
    public BigDecimal claudeSearchInr(long searches) {
        return inr(searches / 1000.0 * num(map(pricing, "anthropic"), "webSearchPer1000Usd"));
    }

    /** Hard monthly cap on GPU work (Modal), in rupees. Default: the free credit. */
    public BigDecimal gpuMonthlyCapInr() {
        return inr(num(map(pricing, "modal"), "monthlyCapUsd"));
    }

    /** Expected H100 render seconds for one 81-frame Wan clip of this kind (draft / final). */
    public double wanSecondsPer81Frames(String kind) {
        return num(map(map(pricing, "modal"), "wanSecondsPer81Frames"), kind);
    }

    /** Container start + model load, billed once per GPU container a batch uses. */
    public double gpuContainerStartSeconds() {
        return num(map(pricing, "modal"), "containerStartSeconds");
    }

    private BigDecimal tokensInr(Map<String, Object> rates, Usage u) {
        if (u == null) {
            return BigDecimal.ZERO;
        }
        // Thinking tokens are billed as output tokens.
        double usd = u.promptTokens() / 1e6 * num(rates, "inputPerMTokUsd")
                + (u.outputTokens() + u.thoughtTokens()) / 1e6 * num(rates, "outputPerMTokUsd");
        return inr(usd);
    }

    private BigDecimal inr(double usd) {
        return BigDecimal.valueOf(usd * fx()).setScale(4, RoundingMode.HALF_UP);
    }

    private Map<String, Object> gemini() {
        return map(pricing, "gemini");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Map<String, Object> m, String key) {
        Object v = m.get(key);
        if (!(v instanceof Map)) {
            throw new IllegalStateException("Missing pricing key: " + key);
        }
        return (Map<String, Object>) v;
    }

    private static double num(Map<String, Object> m, String key) {
        Object v = m.get(key);
        if (!(v instanceof Number n)) {
            throw new IllegalStateException("Missing pricing number: " + key);
        }
        return n.doubleValue();
    }
}
