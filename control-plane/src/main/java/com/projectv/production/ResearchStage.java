package com.projectv.production;

import com.projectv.ledger.BudgetGuard;
import com.projectv.media.MediaTypes.ResearchRequest;
import com.projectv.media.MediaTypes.ResearchResult;
import com.projectv.planner.VideoPlan;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.Map;
import java.util.UUID;

/** Research: Gemini + Google Search request, and storing sourced facts. */
@Service
public class ResearchStage extends StageSupport {

    public ResearchStage(ProductionDeps d) {
        super(d);
    }

    public ResearchRequest researchRequest(UUID videoId, String ownerNotes) {
        VideoPlan plan = plan(videoId);
        budget.require(BigDecimal.valueOf(3), "RESEARCH");
        return new ResearchRequest(prompt(videoId), plan.summary(), plan.mustInclude(), plan.language(),
                Math.max(8, Math.min(20, plan.durationSec() / 15)), textModel, ownerNotes);
    }

    @Transactional
    public StageResult saveResearch(UUID videoId, ResearchResult r) {
        int version = nextVersion("research", videoId);
        jdbc.sql("""
                        INSERT INTO research (id, video_id, version, facts_json, sources_json, queries, model)
                        VALUES (:id, :v, :ver, CAST(:facts AS jsonb), CAST(:sources AS jsonb), :queries, :model)
                        """)
                .param("id", UUID.randomUUID()).param("v", videoId).param("ver", version)
                .param("facts", json.writeValueAsString(r.facts())).param("sources", json.writeValueAsString(r.sources()))
                .param("queries", r.queries() == null ? new String[0] : r.queries().toArray(String[]::new))
                .param("model", r.model()).update();
        int queries = r.queries() == null ? 0 : r.queries().size();
        // The Claude backup researcher records its own cost (tokens + searches) as soon as it answers.
        if (!com.projectv.agents.ClaudeChat.handles(r.model())) {
            ledger.record(videoId, BudgetGuard.GEMINI, pricing.textInr(r.usage()).add(pricing.groundingInr(queries)),
                    BigDecimal.valueOf(r.usage() == null ? 0 : r.usage().totalTokens()), "tokens", true,
                    "RESEARCH (" + r.model() + ", " + queries + " searches)");
        }
        long sourced = r.facts().stream().filter(f -> f.sources() != null && !f.sources().isEmpty()).count();
        long confirmed = r.facts().stream().filter(f -> "confirmed".equals(f.check())).count();
        long unconfirmed = r.facts().stream().filter(f -> "unconfirmed".equals(f.check())).count();
        String checked = confirmed + unconfirmed == 0 ? "" : " Source check: %d confirmed on their page, %d unconfirmed (writer told to avoid those numbers)."
                .formatted(confirmed, unconfirmed);
        String summary = "%d facts (%d with sources) from %d web sources%s.".formatted(r.facts().size(), sourced,
                r.sources() == null ? 0 : r.sources().size(),
                com.projectv.agents.ClaudeChat.handles(r.model()) ? " (Claude researcher)" : "") + checked;
        return StageResult.of(summary, json.writeValueAsString(Map.of("facts", r.facts(), "queries", r.queries())));
    }
}
