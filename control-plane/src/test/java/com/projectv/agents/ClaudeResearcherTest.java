package com.projectv.agents;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.ResearchRequest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ClaudeResearcherTest {

    /** Live: one research call with up to 5 web searches (~₹5-12). Run with PROJECTV_LIVE_CLAUDE=1. */
    @Test
    @EnabledIfEnvironmentVariable(named = "PROJECTV_LIVE_CLAUDE", matches = "1")
    void liveResearchReturnsCitedFacts() throws Exception {
        BudgetGuard budget = mock(BudgetGuard.class);
        when(budget.canSpendClaude(any(BigDecimal.class))).thenReturn(true);
        Pricing pricing = new Pricing("../config");
        var researcher = new ClaudeResearcher(new ClaudeChat(System.getenv("ANTHROPIC_API_KEY")), mock(CostLedger.class),
                budget, pricing, "claude:claude-sonnet-5-5");
        var r = researcher.research(UUID.randomUUID(), new ResearchRequest("The deep ocean midnight zone, 4 km down",
                "A 60 s Short: bioluminescence, anglerfish, giant squid, crushing pressure", List.of(), "en", 8, null, null));
        long cited = r.facts().stream().filter(f -> !f.sources().isEmpty()).count();
        r.facts().forEach(f -> System.out.println("- " + f.text() + "  [" + f.sources().size() + " sources]"));
        System.out.printf("LIVE research: %d facts, %d cited, %d sources, tokens %d in / %d out, ≈₹%s%n", r.facts().size(),
                cited, r.sources().size(), r.usage().promptTokens(), r.usage().outputTokens(),
                pricing.claudeInr(r.usage().promptTokens(), r.usage().outputTokens()).setScale(2, java.math.RoundingMode.HALF_UP));
        assertTrue(r.facts().size() >= 5, "facts returned");
        assertTrue(cited >= r.facts().size() / 2, "most facts carry citations");
    }
}
