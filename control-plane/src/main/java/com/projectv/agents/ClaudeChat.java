package com.projectv.agents;

import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.MessageCreateParams;
import com.anthropic.models.messages.OutputConfig;
import org.springframework.ai.converter.BeanOutputConverter;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.stream.Collectors;

/**
 * Claude via the official Anthropic Java SDK. Owner-funded ($5, 17 Nov 2026), so used only where it lifts quality most
 * (script and shot plan), at medium effort with a capped output. Model ids in a fallback list written as
 * {@code claude:<model>} are routed here; without a key the list simply moves on to Gemini.
 * Structured output: Spring AI's format instructions in the prompt, JSON parsed back (same as the other backups).
 */
@Component
public class ClaudeChat {

    public static final String PREFIX = "claude:";
    /** Hard ceiling per call (output + thinking): ~$0.16 on Sonnet 5.5. Raised from 8,000 on 22 Nov 2026: a 26-shot plan with diagram specs was cut off. */
    static final long MAX_TOKENS = 16_000;

    public record Result<T>(T entity, String model, long inputTokens, long outputTokens) {
    }

    private final AnthropicClient client;

    public ClaudeChat(@Value("${projectv.anthropic.api-key:}") String apiKey) {
        this.client = apiKey == null || apiKey.isBlank() ? null : AnthropicOkHttpClient.builder()
                .apiKey(apiKey)
                .timeout(Duration.ofSeconds(170))
                .maxRetries(1)
                .build();
    }

    public static boolean handles(String model) {
        return model != null && model.startsWith(PREFIX);
    }

    public boolean configured() {
        return client != null;
    }

    /** A raw request (e.g. with server-side web search); the caller records the cost from the response's usage. */
    public Message send(MessageCreateParams params) {
        if (client == null) {
            throw new IllegalStateException("not supported: no ANTHROPIC_API_KEY configured");
        }
        return client.messages().create(params);
    }

    /** @param onUsage called as soon as a response arrives (input, output tokens), so a paid call is always recorded */
    public <T> Result<T> call(String model, String system, String user, Class<T> type,
                              java.util.function.BiConsumer<Long, Long> onUsage) {
        return call(model, system, user, type, OutputConfig.Effort.MEDIUM, onUsage);
    }

    /** As above with a chosen thinking effort (cost lever; MEDIUM is the A/B-tested default). */
    public <T> Result<T> call(String model, String system, String user, Class<T> type, OutputConfig.Effort effort,
                              java.util.function.BiConsumer<Long, Long> onUsage) {
        if (client == null) {
            throw new IllegalStateException("not supported: no ANTHROPIC_API_KEY configured");
        }
        String id = model.substring(PREFIX.length());
        BeanOutputConverter<T> converter = new BeanOutputConverter<>(type);
        MessageCreateParams params = MessageCreateParams.builder()
                .model(id)
                .maxTokens(MAX_TOKENS)
                .outputConfig(OutputConfig.builder().effort(effort).build())
                .system(system)
                .addUserMessage(user + "\n\n" + converter.getFormat())
                .build();
        Message response = client.messages().create(params);
        String text = response.content().stream()
                .flatMap(block -> block.text().stream())
                .map(t -> t.text())
                .collect(Collectors.joining("\n"));
        long in = response.usage().inputTokens();
        long out = response.usage().outputTokens();
        onUsage.accept(in, out);
        if (response.stopReason().map(r -> r.toString().toLowerCase().contains("max_tokens")).orElse(false)) {
            throw new IllegalStateException("unavailable: " + model + " answer was cut off (token limit); tokens " + in + "/" + out);
        }
        if (text.isBlank()) {
            throw new IllegalStateException("unavailable: " + model + " returned no text");
        }
        try {
            return new Result<>(converter.convert(OpenAiCompatChat.clean(text)), model, in, out);
        } catch (RuntimeException e) {
            throw new IllegalStateException("unavailable: " + model + " returned an unreadable answer ("
                    + e.getClass().getSimpleName() + ")", e);
        }
    }
}
