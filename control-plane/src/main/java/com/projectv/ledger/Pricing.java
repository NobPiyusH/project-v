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

    /**
     * Google's bill vs the studio's estimates (Nov 1-17 2026: ₹372.62 billed before savings vs ₹243.43 in the ledger,
     * ×1.53 incl. some untracked test calls): every Gemini estimate is scaled so the ₹400 cap protects real spend.
     */
    private BigDecimal calibrated(BigDecimal inr) {
        Object c = gemini().get("calibration");
        return c instanceof Number n ? inr.multiply(BigDecimal.valueOf(n.doubleValue())) : inr;
    }

    public BigDecimal textInr(Usage u) {
        return calibrated(tokensInr(map(gemini(), "text"), u));
    }

    public BigDecimal ttsInr(Usage u) {
        return calibrated(tokensInr(map(gemini(), "tts"), u));
    }

    public BigDecimal imageInr(String size) {
        Map<String, Object> per = map(map(gemini(), "image"), "perImageUsd");
        Object v = per.getOrDefault(size, per.get("1K"));
        return calibrated(inr(((Number) v).doubleValue()));
    }

    public BigDecimal musicInr(int songs) {
        return calibrated(inr(num(map(gemini(), "music"), "perSongUsd") * songs));
    }

    public BigDecimal groundingInr(int queries) {
        return calibrated(inr(num(gemini(), "groundingPerQueryUsd") * queries));
    }

    /** Gemini Omni Flash clip: video output tokens per second at this resolution (config), estimated before the call. */
    public BigDecimal omniInr(double seconds, String resolution) {
        Map<String, Object> o = map(gemini(), "omni");
        return omniTokensInr((long) (num(map(o, "tokensPerSecond"), resolution) * seconds));
    }

    /** Gemini Omni Flash: actual output tokens reported by Google. */
    public BigDecimal omniTokensInr(long outputTokens) {
        return calibrated(inr(num(map(gemini(), "omni"), "outputPerMTokUsd") * outputTokens / 1_000_000.0));
    }

    public BigDecimal modalGpuInr(String gpu, double seconds) {
        Map<String, Object> modal = map(pricing, "modal");
        return inr(num(map(modal, "gpuPerSecondUsd"), gpu) * seconds);
    }

    /** AI Producer: what it may approve per video (Short or long-form). */
    public BigDecimal producerBudgetInr(boolean shortVideo) {
        return BigDecimal.valueOf(num(map(pricing, "producer"), shortVideo ? "budgetPerShortInr" : "budgetPerLongInr"));
    }

    /** AI Producer: a numeric setting (redosPerItem, roundsPerStage, trustAfterDecisions, trustAgreementPct). */
    public int producerSetting(String key) {
        return (int) num(map(pricing, "producer"), key);
    }

    /** AI Producer: any single decision above this goes to the owner. */
    public BigDecimal producerAskAboveInr() {
        return BigDecimal.valueOf(num(map(pricing, "producer"), "askAboveInr"));
    }

    /** Router: quality points one rupee is worth with a full budget (pricing.routing.lambdaPerInr). */
    public double routingLambdaPerInr() {
        return num(map(pricing, "routing"), "lambdaPerInr");
    }

    /** Router: {minQuality, enoughQuality} for a job capability; defaults 0 / 10 when not configured. */
    public double[] routingQuality(String capability) {
        Object job = map(map(pricing, "routing"), "jobs").get(capability);
        if (!(job instanceof Map<?, ?> m)) {
            return new double[]{0, 10};
        }
        double min = m.get("minQuality") instanceof Number n ? n.doubleValue() : 0;
        double enough = m.get("enoughQuality") instanceof Number n ? n.doubleValue() : 10;
        return new double[]{min, enough};
    }

    /** Router estimate for Z-Image keyframes on the L40S (modal.zimageSecondsPerImage). */
    public BigDecimal zimageInr(int images) {
        return modalGpuInr("L40S", num(map(pricing, "modal"), "zimageSecondsPerImage") * images);
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

    /** ElevenLabs credits in rupees (the plan's price per 1,000 credits; 0 on the free plan). */
    public BigDecimal elevenLabsInr(long credits) {
        return inr(credits / 1000.0 * num(map(pricing, "elevenlabs"), "usdPer1000Credits"));
    }

    /** Claude's server-side web search, billed per search. */
    public BigDecimal claudeSearchInr(long searches) {
        return inr(searches / 1000.0 * num(map(pricing, "anthropic"), "webSearchPer1000Usd"));
    }

    /** Hard monthly cap on GPU work (Modal), in rupees. Default: the free credit. */
    public BigDecimal gpuMonthlyCapInr() {
        return inr(num(map(pricing, "modal"), "monthlyCapUsd"));
    }

    /** Modal's free credit each month, in US dollars. */
    public double modalMonthlyFreeUsd() {
        return num(map(pricing, "modal"), "monthlyFreeUsd");
    }

    /** Expected H100 render seconds for one 81-frame Wan clip of this kind (draft / final). */
    /** Extra GPU seconds the first clip in a fresh container takes (warm-up); 0 when not configured. */
    public double wanFirstClipExtraSeconds() {
        Object v = map(pricing, "modal").get("wanFirstClipExtraSeconds");
        return v instanceof Number x ? x.doubleValue() : 0;
    }

    public double wanSecondsPer81Frames(String kind) {
        return num(map(map(pricing, "modal"), "wanSecondsPer81Frames"), kind);
    }

    /** Seconds the Wan GPU stays warm after a Producer-run Short's clip batch, for its redo (0 = off). */
    public int wanRedoHoldSeconds() {
        Object v = map(pricing, "modal").get("wanRedoHoldSeconds");
        return v instanceof Number x ? x.intValue() : 0;
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
