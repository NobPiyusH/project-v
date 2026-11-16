package com.projectv.agents;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.client.ResponseEntity;
import org.springframework.ai.chat.metadata.Usage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
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

    public Llm(ObjectProvider<ChatModel> chatModel, CostLedger ledger, Pricing pricing, BudgetGuard budget,
               @Value("${projectv.agents.timeout-seconds:150}") long timeoutSeconds,
               @Value("${projectv.models.text}") String model) {
        this.chatModel = chatModel;
        this.ledger = ledger;
        this.pricing = pricing;
        this.budget = budget;
        this.timeoutSeconds = timeoutSeconds;
        this.model = model;
    }

    public String model() {
        return model;
    }

    /** Calls the model for a typed result. Records the cost against {@code videoId}. Throws on timeout or failure. */
    public <T> T call(UUID videoId, String purpose, String system, String user, Class<T> type) {
        ChatModel cm = chatModel.getIfAvailable();
        if (cm == null) {
            throw new IllegalStateException("No LLM configured: set GEMINI_API_KEY in .env");
        }
        budget.require(BigDecimal.ONE, purpose); // a text call costs well under ₹1; refuse only when the cap is hit
        try {
            ResponseEntity<ChatResponse, T> r = CompletableFuture
                    .supplyAsync(() -> ChatClient.create(cm).prompt().system(system).user(user).call().responseEntity(type))
                    .get(timeoutSeconds, TimeUnit.SECONDS);
            com.projectv.media.MediaTypes.Usage usage = toUsage(r.response());
            ledger.record(videoId, BudgetGuard.GEMINI, pricing.textInr(usage), BigDecimal.valueOf(usage.totalTokens()),
                    "tokens", true, purpose + " (" + model + ")");
            if (r.entity() == null) {
                throw new IllegalStateException(purpose + ": the model returned no parsable result");
            }
            return r.entity();
        } catch (TimeoutException e) {
            throw new IllegalStateException(purpose + ": " + model + " did not answer within " + timeoutSeconds + "s");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(purpose + " interrupted");
        } catch (ExecutionException e) {
            Throwable c = e.getCause() == null ? e : e.getCause();
            throw new IllegalStateException(purpose + " failed: " + c.getClass().getSimpleName() + ": " + c.getMessage(), c);
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
