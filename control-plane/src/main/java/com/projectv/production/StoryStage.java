package com.projectv.production;

import com.projectv.agents.AgentTypes.Concept;
import com.projectv.agents.AgentTypes.Script;
import com.projectv.agents.AgentTypes.ScriptLine;
import com.projectv.agents.WriterAgent;
import com.projectv.planner.VideoPlan;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import com.projectv.production.ProductionService.*;

/** Concept and script (Writer agent). */
@Service
public class StoryStage extends StageSupport {

    public StoryStage(ProductionDeps d) {
        super(d);
    }

    @Transactional
    public StageResult concept(UUID videoId, String ownerNotes) {
        VideoPlan plan = plan(videoId);
        Concept c = writer.concept(videoId, prompt(videoId), plan, facts(videoId), ownerNotes);
        int version = nextVersion("concept", videoId);
        jdbc.sql("""
                        INSERT INTO concept (id, video_id, big_idea, clock_definition, acts_json, version)
                        VALUES (:id, :v, :idea, CAST(:clock AS jsonb), CAST(:acts AS jsonb), :ver)
                        """)
                .param("id", UUID.randomUUID()).param("v", videoId).param("idea", c.bigIdea())
                .param("clock", json.writeValueAsString(Map.of("label", c.clockLabel() == null ? "" : c.clockLabel(),
                        "logline", c.logline() == null ? "" : c.logline(),
                        "emotionalArc", c.emotionalArc() == null ? "" : c.emotionalArc())))
                .param("acts", json.writeValueAsString(c.acts() == null ? List.of() : c.acts()))
                .param("ver", version).update();
        return StageResult.of(c.logline() + " Big idea: " + c.bigIdea(), json.writeValueAsString(c));
    }

    @Transactional
    public StageResult script(UUID videoId, String ownerNotes) {
        VideoPlan plan = plan(videoId);
        Concept concept = latestConcept(videoId).orElseThrow(() -> new IllegalStateException("No concept yet"));
        Script s = writer.script(videoId, prompt(videoId), plan, concept, facts(videoId), ownerNotes);
        if (s.lines() == null || s.lines().isEmpty()) {
            throw new IllegalStateException("The writer returned an empty script");
        }
        List<StoredLine> lines = new ArrayList<>();
        int i = 0;
        for (ScriptLine l : s.lines()) {
            boolean spoken = l.spoken() && !"none".equals(plan.narration());
            lines.add(new StoredLine(i++, l.text(), l.act(), l.purpose(), spoken));
        }
        String voice = WriterAgent.VOICES.contains(s.voiceName()) ? s.voiceName() : defaultVoice;
        StoredScript stored = new StoredScript(nextVersion("script", videoId), lines, voice, s.voiceStyle(), s.musicBrief(), null, null);
        int words = lines.stream().filter(StoredLine::spoken).mapToInt(l -> l.text().split("\\s+").length).sum();
        StringBuilder md = new StringBuilder();
        for (StoredLine l : lines) {
            md.append(l.spoken() ? "" : "_[visual]_ ").append(l.text()).append("\n\n");
        }
        jdbc.sql("""
                        INSERT INTO script (id, video_id, version, narration_md, beat_sheet_json, word_count, est_duration_sec)
                        VALUES (:id, :v, :ver, :md, CAST(:beats AS jsonb), :words, :est)
                        """)
                .param("id", UUID.randomUUID()).param("v", videoId).param("ver", stored.version())
                .param("md", md.toString()).param("beats", json.writeValueAsString(stored)).param("words", words)
                .param("est", (int) Math.round(words / 2.3)).update();
        long spoken = lines.stream().filter(StoredLine::spoken).count();
        return StageResult.of("%d lines (%d spoken, %d words), voice %s.".formatted(lines.size(), spoken, words, voice),
                json.writeValueAsString(stored));
    }
}
