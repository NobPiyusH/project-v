package com.projectv.production;

import com.projectv.agents.AgentTypes.Concept;
import com.projectv.agents.DirectorAgent;
import com.projectv.agents.WriterAgent;
import com.projectv.assets.AssetStore;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.Fact;
import com.projectv.planner.StylePresets;
import com.projectv.planner.VideoPlan;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import com.projectv.production.ProductionService.*;

/** Base of every production stage: shared dependencies and read/write helpers (script row lock, plan, shots...). */
public abstract class StageSupport {
    protected final JdbcClient jdbc;
    protected final ObjectMapper json;
    protected final WriterAgent writer;
    protected final DirectorAgent director;
    protected final AssetStore assets;
    protected final CostLedger ledger;
    protected final Pricing pricing;
    protected final BudgetGuard budget;
    protected final StylePresets presets;
    protected final String textModel;
    protected final String ttsModel;
    protected final String imageModel;
    protected final String musicModel;
    protected final String defaultVoice;
    protected final String modalApp;
    protected final String modalClass;

    protected StageSupport(ProductionDeps d) {
        this.jdbc = d.jdbc;
        this.json = d.json;
        this.writer = d.writer;
        this.director = d.director;
        this.assets = d.assets;
        this.ledger = d.ledger;
        this.pricing = d.pricing;
        this.budget = d.budget;
        this.presets = d.presets;
        this.textModel = d.textModel;
        this.ttsModel = d.ttsModel;
        this.imageModel = d.imageModel;
        this.musicModel = d.musicModel;
        this.defaultVoice = d.defaultVoice;
        this.modalApp = d.modalApp;
        this.modalClass = d.modalClass;
    }

    public VideoPlan plan(UUID videoId) {
        String planJson = jdbc.sql("SELECT plan_json::text FROM video WHERE id = :v").param("v", videoId)
                .query(String.class).single();
        if (planJson == null) {
            throw new IllegalStateException("Video has no plan yet");
        }
        Map<String, Object> wrapper = json.readValue(planJson, new TypeReference<>() {
        });
        return json.convertValue(wrapper.get("plan"), VideoPlan.class);
    }

    protected String prompt(UUID videoId) {
        return jdbc.sql("SELECT prompt FROM video WHERE id = :v").param("v", videoId).query(String.class).single();
    }

    public List<Fact> facts(UUID videoId) {
        return jdbc.sql("SELECT facts_json::text FROM research WHERE video_id = :v ORDER BY version DESC LIMIT 1")
                .param("v", videoId).query(String.class).optional()
                .map(s -> json.readValue(s, new TypeReference<List<Fact>>() {
                })).orElse(List.of());
    }

    protected Optional<Concept> latestConcept(UUID videoId) {
        return jdbc.sql("""
                        SELECT big_idea, clock_definition::text AS clock, acts_json::text AS acts
                        FROM concept WHERE video_id = :v ORDER BY version DESC LIMIT 1
                        """)
                .param("v", videoId).query().listOfRows().stream().findFirst().map(r -> {
                    Map<String, String> clock = json.readValue((String) r.get("clock"), new TypeReference<>() {
                    });
                    List<com.projectv.agents.AgentTypes.Act> acts = json.readValue((String) r.get("acts"), new TypeReference<>() {
                    });
                    return new Concept(clock.get("logline"), (String) r.get("big_idea"), clock.get("label"), acts,
                            clock.get("emotionalArc"));
                });
    }

    public Optional<StoredScript> latestScript(UUID videoId) {
        return jdbc.sql("SELECT beat_sheet_json::text FROM script WHERE video_id = :v ORDER BY version DESC LIMIT 1")
                .param("v", videoId).query(String.class).optional()
                .map(s -> json.readValue(s, StoredScript.class));
    }

    protected List<Map<String, Object>> shots(UUID videoId) {
        return jdbc.sql("""
                        SELECT s.id, s.ord, s.duration_target_sec, s.shot_type, s.camera_move, s.description,
                               s.prompt_json::text AS prompt_json, s.priority, s.overlay_text, s.keyframe_asset_id,
                               s.status, s.error, s.draft_clip_asset_id, s.final_clip_asset_id, s.clip_status,
                               s.clip_error, s.final_wanted,
                               a.uri_hot AS keyframe_uri, p.provider AS keyframe_provider, sc.title AS scene_title
                        FROM shot s JOIN scene sc ON sc.id = s.scene_id
                        LEFT JOIN asset a ON a.id = s.keyframe_asset_id
                        LEFT JOIN provenance p ON p.id = a.provenance_id
                        WHERE sc.video_id = :v ORDER BY s.ord
                        """).param("v", videoId).query().listOfRows();
    }

    protected List<Double> beatTimes(UUID videoId) {
        return jdbc.sql("SELECT beat_times_json::text FROM music_cue WHERE video_id = :v").param("v", videoId)
                .query(String.class).optional()
                .map(s -> json.readValue(s, new TypeReference<List<Double>>() {
                })).orElse(List.of());
    }

    protected double bpm(UUID videoId) {
        return jdbc.sql("SELECT bpm FROM music_cue WHERE video_id = :v").param("v", videoId)
                .query(BigDecimal.class).optional().map(BigDecimal::doubleValue).orElse(0.0);
    }

    protected int nextVersion(String table, UUID videoId) {
        // Table names are fixed constants from this class, never user input.
        return jdbc.sql("SELECT COALESCE(max(version), 0) + 1 FROM " + table + " WHERE video_id = :v")
                .param("v", videoId).query(Integer.class).single();
    }

    protected int nextKeyframeAttempt(UUID videoId) {
        return jdbc.sql("SELECT count(*) + 1 FROM asset WHERE video_id = :v AND kind = 'image'")
                .param("v", videoId).query(Integer.class).single();
    }

    protected static double round(double v) {
        return Math.round(v * 1000.0) / 1000.0;
    }

    /** Reads the latest script with a row lock, so concurrent redos/edits can't overwrite each other. */
    protected StoredScript lockScript(UUID videoId) {
        return jdbc.sql("""
                        SELECT beat_sheet_json::text FROM script WHERE video_id = :v
                        AND version = (SELECT max(version) FROM script WHERE video_id = :v) FOR UPDATE
                        """)
                .param("v", videoId).query(String.class).optional()
                .map(s -> json.readValue(s, StoredScript.class))
                .orElseThrow(() -> new IllegalStateException("This video has no script yet"));
    }

    protected void writeScript(UUID videoId, StoredScript s) {
        StringBuilder md = new StringBuilder();
        for (StoredLine l : s.lines()) {
            md.append(l.spoken() ? "" : "_[visual]_ ").append(l.text()).append("\n\n");
        }
        int words = s.lines().stream().filter(StoredLine::spoken).mapToInt(l -> l.text().split("\\s+").length).sum();
        jdbc.sql("""
                        UPDATE script SET beat_sheet_json = CAST(:b AS jsonb), narration_md = :md, word_count = :w, updated_at = now()
                        WHERE video_id = :v AND version = :ver
                        """)
                .param("b", json.writeValueAsString(s)).param("md", md.toString()).param("w", words)
                .param("v", videoId).param("ver", s.version()).update();
    }

    protected static StoredScript withLines(StoredScript s, List<StoredLine> lines) {
        return new StoredScript(s.version(), lines, s.voiceName(), s.voiceStyle(), s.musicBrief(), s.lineStarts(), s.tempo());
    }
}
