package com.projectv.agents;

import org.springframework.ai.converter.BeanOutputConverter;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;

/**
 * Backup text models on OpenAI-compatible APIs (NVIDIA NIM, Groq). Used as fallbacks after Gemini: model ids in the
 * fallback list written as {@code <provider>:<model>} (e.g. {@code nvidia:nvidia/nemotron-3-ultra-550b-a55b},
 * {@code groq:openai/gpt-oss-120b}) are routed here. A provider with no key configured is skipped. Structured output via Spring AI's
 * {@link BeanOutputConverter} (format instructions in the prompt, JSON parsed back), since these models don't all
 * support a JSON schema parameter.
 */
@Component
public class OpenAiCompatChat {

    private record Provider(String baseUrl, String apiKey) {
    }

    public record Result<T>(T entity, long promptTokens, long completionTokens, long totalTokens) {
    }

    private final java.util.Map<String, Provider> providers;
    private final RestClient http;

    public OpenAiCompatChat(@Value("${projectv.nvidia.api-key:}") String nvidiaKey,
                            @Value("${projectv.nvidia.base-url:https://integrate.api.nvidia.com/v1}") String nvidiaUrl,
                            @Value("${projectv.groq.api-key:}") String groqKey,
                            @Value("${projectv.groq.base-url:https://api.groq.com/openai/v1}") String groqUrl,
                            @Value("${projectv.openrouter.api-key:}") String openRouterKey,
                            @Value("${projectv.openrouter.base-url:https://openrouter.ai/api/v1}") String openRouterUrl) {
        this.providers = java.util.Map.of(
                "nvidia", new Provider(nvidiaUrl, nvidiaKey),
                "groq", new Provider(groqUrl, groqKey),
                "openrouter", new Provider(openRouterUrl, openRouterKey));
        SimpleClientHttpRequestFactory f = new SimpleClientHttpRequestFactory();
        f.setConnectTimeout(15_000);
        f.setReadTimeout(300_000); // the caller's ModelFallback time limit is the real limit
        this.http = RestClient.builder().requestFactory(f).build();
    }

    /** True for {@code nvidia:...}, {@code groq:...} and {@code openrouter:...} model ids. */
    public static boolean handles(String model) {
        return model != null && (model.startsWith("nvidia:") || model.startsWith("groq:")
                || model.startsWith("openrouter:"));
    }

    /** Provider name of a routed model id, for the cost ledger. */
    public static String provider(String model) {
        return model.substring(0, model.indexOf(':'));
    }

    @SuppressWarnings("unchecked")
    public <T> Result<T> call(String model, String system, String user, Class<T> type) {
        Provider prov = providers.get(provider(model));
        if (prov == null || prov.apiKey() == null || prov.apiKey().isBlank()) {
            // Reported as a model problem so the fallback list moves on.
            throw new IllegalStateException("not supported: no API key for " + provider(model));
        }
        BeanOutputConverter<T> converter = new BeanOutputConverter<>(type);
        Map<String, Object> body = Map.of(
                "model", model.substring(model.indexOf(':') + 1),
                "temperature", 0.6,
                "max_tokens", 8192,
                "messages", List.of(
                        Map.of("role", "system", "content", system),
                        Map.of("role", "user", "content", user + "\n\n" + converter.getFormat())));
        Map<String, Object> r = http.post().uri(prov.baseUrl() + "/chat/completions")
                .header("Authorization", "Bearer " + prov.apiKey()) // never logged
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .body(Map.class);
        if (r == null || !(r.get("choices") instanceof List<?> choices) || choices.isEmpty()) {
            throw new IllegalStateException("unavailable: the provider returned no choices");
        }
        Map<String, Object> choice = (Map<String, Object>) choices.getFirst();
        Map<String, Object> msg = (Map<String, Object>) choice.get("message");
        String text = msg == null ? null : (String) msg.get("content");
        if (text == null || text.isBlank()) {
            throw new IllegalStateException("unavailable: the provider returned an empty answer");
        }
        // A cut-off or unreadable answer is this model's problem: report it so the next model is tried
        // (16 Nov 2026: Groq's answer for a shot plan stopped mid-JSON and failed the whole stage).
        if ("length".equals(choice.get("finish_reason"))) {
            throw new IllegalStateException("unavailable: " + model + " answer was cut off (token limit)");
        }
        T entity;
        try {
            entity = converter.convert(clean(text));
        } catch (RuntimeException e) {
            throw new IllegalStateException("unavailable: " + model + " returned an unreadable answer ("
                    + e.getClass().getSimpleName() + ")", e);
        }
        Map<String, Object> u = r.get("usage") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        long p = num(u.get("prompt_tokens"));
        long c = num(u.get("completion_tokens"));
        long t = u.containsKey("total_tokens") ? num(u.get("total_tokens")) : p + c;
        return new Result<>(entity, p, c, t);
    }

    /** Drops reasoning blocks and text around the JSON object some models add. */
    static String clean(String text) {
        String s = text.replaceAll("(?s)<think>.*?</think>", "").strip();
        int a = s.indexOf('{');
        int b = s.lastIndexOf('}');
        return a >= 0 && b > a ? s.substring(a, b + 1) : s;
    }

    private static long num(Object o) {
        return o instanceof Number n ? n.longValue() : 0;
    }
}
