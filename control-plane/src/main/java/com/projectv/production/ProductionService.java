package com.projectv.production;

import com.projectv.agents.AgentTypes.Concept;
import com.projectv.agents.AgentTypes.PlannedShot;
import com.projectv.agents.AgentTypes.Script;
import com.projectv.agents.AgentTypes.ScriptLine;
import com.projectv.agents.AgentTypes.ShotPlan;
import com.projectv.agents.DirectorAgent;
import com.projectv.agents.DirectorAgent.TimedLine;
import com.projectv.agents.WriterAgent;
import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.AnimaticLine;
import com.projectv.media.MediaTypes.AnimaticRequest;
import com.projectv.media.MediaTypes.AnimaticResult;
import com.projectv.media.MediaTypes.AnimaticShot;
import com.projectv.media.MediaTypes.Fact;
import com.projectv.media.MediaTypes.KeyframeJob;
import com.projectv.media.MediaTypes.KeyframeOutput;
import com.projectv.media.MediaTypes.KeyframeRequest;
import com.projectv.media.MediaTypes.KeyframeResult;
import com.projectv.media.MediaTypes.MusicRequest;
import com.projectv.media.MediaTypes.MusicResult;
import com.projectv.media.MediaTypes.NarrationClip;
import com.projectv.media.MediaTypes.NarrationLine;
import com.projectv.media.MediaTypes.NarrationRequest;
import com.projectv.media.MediaTypes.NarrationResult;
import com.projectv.media.MediaTypes.ResearchRequest;
import com.projectv.media.MediaTypes.ResearchResult;
import com.projectv.media.MediaTypes.Usage;
import com.projectv.planner.StylePresets;
import com.projectv.planner.VideoPlan;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * The M2a production stages: what each one reads, asks for, and stores. Activities call into this; it holds no
 * Temporal code. Every LLM/media spend goes through the ledger and the budget guard; every asset gets provenance.
 */
@Service
public class ProductionService {

    /** One stored script line, enriched with its narration audio once generated. */
    public record StoredLine(int index, String text, String act, String purpose, boolean spoken,
                             UUID audioAssetId, String audioUri, Double durationSec) {
    }

    public record StoredScript(int version, List<StoredLine> lines, String voiceName, String voiceStyle, String musicBrief) {
    }

    private final JdbcClient jdbc;
    private final ObjectMapper json;
    private final WriterAgent writer;
    private final DirectorAgent director;
    private final AssetStore assets;
    private final CostLedger ledger;
    private final Pricing pricing;
    private final BudgetGuard budget;
    private final StylePresets presets;
    private final String textModel;
    private final String ttsModel;
    private final String imageModel;
    private final String musicModel;
    private final String defaultVoice;
    private final String modalApp;
    private final String modalClass;

    public ProductionService(JdbcClient jdbc, ObjectMapper json, WriterAgent writer, DirectorAgent director,
                             AssetStore assets, CostLedger ledger, Pricing pricing, BudgetGuard budget,
                             StylePresets presets,
                             @Value("${projectv.models.text}") String textModel,
                             @Value("${projectv.models.tts}") String ttsModel,
                             @Value("${projectv.models.image}") String imageModel,
                             @Value("${projectv.models.music}") String musicModel,
                             @Value("${projectv.models.tts-voice}") String defaultVoice,
                             @Value("${projectv.modal.zimage-app}") String modalApp,
                             @Value("${projectv.modal.zimage-class}") String modalClass) {
        this.jdbc = jdbc;
        this.json = json;
        this.writer = writer;
        this.director = director;
        this.assets = assets;
        this.ledger = ledger;
        this.pricing = pricing;
        this.budget = budget;
        this.presets = presets;
        this.textModel = textModel;
        this.ttsModel = ttsModel;
        this.imageModel = imageModel;
        this.musicModel = musicModel;
        this.defaultVoice = defaultVoice;
        this.modalApp = modalApp;
        this.modalClass = modalClass;
    }

