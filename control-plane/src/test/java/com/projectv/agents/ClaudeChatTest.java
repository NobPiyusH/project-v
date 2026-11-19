package com.projectv.agents;

import com.projectv.ledger.Pricing;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.math.BigDecimal;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ClaudeChatTest {

    public record Haiku(String title, List<String> lines) {
    }

    @Test
    void sonnetCostIsPricedFromConfig() throws Exception {
        Pricing pricing = new Pricing("../config");
        // 4,000 in + 3,000 out on Sonnet 5.5 = $0.008 + $0.030 = $0.038 = ₹3.572 at 94/$
        assertEquals(0, new BigDecimal("3.5720").compareTo(pricing.claudeInr(4_000, 3_000)));
        assertEquals(0, new BigDecimal("752.0000").compareTo(pricing.claudeMonthlyCapInr()));
    }

    @Test
    void withoutAKeyClaudeIsSkippedAsAModelProblem() {
        ClaudeChat none = new ClaudeChat("");
        assertFalse(none.configured());
        var e = assertThrows(IllegalStateException.class, () -> none.call("claude:x", "s", "u", Haiku.class, (i, o) -> { }));
        assertTrue(ModelFallback.isModelProblem(e), "the fallback list moves on to Gemini");
    }

    @Test
    void aSlowPaidModelGetsItsOwnTimeLimit() throws Exception {
        var a = ModelFallback.run(List.of("claude:slow", "fast"), m -> m.startsWith("claude:") ? 3L : 1L, "test", m -> {
            if (m.startsWith("claude:")) {
                try {
                    Thread.sleep(1_500);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
            return m;
        });
        assertEquals("claude:slow", a.model(), "1.5 s is within Claude's 3 s, so its (paid) answer is used");
    }

    /** Live: one tiny call on the cheapest model (~₹0.05). Run with PROJECTV_LIVE_CLAUDE=1. */
    @Test
    @EnabledIfEnvironmentVariable(named = "PROJECTV_LIVE_CLAUDE", matches = "1")
    void liveStructuredCallOnHaiku() {
        ClaudeChat chat = new ClaudeChat(System.getenv("ANTHROPIC_API_KEY"));
        AtomicLong in = new AtomicLong();
        AtomicLong out = new AtomicLong();
        var r = chat.call("claude:claude-haiku-5-5", "You write tiny poems.",
                "A 3-line haiku about the night sky being dark.", Haiku.class, (i, o) -> {
                    in.set(i);
                    out.set(o);
                });
        assertNotNull(r.entity().title());
        assertEquals(3, r.entity().lines().size());
        assertTrue(in.get() > 0 && out.get() > 0, "usage reported for the ledger");
        System.out.printf("LIVE Claude OK: %s | %s | tokens %d in / %d out%n", r.entity().title(), r.entity().lines(), in.get(), out.get());
    }
}
