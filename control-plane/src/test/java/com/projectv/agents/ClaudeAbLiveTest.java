package com.projectv.agents;

import com.projectv.agents.AgentTypes.Concept;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.ResearchRequest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * One-off A/B (17 Nov 2026): Claude research + concept on the deep-ocean demo's exact inputs, written to a file for a
 * blind comparison with what Gemini/NVIDIA produced. Run with PROJECTV_LIVE_AB=1 (~Rs 11 of Claude credit).
 */
class ClaudeAbLiveTest {

    static final String PROMPT = "A 60-second vertical YouTube Short / Instagram Reel: a descent into the deep ocean midnight zone, "
            + "4 km down. Bioluminescence, anglerfish, giant squid, crushing pressure, total darkness lit only by living "
            + "light. Awe and mystery, fast hook, true science, ending that loops back to the surface.";

    @Test
    @EnabledIfEnvironmentVariable(named = "PROJECTV_LIVE_AB", matches = "1")
    void claudeResearchAndConceptOnTheDemoInputs() throws Exception {
        BudgetGuard budget = mock(BudgetGuard.class);
        when(budget.canSpendClaude(any(BigDecimal.class))).thenReturn(true);
        Pricing pricing = new Pricing("../config");
        ClaudeChat chat = new ClaudeChat(System.getenv("ANTHROPIC_API_KEY"));
        var research = new ClaudeResearcher(chat, mock(CostLedger.class), budget, pricing, "claude:claude-sonnet-5-5")
                .research(UUID.randomUUID(), new ResearchRequest(PROMPT, "Midnight Descent: 4 km into the Abyss",
                        List.of(), "en", 8, null, null));
        StringBuilder facts = new StringBuilder();
        research.facts().forEach(f -> facts.append("- ").append(f.text()).append('\n'));
        // The concept prompt exactly as WriterAgent.concept builds it (preset shorts_punchy).
        String system = """
                You are the head writer of a premium documentary studio. Style preset: Shorts punchy (fast vertical short).
                Design the concept for this video: one big idea that makes the audience feel something, an optional
                on-screen "clock" (a counter such as years, distance or temperature that drives the narrative; leave
                clockLabel empty if it does not fit), and 3-6 acts whose approxSec add up to the target duration.
                Return only the JSON object.""";
        String user = """
                Owner's prompt: %s
                Plan: Midnight Descent: 4 km into the Abyss, 60 seconds, tone awe and mystery, language en, narration full.
                Must include:\s
                Research facts:
                %s""".formatted(PROMPT, facts);
        long[] usage = new long[2];
        var concept = chat.call("claude:claude-sonnet-5-5", system, user, Concept.class, (in, out) -> {
            usage[0] = in;
            usage[1] = out;
        });
        Path out = Path.of("build", "claude-ab.json");
        Files.writeString(out, JsonMapper.builder().build().writeValueAsString(Map.of(
                "facts", research.facts(), "concept", concept.entity(),
                "researchTokens", research.usage(), "conceptTokens", List.of(usage[0], usage[1]))));
        System.out.printf("AB written to %s: %d facts; concept tokens %d in / %d out%n", out.toAbsolutePath(),
                research.facts().size(), usage[0], usage[1]);
    }
}