    // ================================================================================================ RESEARCH

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
        ledger.record(videoId, BudgetGuard.GEMINI, pricing.textInr(r.usage()).add(pricing.groundingInr(queries)),
                BigDecimal.valueOf(r.usage() == null ? 0 : r.usage().totalTokens()), "tokens", true,
                "RESEARCH (" + r.model() + ", " + queries + " searches)");
        long sourced = r.facts().stream().filter(f -> f.sources() != null && !f.sources().isEmpty()).count();
        String summary = "%d facts (%d with sources) from %d web sources.".formatted(r.facts().size(), sourced,
                r.sources() == null ? 0 : r.sources().size());
        return StageResult.of(summary, json.writeValueAsString(Map.of("facts", r.facts(), "queries", r.queries())));
    }

    // ================================================================================================ CONCEPT + SCRIPT

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
            lines.add(new StoredLine(i++, l.text(), l.act(), l.purpose(), spoken, null, null, null));
        }
        String voice = WriterAgent.VOICES.contains(s.voiceName()) ? s.voiceName() : defaultVoice;
        StoredScript stored = new StoredScript(nextVersion("script", videoId), lines, voice, s.voiceStyle(), s.musicBrief());
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

    // ================================================================================================ MUSIC

    public MusicRequest musicRequest(UUID videoId, String ownerNotes) {
        VideoPlan plan = plan(videoId);
        StoredScript script = latestScript(videoId).orElse(null);
        double target = plan.durationSec();
        int maxSegments = (int) Math.min(6, Math.ceil(target / 55.0) + 1);
        budget.require(pricing.musicInr(maxSegments), "MUSIC (" + maxSegments + " Lyria generations at most)");
        String brief = script != null && script.musicBrief() != null ? script.musicBrief() : "Cinematic score matching the tone: " + plan.tone();
        String prompt = """
                Instrumental film score, no vocals, no lyrics. About %d seconds long.
                Mood and style: %s
                Video: %s (%s). It must leave room for a narrator's voice: avoid busy melodies in the mid range.
                %s""".formatted(Math.round(target), brief, plan.title(), plan.tone(),
                ownerNotes == null || ownerNotes.isBlank() ? "" : "Owner's notes: " + ownerNotes);
        return new MusicRequest(videoId.toString(), musicModel, prompt, target, maxSegments);
    }

    @Transactional
    public StageResult saveMusic(UUID videoId, MusicResult r) {
        int songs = r.usage() == null ? r.segments() : r.usage().size();
        ledger.record(videoId, BudgetGuard.GEMINI, pricing.musicInr(songs), BigDecimal.valueOf(songs), "song", true,
                "MUSIC (" + r.model() + ")");
        UUID assetId = assets.create(new NewAsset(videoId, "audio", r.uri(), "audio/wav", null, null, r.durationSec(),
                "gemini_api", r.model(), null, null, AssetStore.LICENCE_GEMINI, true));
        jdbc.sql("DELETE FROM music_cue WHERE video_id = :v").param("v", videoId).update();
        jdbc.sql("""
                        INSERT INTO music_cue (id, video_id, asset_id, bpm, beat_times_json, sections_json)
                        VALUES (:id, :v, :a, :bpm, CAST(:beats AS jsonb), CAST(:sections AS jsonb))
                        """)
                .param("id", UUID.randomUUID()).param("v", videoId).param("a", assetId).param("bpm", r.bpm())
                .param("beats", json.writeValueAsString(r.beatTimes()))
                .param("sections", json.writeValueAsString(Map.of("downbeats", r.downbeatTimes())))
                .update();
        return StageResult.of("Score %.0fs at %.0f BPM, %d beats (%d Lyria generation%s).".formatted(r.durationSec(),
                        r.bpm(), r.beatTimes().size(), songs, songs == 1 ? "" : "s"),
                json.writeValueAsString(Map.of("assetId", assetId, "bpm", r.bpm(), "durationSec", r.durationSec())));
    }

    // ================================================================================================ NARRATION + SHOT PLAN

    public NarrationRequest narrationRequest(UUID videoId) {
        VideoPlan plan = plan(videoId);
        StoredScript s = latestScript(videoId).orElseThrow(() -> new IllegalStateException("No script yet"));
        List<NarrationLine> lines = s.lines().stream().filter(StoredLine::spoken)
                .map(l -> new NarrationLine(l.index(), l.text())).toList();
        // ~25 audio tokens per second of speech at ~2.3 words per second.
        long words = lines.stream().mapToLong(l -> l.text().split("\\s+").length).sum();
        budget.require(pricing.ttsInr(new Usage(words * 2, Math.round(words / 2.3 * 25 * 1.5), 0, 0)), "NARRATION");
        // Short "in a <style> voice" phrase: long instructions get read aloud by the TTS model (owner feedback).
        String raw = s.voiceStyle() == null ? "" : s.voiceStyle().strip().replaceAll("[.\\s]+$", "");
        String[] styleWords = raw.split("\\s+");
        String shortStyle = String.join(" ", java.util.Arrays.copyOf(styleWords, Math.min(styleWords.length, 8))).toLowerCase(java.util.Locale.ROOT);
        String style = shortStyle.isBlank() ? "in a calm documentary narrator's voice" : "in a " + shortStyle + " voice";
        return new NarrationRequest(videoId.toString(), ttsModel, textModel, s.voiceName(), style, plan.language(), lines);
    }

    @Transactional
    public StageResult saveNarration(UUID videoId, NarrationResult r) {
        StoredScript s = latestScript(videoId).orElseThrow();
        Map<Integer, NarrationClip> byIndex = new LinkedHashMap<>();
        Usage total = Usage.NONE;
        for (NarrationClip c : r.lines()) {
            byIndex.put(c.index(), c);
            if (c.usage() != null) {
                total = new Usage(total.promptTokens() + c.usage().promptTokens(), total.outputTokens() + c.usage().outputTokens(),
                        total.thoughtTokens() + c.usage().thoughtTokens(), total.totalTokens() + c.usage().totalTokens());
            }
        }
        List<StoredLine> lines = new ArrayList<>();
        for (StoredLine l : s.lines()) {
            NarrationClip c = byIndex.get(l.index());
            if (c == null) {
                lines.add(l);
                continue;
            }
            UUID assetId = assets.create(new NewAsset(videoId, "audio", c.uri(), "audio/wav", null, null, c.durationSec(),
                    "gemini_api", r.model(), "[" + r.voiceName() + "] " + l.text(), null, AssetStore.LICENCE_GEMINI, true));
            lines.add(new StoredLine(l.index(), l.text(), l.act(), l.purpose(), true, assetId, c.uri(), c.durationSec()));
        }
        ledger.record(videoId, BudgetGuard.GEMINI, pricing.ttsInr(total), BigDecimal.valueOf(total.totalTokens()), "tokens",
                true, "NARRATION (" + r.model() + ", " + r.lines().size() + " lines)");
        StoredScript updated = new StoredScript(s.version(), lines, s.voiceName(), s.voiceStyle(), s.musicBrief());
        jdbc.sql("UPDATE script SET beat_sheet_json = CAST(:b AS jsonb), updated_at = now() WHERE video_id = :v AND version = :ver")
                .param("b", json.writeValueAsString(updated)).param("v", videoId).param("ver", s.version()).update();

        int retakes = r.lines().stream().mapToInt(c -> Math.max(0, c.takes() - 1)).sum();
        List<String> failed = r.lines().stream().filter(c -> !c.passed()).map(c -> "#" + c.index() + " (" + c.check() + ")").toList();
        double seconds = r.lines().stream().mapToDouble(NarrationClip::durationSec).sum();
        String summary = "%d lines voiced by %s, %.0fs of speech. Every line checked by transcription: %d re-voiced automatically%s."
                .formatted(r.lines().size(), r.voiceName(), seconds, retakes,
                        failed.isEmpty() ? ", all pass" : "; still imperfect after 3 takes: " + String.join(", ", failed));
        return StageResult.of(summary, json.writeValueAsString(Map.of("lines", r.lines().size(), "retakes", retakes, "failed", failed)));
    }

    @Transactional
    public StageResult shotPlan(UUID videoId, String ownerNotes) {
        VideoPlan plan = plan(videoId);
        Concept concept = latestConcept(videoId).orElseThrow(() -> new IllegalStateException("No concept yet"));
        StoredScript script = latestScript(videoId).orElseThrow(() -> new IllegalStateException("No script yet"));
        List<Double> beats = beatTimes(videoId);
        double bpm = bpm(videoId);
        double total = plan.durationSec();
        List<TimedLine> timed = script.lines().stream()
                .map(l -> new TimedLine(l.index(), l.text(), l.spoken(), l.durationSec() != null ? l.durationSec() : 6.0))
                .toList();
        ShotPlan sp = director.plan(videoId, plan, concept, timed, bpm, total, ownerNotes);
        if (sp.shots() == null || sp.shots().isEmpty()) {
            throw new IllegalStateException("The director returned no shots");
        }
        boolean isShort = "short".equals(plan.format());
        List<double[]> times = ShotTiming.fit(sp.shots().stream().map(PlannedShot::durationSec).toList(), total, beats,
                isShort ? 1.2 : 2.5);

        // Hero shots get the premium (paid) image model; models over-assign them, so enforce ≤20% in code.
        // The opening and the final shot keep hero status first, then the director's picks in order.
        int heroCap = Math.max(1, (int) Math.round(sp.shots().size() * 0.20));
        java.util.Set<Integer> heroIdx = new java.util.LinkedHashSet<>();
        int last = sp.shots().size() - 1;
        for (int i : new int[]{0, last}) {
            if ("hero".equals(sp.shots().get(i).priority()) && heroIdx.size() < heroCap) {
                heroIdx.add(i);
            }
        }
        for (int i = 0; i < sp.shots().size() && heroIdx.size() < heroCap; i++) {
            if ("hero".equals(sp.shots().get(i).priority())) {
                heroIdx.add(i);
            }
        }

        jdbc.sql("DELETE FROM scene WHERE video_id = :v").param("v", videoId).update();
        UUID sceneId = null;
        String sceneTitle = null;
        int sceneOrd = 0;
        int heroes = 0;
        for (int i = 0; i < sp.shots().size(); i++) {
            PlannedShot s = sp.shots().get(i);
            String title = s.sceneTitle() == null || s.sceneTitle().isBlank() ? "Scene" : s.sceneTitle();
            if (sceneId == null || !title.equals(sceneTitle)) {
                sceneId = UUID.randomUUID();
                sceneTitle = title;
                jdbc.sql("INSERT INTO scene (id, video_id, ord, title) VALUES (:id, :v, :o, :t)")
                        .param("id", sceneId).param("v", videoId).param("o", sceneOrd++).param("t", title).update();
            }
            String priority = heroIdx.contains(i) ? "hero" : "standard";
            if (priority.equals("hero")) {
                heroes++;
            }
            Map<String, Object> prompt = new LinkedHashMap<>();
            prompt.put("keyframePrompt", s.keyframePrompt());
            prompt.put("motionPrompt", s.motionPrompt());
            prompt.put("lineIndex", s.lineIndex());
            prompt.put("startSec", round(times.get(i)[0]));
            jdbc.sql("""
                            INSERT INTO shot (id, scene_id, ord, duration_target_sec, shot_type, camera_move, description,
                                              prompt_json, priority, overlay_text)
                            VALUES (:id, :scene, :ord, :dur, :type, :move, :desc, CAST(:prompt AS jsonb), :prio, :overlay)
                            """)
                    .param("id", UUID.randomUUID()).param("scene", sceneId).param("ord", i)
                    .param("dur", round(times.get(i)[1])).param("type", s.shotType()).param("move", s.cameraMove())
                    .param("desc", s.description()).param("prompt", json.writeValueAsString(prompt))
                    .param("prio", priority).param("overlay", s.overlayText() == null || s.overlayText().isBlank() ? null : s.overlayText())
                    .update();
        }
        return StageResult.of("%d shots in %d scenes over %.0fs (%d hero), cuts snapped to the %.0f BPM beat grid."
                .formatted(sp.shots().size(), sceneOrd, total, heroes, bpm), json.writeValueAsString(sp));
    }

    // ================================================================================================ KEYFRAMES

    public KeyframeRequest keyframeRequest(UUID videoId, String ownerNotes) {
        VideoPlan plan = plan(videoId);
        StylePresets.Preset preset = presets.byIdOrDefault(plan.stylePreset());
        boolean vertical = "9:16".equals(plan.aspectRatio());
        int width = vertical ? 768 : 1344;
        int height = vertical ? 1344 : 768;
        BigDecimal perImage = pricing.imageInr("1K");
        BigDecimal available = budget.geminiRemaining().subtract(pricing.geminiReserveInr());
        List<KeyframeJob> jobs = new ArrayList<>();
        int attempt = nextKeyframeAttempt(videoId);
        for (Map<String, Object> shot : shots(videoId)) {
            Map<String, Object> p = json.readValue((String) shot.get("prompt_json"), new TypeReference<>() {
            });
            String prompt = p.get("keyframePrompt") + ". Visual style: " + preset.visualStyle()
                    + (ownerNotes == null || ownerNotes.isBlank() ? "" : ". Director's notes: " + ownerNotes);
            boolean hero = "hero".equals(shot.get("priority"));
            String provider = "modal_zimage";
            if (hero && available.compareTo(perImage) >= 0) {
                provider = "gemini_api";
                available = available.subtract(perImage);
            }
            String shotId = shot.get("id").toString();
            jobs.add(new KeyframeJob(shotId, attempt, provider, prompt, width, height, plan.aspectRatio(), "1K",
                    Math.abs((long) shotId.hashCode()) + attempt));
        }
        if (jobs.isEmpty()) {
            throw new IllegalStateException("No shots to render; run the shot plan first");
        }
        return new KeyframeRequest(videoId.toString(), imageModel, modalApp, modalClass, jobs);
    }

    @Transactional
    public StageResult saveKeyframes(UUID videoId, KeyframeRequest request, KeyframeResult r) {
        Map<String, KeyframeJob> jobs = new LinkedHashMap<>();
        request.jobs().forEach(j -> jobs.put(j.shotId(), j));
        int ok = 0;
        int gem = 0;
        List<String> errors = new ArrayList<>();
        for (KeyframeOutput o : r.results()) {
            if (o.uri() == null) {
                errors.add(o.error());
                continue;
            }
            KeyframeJob job = jobs.get(o.shotId());
            boolean isGemini = "gemini_api".equals(o.provider());
            UUID assetId = assets.create(new NewAsset(videoId, "image", o.uri(), o.uri().endsWith(".png") ? "image/png" : "image/jpeg",
                    job.width(), job.height(), null, o.provider(), o.model(), job.prompt(), o.seed(),
                    isGemini ? AssetStore.LICENCE_GEMINI : AssetStore.LICENCE_ZIMAGE, true));
            jdbc.sql("UPDATE shot SET keyframe_asset_id = :a, updated_at = now() WHERE id = :s")
                    .param("a", assetId).param("s", UUID.fromString(o.shotId())).update();
            if (isGemini) {
                gem++;
                ledger.record(videoId, BudgetGuard.GEMINI, pricing.imageInr("1K"), BigDecimal.ONE, "image", true,
                        "KEYFRAME hero (" + o.model() + ")");
            }
            ok++;
        }
        if (r.modalGpuSeconds() > 0) {
            ledger.record(videoId, "modal_l40s", pricing.modalGpuInr("L40S", r.modalGpuSeconds()),
                    BigDecimal.valueOf(r.modalGpuSeconds()), "gpu_seconds", true,
                    "KEYFRAMES Z-Image (free Modal credit; excludes container start-up)");
        }
        if (ok == 0) {
            throw new IllegalStateException("No keyframes were produced: " + String.join(" | ", errors));
        }
        String summary = "%d/%d keyframes (%d hero via Nano Banana, %d via Z-Image on Modal)%s.".formatted(ok, jobs.size(), gem,
                ok - gem, errors.isEmpty() ? "" : "; " + errors.size() + " failed: " + errors.getFirst());
        return StageResult.of(summary, json.writeValueAsString(Map.of("ok", ok, "failed", errors)));
    }

    // ================================================================================================ ANIMATIC

    public AnimaticRequest animaticRequest(UUID videoId) {
        VideoPlan plan = plan(videoId);
        List<Map<String, Object>> shotRows = shots(videoId);
        List<AnimaticShot> shots = new ArrayList<>();
        Map<Integer, Double> lineStart = new LinkedHashMap<>();
        String lastImage = null;
        double end = 0;
        for (int i = 0; i < shotRows.size(); i++) {
            Map<String, Object> s = shotRows.get(i);
            Map<String, Object> p = json.readValue((String) s.get("prompt_json"), new TypeReference<>() {
            });
            double start = ((Number) p.get("startSec")).doubleValue();
            double dur = ((Number) s.get("duration_target_sec")).doubleValue();
            String image = s.get("keyframe_uri") == null ? lastImage : (String) s.get("keyframe_uri");
            if (image == null) {
                continue; // no keyframe at all yet for the opening shots
            }
            lastImage = image;
            shots.add(new AnimaticShot(i + 1, start, dur, image, (String) s.get("overlay_text")));
            if (p.get("lineIndex") instanceof Number n) {
                lineStart.putIfAbsent(n.intValue(), start);
            }
            end = Math.max(end, start + dur);
        }
        if (shots.isEmpty()) {
            throw new IllegalStateException("No keyframes to build an animatic from");
        }
        List<AnimaticLine> narration = new ArrayList<>();
        double prevEnd = 0;
        for (StoredLine l : latestScript(videoId).map(StoredScript::lines).orElse(List.of())) {
            if (!l.spoken() || l.audioUri() == null) {
                continue;
            }
            double start = Math.max(lineStart.getOrDefault(l.index(), prevEnd + 0.4), prevEnd + 0.25);
            narration.add(new AnimaticLine(l.audioUri(), round(start)));
            prevEnd = start + (l.durationSec() == null ? 0 : l.durationSec());
        }
        String musicUri = jdbc.sql("""
                        SELECT a.uri_hot FROM music_cue m JOIN asset a ON a.id = m.asset_id WHERE m.video_id = :v
                        """).param("v", videoId).query(String.class).optional().orElse(null);
        return new AnimaticRequest(videoId.toString(), plan.title(), shots, narration, musicUri, Math.max(end, prevEnd));
    }

    @Transactional
    public StageResult saveAnimatic(UUID videoId, AnimaticResult r) {
        UUID assetId = assets.create(new NewAsset(videoId, "video", r.uri(), "video/mp4", 1920, 1080, r.durationSec(),
                "projectv", "ffmpeg animatic", null, null, AssetStore.LICENCE_OWN_RENDER, false));
        jdbc.sql("UPDATE video SET animatic_asset_id = :a, updated_at = now() WHERE id = :v")
                .param("a", assetId).param("v", videoId).update();
        return StageResult.of("Animatic ready: %.0fs, 1080p, keyframes + narration + music. Story and pacing check before any video spend."
                .formatted(r.durationSec()), json.writeValueAsString(Map.of("assetId", assetId, "durationSec", r.durationSec())));
    }

    // ================================================================================================ READ MODELS

    /** Everything the dashboard shows for a video's production. */
    public Map<String, Object> production(UUID videoId) {
        Map<String, Object> out = new LinkedHashMap<>();
        // Postgres text[] must be converted to JSON in SQL: the JDBC array object itself isn't serialisable.
        out.put("research", jdbc.sql("""
                        SELECT facts_json::text AS facts, to_json(COALESCE(queries, '{}'))::text AS queries, version
                        FROM research WHERE video_id = :v ORDER BY version DESC LIMIT 1
                        """)
                .param("v", videoId).query().listOfRows().stream().findFirst().map(r -> {
                    Map<String, Object> m = new LinkedHashMap<>(r);
                    m.put("queries", json.readValue((String) r.get("queries"), new TypeReference<List<String>>() {
                    }));
                    return m;
                }).orElse(null));
        out.put("concept", latestConcept(videoId).orElse(null));
        out.put("script", latestScript(videoId).orElse(null));
        out.put("music", jdbc.sql("SELECT asset_id, bpm FROM music_cue WHERE video_id = :v").param("v", videoId)
                .query().listOfRows().stream().findFirst().orElse(null));
        List<Map<String, Object>> shots = new ArrayList<>();
        for (Map<String, Object> s : shots(videoId)) {
            Map<String, Object> row = new LinkedHashMap<>(s);
            row.remove("keyframe_uri");
            row.put("prompt_json", json.readValue((String) s.get("prompt_json"), new TypeReference<Map<String, Object>>() {
            }));
            shots.add(row);
        }
        out.put("shots", shots);
        out.put("animaticAssetId", jdbc.sql("SELECT animatic_asset_id FROM video WHERE id = :v").param("v", videoId)
                .query(UUID.class).optional().orElse(null));
        return out;
    }

    // ================================================================================================ helpers

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

    private String prompt(UUID videoId) {
        return jdbc.sql("SELECT prompt FROM video WHERE id = :v").param("v", videoId).query(String.class).single();
    }

    public List<Fact> facts(UUID videoId) {
        return jdbc.sql("SELECT facts_json::text FROM research WHERE video_id = :v ORDER BY version DESC LIMIT 1")
                .param("v", videoId).query(String.class).optional()
                .map(s -> json.readValue(s, new TypeReference<List<Fact>>() {
                })).orElse(List.of());
    }

    private Optional<Concept> latestConcept(UUID videoId) {
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

    private List<Map<String, Object>> shots(UUID videoId) {
        return jdbc.sql("""
                        SELECT s.id, s.ord, s.duration_target_sec, s.shot_type, s.camera_move, s.description,
                               s.prompt_json::text AS prompt_json, s.priority, s.overlay_text, s.keyframe_asset_id,
                               a.uri_hot AS keyframe_uri, p.provider AS keyframe_provider, sc.title AS scene_title
                        FROM shot s JOIN scene sc ON sc.id = s.scene_id
                        LEFT JOIN asset a ON a.id = s.keyframe_asset_id
                        LEFT JOIN provenance p ON p.id = a.provenance_id
                        WHERE sc.video_id = :v ORDER BY s.ord
                        """).param("v", videoId).query().listOfRows();
    }

    private List<Double> beatTimes(UUID videoId) {
        return jdbc.sql("SELECT beat_times_json::text FROM music_cue WHERE video_id = :v").param("v", videoId)
                .query(String.class).optional()
                .map(s -> json.readValue(s, new TypeReference<List<Double>>() {
                })).orElse(List.of());
    }

    private double bpm(UUID videoId) {
        return jdbc.sql("SELECT bpm FROM music_cue WHERE video_id = :v").param("v", videoId)
                .query(BigDecimal.class).optional().map(BigDecimal::doubleValue).orElse(0.0);
    }

    private int nextVersion(String table, UUID videoId) {
        // Table names are fixed constants from this class, never user input.
        return jdbc.sql("SELECT COALESCE(max(version), 0) + 1 FROM " + table + " WHERE video_id = :v")
                .param("v", videoId).query(Integer.class).single();
    }

    private int nextKeyframeAttempt(UUID videoId) {
        return jdbc.sql("SELECT count(*) + 1 FROM asset WHERE video_id = :v AND kind = 'image'")
                .param("v", videoId).query(Integer.class).single();
    }

    private static double round(double v) {
        return Math.round(v * 1000.0) / 1000.0;
    }
}
