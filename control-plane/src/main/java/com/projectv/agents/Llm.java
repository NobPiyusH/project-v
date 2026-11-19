package com.projectv.agents;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.prompt.ChatOptions;
import org.springframework.ai.chat.client.ResponseEntity;
import org.springframework.ai.chat.metadata.Usage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeoutException;

/**
 * One place for structured LLM calls by the agents: time limit, budget check, and a CostEntry for every call.
 */
@Component
public class Llm {

    private final ObjectProvider<ChatModel> chatModel;
    private final CostLedger ledger;
    private final Pricing pricing;
    private final BudgetGuard budget;
    private final long timeoutSeconds;
    private final String model;
    private final String fallbacks;
    private final OpenAiCompatChat nvidia;
    private final ClaudeChat claude;

    private record Out<T>(T entity, com.projectv.media.MediaTypes.Usage usage) {
    }

    public Llm(ObjectProvider<ChatModel> chatModel, CostLedger ledger, Pricing pricing, BudgetGuard budget, OpenAiCompatChat nvidia,
               ClaudeChat claude,
               @Value("${projectv.agents.timeout-seconds:150}") long timeoutSeconds,
               @Value("${projectv.models.text}") String model,
               @Value("${projectv.models.text-fallbacks:}") String fallbacks) {
        this.chatModel = chatModel;
        this.ledger = ledger;
        this.pricing = pricing;
        this.budget = budget;
        this.timeoutSeconds = timeoutSeconds;
        this.model = model;
        this.fallbacks = fallbacks;
        this.nvidia = nvidia;
        this.claude = claude;
    }

    public String model() {
        return model;
    }

    /** Calls the model for a typed result. Records the cost against {@code videoId}. Throws on timeout or failure. */
    public <T> T call(UUID videoId, String purpose, String system, String user, Class<T> type) {
        return call(videoId, purpose, system, user, type, null);
    }

    /**
     * @param preferred a model tried before the usual list (e.g. {@code claude:claude-sonnet-5-5} for the script and
     *                  shot plan); skipped when it isn't configured or its monthly cap is used up
     */
    public <T> T call(UUID videoId, String purpose, String system, String user, Class<T> type, String preferred) {
        ChatModel cm = chatModel.getIfAvailable();
        if (cm == null) {
            throw new IllegalStateException("No LLM configured: set GEMINI_API_KEY in .env");
        }
        budget.require(BigDecimal.ONE, purpose); // a text call costs well under ₹1; refuse only when the cap is hit
        List<String> models = ModelFallback.models(
                preferred != null && !preferred.isBlank() && (!ClaudeChat.handles(preferred) || claude.configured())
                        ? preferred + "," + model : model, fallbacks);
        try {
            ModelFallback.Attempt<Out<T>> a = ModelFallback.run(models,
                    m -> ClaudeChat.handles(m) ? Math.max(timeoutSeconds, 180) : timeoutSeconds, purpose, m -> {
                if (ClaudeChat.handles(m)) {
                    // Owner-funded: stop at the monthly cap and let Gemini do it (≈₹10 = the most a capped call costs).
                    if (!budget.canSpendClaude(BigDecimal.TEN)) {
                        throw new IllegalStateException("unavailable: Claude monthly cap reached");
                    }
                    ClaudeChat.Result<T> c = claude.call(m, system, user, type, (in, out) -> ledger.record(videoId,
                            BudgetGuard.CLAUDE, pricing.claudeInr(in, out), BigDecimal.valueOf(in + out), "tokens", false,
                            purpose + " (" + m + ", " + in + " in / " + out + " out)"));
                    return new Out<>(c.entity(), new com.projectv.media.MediaTypes.Usage(c.inputTokens(), c.outputTokens(), 0,
                            c.inputTokens() + c.outputTokens()));
                }
                if (OpenAiCompatChat.handles(m)) {
                    OpenAiCompatChat.Result<T> n = nvidia.call(m, system, user, type);
                    return new Out<>(n.entity(), new com.projectv.media.MediaTypes.Usage(
                            n.promptTokens(), n.completionTokens(), 0, n.totalTokens()));
                }
                ResponseEntity<ChatResponse, T> g = ChatClient.create(cm).prompt()
                        .options(ChatOptions.builder().model(m))
                        .system(system).user(user).call().responseEntity(type);
                return new Out<>(g.entity(), toUsage(g.response()));
            });
            Out<T> r = a.value();
            com.projectv.media.MediaTypes.Usage usage = r.usage();
            if (ClaudeChat.handles(a.model())) {
                // Already recorded when the response arrived (a paid call is recorded even if it couldn't be used).
            } else if (OpenAiCompatChat.handles(a.model())) {
                // NVIDIA NIM and Groq free tiers: record the call (₹0) so every call stays in the ledger.
                ledger.record(videoId, OpenAiCompatChat.provider(a.model()), BigDecimal.ZERO, BigDecimal.valueOf(usage.totalTokens()),
                        "tokens", true, purpose + " (" + a.model() + ")");
            } else {
                ledger.record(videoId, BudgetGuard.GEMINI, pricing.textInr(usage), BigDecimal.valueOf(usage.totalTokens()),
                        "tokens", true, purpose + " (" + a.model() + ")");
            }
            if (r.entity() == null) {
                throw new IllegalStateException(purpose + ": the model returned no parsable result");
            }
            return r.entity();
        } catch (TimeoutException e) {
            throw new IllegalStateException(purpose + ": no model in " + models + " answered within " + timeoutSeconds + "s");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(purpose + " interrupted");
        } catch (ExecutionException e) {
            Throwable c = e.getCause() == null ? e : e.getCause();
            throw new IllegalStateException(purpose + " failed: " + c.getClass().getSimpleName() + ": " + c.getMessage(), c);
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new IllegalStateException(purpose + " failed: " + e.getMessage(), e);
        }
    }

    private static com.projectv.media.MediaTypes.Usage toUsage(ChatResponse response) {
        if (response == null || response.getMetadata() == null || response.getMetadata().getUsage() == null) {
            return com.projectv.media.MediaTypes.Usage.NONE;
        }
        Usage u = response.getMetadata().getUsage();
        long prompt = u.getPromptTokens() == null ? 0 : u.getPromptTokens();
        long completion = u.getCompletionTokens() == null ? 0 : u.getCompletionTokens();
        long total = u.getTotalTokens() == null ? prompt + completion : u.getTotalTokens();
        // Gemini bills thinking tokens as output; the total includes them, so attribute the remainder to thinking.
        long thinking = Math.max(0, total - prompt - completion);
        return new com.projectv.media.MediaTypes.Usage(prompt, completion, thinking, total);
    }
}
