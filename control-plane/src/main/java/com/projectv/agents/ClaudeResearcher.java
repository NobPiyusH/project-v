package com.projectv.agents;

import com.anthropic.models.messages.ContentBlock;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.MessageCreateParams;
import com.anthropic.models.messages.OutputConfig;
import com.anthropic.models.messages.StopReason;
import com.anthropic.models.messages.TextCitation;
import com.anthropic.models.messages.WebSearchResultBlock;
import com.anthropic.models.messages.WebSearchTool20250305;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.Fact;
import com.projectv.media.MediaTypes.ResearchRequest;
import com.projectv.media.MediaTypes.ResearchResult;
import com.projectv.media.MediaTypes.Source;
import com.projectv.media.MediaTypes.Usage;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Backup researcher (added 17 Nov 2026 after Gemini's Google Search grounding stalled a video for 30+ minutes): Claude
 * with Anthropic's server-side web search. Same output as the Gemini researcher: one fact per line, each tied to the
 * web pages Claude cited for it. Used only when the Gemini research activity has failed; its cost (tokens and
 * searches) is recorded the moment the answer arrives and counts against the owner's Claude cap.
 */
@Component
public class ClaudeResearcher {

    static final long MAX_SEARCHES = 5;
    /** Upper bound used to check the Claude cap before calling: ~40k tokens of search results + ~2k out + 5 searches. */
    static final BigDecimal ESTIMATE_INR = BigDecimal.valueOf(15);
    /** A fact line: "- ", "* ", "• " or "1. " bullets (models vary). */
    private static final Pattern FACT = Pattern.compile("(?m)^\\s*(?:[-*•]|\\d+[.)])\\s+(.+)$");

    private final ClaudeChat claude;
    private final CostLedger ledger;
    private final BudgetGuard budget;
    private final Pricing pricing;
    private final String model;

    public ClaudeResearcher(ClaudeChat claude, CostLedger ledger, BudgetGuard budget, Pricing pricing,
                            @Value("${projectv.models.story:claude:claude-sonnet-5-5}") String model) {
        this.claude = claude;
        this.ledger = ledger;
        this.budget = budget;
        this.pricing = pricing;
        this.model = model;
    }

    public ResearchResult research(UUID videoId, ResearchRequest req) {
        if (!claude.configured() || !ClaudeChat.handles(model)) {
            throw new IllegalStateException("unavailable: no Claude backup researcher configured");
        }
        if (!budget.canSpendClaude(ESTIMATE_INR)) {
            throw new IllegalStateException("unavailable: Claude monthly cap reached");
        }
        String must = req.mustInclude() == null || req.mustInclude().isEmpty() ? "none" : String.join(", ", req.mustInclude());
        String prompt = """
                Research this topic for a documentary video script. Use web search.
                Topic: %s
                Video summary: %s
                Must include: %s
                %s
                Write %d concise, verifiable facts, one per line, each starting with "- ".
                Prefer surprising, visual, emotionally powerful facts with concrete numbers, scales and time spans.
                Only state facts that your search results support. No introduction, no conclusion."""
                .formatted(req.topic(), req.summary() == null ? "" : req.summary(), must,
                        req.ownerNotes() == null || req.ownerNotes().isBlank() ? "" : "Owner notes: " + req.ownerNotes(),
                        req.maxFacts());
        MessageCreateParams.Builder params = MessageCreateParams.builder()
                .model(model.substring(ClaudeChat.PREFIX.length()))
                .maxTokens(ClaudeChat.MAX_TOKENS)
                .outputConfig(OutputConfig.builder().effort(OutputConfig.Effort.MEDIUM).build())
                .system("You are a careful science researcher for a premium documentary studio. Accuracy over drama.")
                .addTool(WebSearchTool20250305.builder().maxUses(MAX_SEARCHES).build())
                .addUserMessage(prompt);
        List<Message> turns = new ArrayList<>();
        Message response = claude.send(params.build());
        turns.add(response);
        record(videoId, response);
        // A long server-side search turn can pause; continue it once with the partial answer.
        if (response.stopReason().map(StopReason.PAUSE_TURN::equals).orElse(false)) {
            response = claude.send(params.addMessage(response).build());
            turns.add(response);
            record(videoId, response);
        }
        if (Boolean.getBoolean("projectv.debugResearch")) {
            turns.forEach(t -> t.content().forEach(b -> System.out.println("BLOCK " + (b.text().isPresent()
                    ? "text cites=" + b.text().get().citations().map(List::size).orElse(0) + " | "
                    + b.text().get().text().replace("\n", "\\n") : b.toString().substring(0, Math.min(120, b.toString().length()))))));
        }
        return parse(turns, model);
    }

    private void record(UUID videoId, Message m) {
        long in = m.usage().inputTokens();
        long out = m.usage().outputTokens();
        long searches = m.usage().serverToolUse().map(s -> s.webSearchRequests()).orElse(0L);
        ledger.record(videoId, BudgetGuard.CLAUDE, pricing.claudeInr(in, out).add(pricing.claudeSearchInr(searches)),
                BigDecimal.valueOf(in + out), "tokens", false, "RESEARCH backup (%s, %d web searches)".formatted(model, searches));
    }

    /** Facts from the "- " lines; each fact's sources are the pages cited by the text blocks it overlaps. */
    static ResearchResult parse(List<Message> turns, String model) {
        StringBuilder text = new StringBuilder();
        List<int[]> spans = new ArrayList<>();
        List<List<Source>> spanSources = new ArrayList<>();
        Map<String, Source> all = new LinkedHashMap<>();
        long in = 0, out = 0;
        for (Message m : turns) {
            in += m.usage().inputTokens();
            out += m.usage().outputTokens();
            for (ContentBlock b : m.content()) {
                b.webSearchToolResult().flatMap(r -> r.content().resultBlocks()).ifPresent(results -> {
                    for (WebSearchResultBlock r : results) {
                        all.putIfAbsent(r.url(), new Source(r.title(), r.url()));
                    }
                });
                b.text().ifPresent(t -> {
                    int start = text.length();
                    text.append(t.text());
                    List<Source> cited = new ArrayList<>();
                    for (TextCitation c : t.citations().orElse(List.of())) {
                        c.webSearchResultLocation().ifPresent(w -> cited.add(new Source(w.title().orElse(w.url()), w.url())));
                    }
                    spans.add(new int[]{start, text.length()});
                    spanSources.add(cited);
                });
            }
        }
        List<Fact> facts = new ArrayList<>();
        Set<String> citedUrls = new LinkedHashSet<>();
        Matcher m = FACT.matcher(text);
        while (m.find()) {
            Map<String, Source> sources = new LinkedHashMap<>();
            for (int i = 0; i < spans.size(); i++) {
                if (spans.get(i)[0] < m.end(1) && spans.get(i)[1] > m.start(1)) {
                    spanSources.get(i).forEach(s -> sources.putIfAbsent(s.uri(), s));
                }
            }
            citedUrls.addAll(sources.keySet());
            facts.add(new Fact(m.group(1).strip(), List.copyOf(sources.values())));
        }
        if (facts.isEmpty()) {
            throw new IllegalStateException("unavailable: " + model + " returned no facts");
        }
        // Cited pages first, then the rest of what the searches found.
        List<Source> sources = new ArrayList<>();
        citedUrls.forEach(u -> sources.add(all.getOrDefault(u, new Source(u, u))));
        all.values().stream().filter(s -> !citedUrls.contains(s.uri())).forEach(sources::add);
        return new ResearchResult(facts, sources, List.of(), model, new Usage(in, out, 0, in + out));
    }
}
