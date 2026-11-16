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
 * Free backup text models on NVIDIA's hosted API (OpenAI-compatible). Used only as fallbacks after Gemini: model
 * ids in the fallback list written as {@code nvidia:<model>} are routed here. Structured output via Spring AI's
 * {@link BeanOutputConverter} (format instructions in the prompt, JSON parsed back), since these models don't all
 * support a JSON schema parameter.
 */
@Component
public class NvidiaChat {

    public static final String PREFIX = "nvidia:";

    public record Result<T>(T entity, long promptTokens, long completionTokens, long totalTokens) {
    }

    private final String apiKey;
    private final RestClient http;

    public NvidiaChat(@Value("${projectv.nvidia.api-key:}") String apiKey,
                      @Value("${projectv.nvidia.base-url:https://integrate.api.nvidia.com/v1}") String baseUrl) {
        this.apiKey = apiKey;
        SimpleClientHttpRequestFactory f = new SimpleClientHttpRequestFactory();
        f.setConnectTimeout(15_000);
        f.setReadTimeout(300_000); // the caller's ModelFallback time limit is the real limit
        this.http = RestClient.builder().baseUrl(baseUrl).requestFactory(f).build();
    }

    public static boolean handles(String model) {
        return model != null && model.startsWith(PREFIX);
    }

    @SuppressWarnings("unchecked")
    public <T> Result<T> call(String model, String system, String user, Class<T> type) {
        if (apiKey == null || apiKey.isBlank()) {
            // Reported as a model problem so the fallback list moves on.
            throw new IllegalStateException("not supported: NVIDIA_API_KEY is not set");
        }
        BeanOutputConverter<T> converter = new BeanOutputConverter<>(type);
        Map<String, Object> body = Map.of(
                "model", model.substring(PREFIX.length()),
                "temperature", 0.6,
                "max_tokens", 8192,
                "messages", List.of(
                        Map.of("role", "system", "content", system),
                        Map.of("role", "user", "content", user + "\n\n" + converter.getFormat())));
        Map<String, Object> r = http.post().uri("/chat/completions")
                .header("Authorization", "Bearer " + apiKey) // never logged
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .body(Map.class);
        if (r == null || !(r.get("choices") instanceof List<?> choices) || choices.isEmpty()) {
            throw new IllegalStateException("unavailable: NVIDIA returned no choices");
        }
        Map<String, Object> msg = (Map<String, Object>) ((Map<String, Object>) choices.getFirst()).get("message");
        String text = msg == null ? null : (String) msg.get("content");
        if (text == null || text.isBlank()) {
            throw new IllegalStateException("unavailable: NVIDIA returned an empty answer");
        }
        T entity = converter.convert(clean(text));
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
