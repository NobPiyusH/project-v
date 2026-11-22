package com.projectv.agents;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Function;

/**
 * Ordered model fallback for LLM calls (Work X). Google retires and overloads models without notice (16 Nov 2026:
 * gemini-2.5-flash "no longer available", 3.7/3.8 Flash hanging), so every call has a list: if a model is gone,
 * unknown, overloaded or hangs, the next one is tried. Billing, quota and content errors are not model problems and
 * fail immediately (trying another model would only spend more).
 */
public final class ModelFallback {

    private static final Logger log = LoggerFactory.getLogger(ModelFallback.class);

    public record Attempt<T>(String model, T value) {
    }

    private ModelFallback() {
    }

    public static List<String> models(String primary, String fallbacks) {
        List<String> out = new ArrayList<>();
        for (String spec : new String[]{primary, fallbacks}) {
            if (spec == null) {
                continue;
            }
            for (String m : spec.split(",")) {
                String t = m.strip();
                if (!t.isEmpty() && !out.contains(t)) {
                    out.add(t);
                }
            }
        }
        return out;
    }

    public static <T> Attempt<T> run(List<String> models, long timeoutSeconds, String what, Function<String, T> call)
            throws Exception {
        return run(models, m -> timeoutSeconds, what, call);
    }

    /** @param timeoutFor per-model time limit (a slower paid model gets longer, so its answer is never paid and lost) */
    public static <T> Attempt<T> run(List<String> models, Function<String, Long> timeoutFor, String what,
                                     Function<String, T> call) throws Exception {
        if (models.isEmpty()) {
            throw new IllegalStateException(what + ": no model configured");
        }
        for (int i = 0; i < models.size(); i++) {
            String m = models.get(i);
            boolean last = i == models.size() - 1;
            try {
                T value = CompletableFuture.supplyAsync(() -> call.apply(m)).get(timeoutFor.apply(m), TimeUnit.SECONDS);
                return new Attempt<>(m, value);
            } catch (TimeoutException e) {
                if (last) {
                    throw e;
                }
                log.warn("{}: {} did not answer within {}s, falling back to {}", what, m, timeoutFor.apply(m), models.get(i + 1));
            } catch (ExecutionException e) {
                Throwable cause = e.getCause() == null ? e : e.getCause();
                if (last || !isModelProblem(cause)) {
                    throw e;
                }
                log.warn("{}: {} failed ({}), falling back to {}", what, m, String.valueOf(cause.getMessage()).lines()
                        .findFirst().orElse(""), models.get(i + 1));
            }
        }
        throw new IllegalStateException("unreachable");
    }

    /**
     * A paid answer that hit the output ceiling is not a model outage: asking weaker fallback models for the same big
     * structured answer wastes money and gives a worse result (22 Nov 2026: three cut-off Sonnet shot plans, each paid).
     * It fails at once with a clear message; the owner retries (or the shot plan split, projectv.cost.shot-plan-split-tokens, avoids it).
     */
    static boolean isCutOff(Throwable t) {
        for (Throwable c = t; c != null; c = c.getCause()) {
            String msg = String.valueOf(c.getMessage()).toLowerCase(Locale.ROOT);
            // Only the paid Claude call: a free-tier model cut off (groq, nvidia) still moves on to the next one, as before.
            if (msg.contains("was cut off") && msg.contains("claude:")) {
                return true;
            }
        }
        return false;
    }

    static boolean isModelProblem(Throwable t) {
        if (isCutOff(t)) {
            return false;
        }
        for (Throwable c = t; c != null; c = c.getCause()) {
            String m = String.valueOf(c.getMessage()).toLowerCase(Locale.ROOT);
            if (m.contains("no longer available") || m.contains("not found") || m.contains("404")
                    || m.contains("not supported") || m.contains("503") || m.contains("unavailable")
                    || m.contains("overloaded") || m.contains("504") || m.contains("deadline")) {
                return true;
            }
        }
        return false;
    }
}
