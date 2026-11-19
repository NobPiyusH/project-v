package com.projectv.agents;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.routing.Router;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeoutException;
import java.util.regex.Pattern;

/**
 * Fallback for text calls via AICredits.in (owner's prepaid INR wallet, 19 Nov 2026). Not a router
 * provider: when the Gemini/Claude chain in {@link Llm} fails with an outage (5xx, 429, timeout, connection error)
 * or is refused by the Gemini/Claude monthly cap (owner, 19 Nov 2026), the same call is retried ONCE on the same model
 * through AICredits. Never on 4xx validation/content errors or the autopilot daily cap. Off unless enabled and a key is
 * set; all fallback spend is capped (pricing.aicredits.totalCapInr) from the cost ledger.
 */
@Component
public class AiCreditsFallback {

    private static final Logger log = LoggerFactory.getLogger(AiCreditsFallback.class);
    private static final Pattern OUTAGE_STATUS = Pattern.compile("\\b(429|5\\d\\d)\\b");
    /** The most one capped call can cost (Sonnet, 8k output); refuse when less than this is left. */
    private static final BigDecimal CALL_ESTIMATE_INR = BigDecimal.TEN;

    private final OpenAiCompatChat chat;
    private final CostLedger ledger;
    private final Pricing pricing;
    private final BudgetGuard budget;
    private final Router router;
    private final boolean enabled;

    public AiCreditsFallback(CostLedger ledger, Pricing pricing, BudgetGuard budget, Router router,
                             @Value("${projectv.aicredits.fallback-enabled:false}") boolean enabled,
                             @Value("${projectv.aicredits.api-key:}") String apiKey,
                             @Value("${projectv.aicredits.base-url:https://api.aicredits.in/v1}") String baseUrl) {
        this(new OpenAiCompatChat(baseUrl, apiKey), ledger, pricing, budget, router, enabled && apiKey != null && !apiKey.isBlank());
    }

    AiCreditsFallback(OpenAiCompatChat chat, CostLedger ledger, Pricing pricing, BudgetGuard budget, Router router,
                      boolean active) {
        this.chat = chat;
        this.ledger = ledger;
        this.pricing = pricing;
        this.budget = budget;
        this.router = router;
        this.enabled = active;
    }

    public boolean active() {
        return enabled;
    }

    /**
     * True for provider outages and the Gemini/Claude monthly-cap rejections (owner, 19 Nov 2026); false for 4xx
     * validation and content errors, and for the autopilot daily cap (never worked around).
     */
    static boolean shouldFallBack(Throwable t) {
        for (Throwable c = t; c != null; c = c.getCause()) {
            if (String.valueOf(c.getMessage()).toLowerCase(Locale.ROOT).contains("daily cap")) {
                return false;
            }
        }
        for (Throwable c = t; c != null; c = c.getCause()) {
            String msg = String.valueOf(c.getMessage()).toLowerCase(Locale.ROOT);
            if (c instanceof BudgetGuard.BudgetExceededException || msg.contains("cap reached")) {
                return true;
            }
        }
        for (Throwable c = t; c != null; c = c.getCause()) {
            if (c instanceof TimeoutException || c instanceof SocketTimeoutException || c instanceof ConnectException
                    || c instanceof UnknownHostException) {
                return true;
            }
            String cls = c.getClass().getSimpleName();
            if (cls.equals("HttpServerErrorException") || cls.equals("InternalServerException")
                    || cls.equals("RateLimitException") || cls.equals("ResourceAccessException")
                    || cls.equals("AnthropicIoException")) {
                return true;
            }
            String msg = String.valueOf(c.getMessage()).toLowerCase(Locale.ROOT);
            if (OUTAGE_STATUS.matcher(msg).find() || msg.contains("overloaded") || msg.contains("timed out")
                    || msg.contains("connection refused") || msg.contains("connection reset")) {
                return true;
            }
        }
        return false;
    }

    /**
     * The primary model to repeat: the first Gemini or Claude model in the list with an AICredits equivalent. Null when
     * there is none.
     */
    String primary(List<String> models) {
        for (String m : models) {
            if (OpenAiCompatChat.handles(m)) {
                continue;
            }
            String id = ClaudeChat.handles(m) ? m.substring(ClaudeChat.PREFIX.length()) : m;
            if (pricing.aicreditsModelId(id) == null) {
                continue;
            }
            return id;
        }
        return null;
    }

    /**
     * Repeats a failed text call once on AICredits. Returns null (caller rethrows the original error) when off,
     * not an outage or cap refusal, no equivalent model, or the fallback cap is used up. Records a CostEntry and a RoutingDecision.
     */
    public <T> T retry(UUID videoId, String purpose, List<String> models, Throwable failure, String system, String user,
                       Class<T> type) {
        if (!enabled || !shouldFallBack(failure)) {
            return null;
        }
        String primary = primary(models);
        if (primary == null) {
            return null;
        }
        if (!budget.canSpendAicredits(CALL_ESTIMATE_INR)) {
            log.warn("{}: AICredits fallback cap of ₹{} reached; not falling back", purpose, pricing.aicreditsCapInr());
            return null;
        }
        String aicId = pricing.aicreditsModelId(primary);
        String reason = "fallback: " + primary + " failed (" + String.valueOf(failure.getMessage()).lines().findFirst()
                .orElse("") + "); retried once on AICredits " + aicId;
        router.recordExternal(videoId, purpose, "llm.text", BudgetGuard.AICREDITS, reason,
                List.of(Map.of("providerId", primary, "failed", true), Map.of("providerId", BudgetGuard.AICREDITS, "model", aicId)));
        log.warn("{}: {}", purpose, reason);
        OpenAiCompatChat.Result<T> r = chat.call("aicredits:" + aicId, system, user, type);
        ledger.record(videoId, BudgetGuard.AICREDITS, pricing.aicreditsInr(primary, r.promptTokens(), r.completionTokens()),
                BigDecimal.valueOf(r.totalTokens()), "tokens", true,
                "fallback: " + purpose + " (" + aicId + " for " + primary + ", " + r.promptTokens() + " in / "
                        + r.completionTokens() + " out)");
        return r.entity();
    }
}
