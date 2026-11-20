package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamRequest;
import com.projectv.dream.DreamTypes.DreamStep;
import com.projectv.workflow.StageMode;
import com.projectv.workflow.TaskQueues;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;

import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** The Dream Shorts screen's backend: start a Short, read it, and pass the owner's decisions to its workflow. */
@Service
public class DreamStudio {

    /**
     * @param hookTitle  burn a big two-colour hook title into the first ~2 s (default off); {@code hookText} is the owner's own
     *                   (blank = the idea pack's, else the writer's); {@code hookColor} the big word's colour (default red)
     * @param chainShots each clip starts from the last frame of the one before: one continuous camera move (default off);
     *                   {@code chainGroup} = chains of N shots (2: shots 1+2 chained, 3+4 chained...), null/0 = one chain;
     *                   {@code chainLocked} = a locked scene (one chain only): one camera path, one world and seed, cleaned last frames
     * @param ideaPack   with {@code ideaId}: start from a curated concept seed (idea pack) instead of researching; the Scout is skipped
     * @param pathChoice "Choose the path": the Short ends on an end card with two path options, and a finished Short can be
     *                   continued down the chosen path as the next episode (default off)
     * @param gpuHold    GPU power: "auto" (default: the studio turns the pod off when the GPU steps are over) or "manual" (stays on
     *                   until the owner turns it off, or {@code gpuIdleMinutes} (5-30, default 10) pass with no GPU work)
     * @param chainBridge first-last frame chaining: every shot keeps its picture and clip N runs from picture N to
     *                   picture N+1 (default off; wins over {@code chainShots}, the two never run together)
     */
    public record NewDream(String idea, String look, Integer shots, String quality, String defaultMode,
                           Map<String, String> modes, Integer reviewMinutes, String music, Boolean scout,
                           Boolean musicEdit, Boolean ambience, Boolean pacing, Boolean producerDecides,
                           Boolean hookTitle, String hookText, String hookColor, Boolean chainShots, String ideaPack,
                           String ideaId, Integer chainGroup, Boolean chainLocked, Boolean pathChoice, Boolean chainBridge,
                           String gpuHold, Integer gpuIdleMinutes) {
        public NewDream(String idea, String look, Integer shots, String quality, String defaultMode,
                        Map<String, String> modes, Integer reviewMinutes, String music, Boolean scout,
                        Boolean musicEdit, Boolean ambience, Boolean pacing, Boolean producerDecides,
                        Boolean hookTitle, String hookText, String hookColor, Boolean chainShots, String ideaPack,
                        String ideaId, Integer chainGroup, Boolean chainLocked, Boolean pathChoice, Boolean chainBridge) {
            this(idea, look, shots, quality, defaultMode, modes, reviewMinutes, music, scout, musicEdit, ambience, pacing,
                    producerDecides, hookTitle, hookText, hookColor, chainShots, ideaPack, ideaId, chainGroup, chainLocked,
                    pathChoice, chainBridge, null, null);
        }

        public NewDream(String idea, String look, Integer shots, String quality, String defaultMode,
                        Map<String, String> modes, Integer reviewMinutes, String music, Boolean scout,
                        Boolean musicEdit, Boolean ambience, Boolean pacing, Boolean producerDecides,
                        Boolean hookTitle, String hookText, String hookColor, Boolean chainShots, String ideaPack,
                        String ideaId, Integer chainGroup, Boolean chainLocked, Boolean pathChoice) {
            this(idea, look, shots, quality, defaultMode, modes, reviewMinutes, music, scout, musicEdit, ambience, pacing,
                    producerDecides, hookTitle, hookText, hookColor, chainShots, ideaPack, ideaId, chainGroup, chainLocked,
                    pathChoice, null, null, null);
        }

        public NewDream(String idea, String look, Integer shots, String quality, String defaultMode,
                        Map<String, String> modes, Integer reviewMinutes, String music, Boolean scout,
                        Boolean musicEdit, Boolean ambience, Boolean pacing, Boolean producerDecides,
                        Boolean hookTitle, String hookText, String hookColor, Boolean chainShots, String ideaPack,
                        String ideaId, Integer chainGroup, Boolean chainLocked) {
            this(idea, look, shots, quality, defaultMode, modes, reviewMinutes, music, scout, musicEdit, ambience, pacing,
                    producerDecides, hookTitle, hookText, hookColor, chainShots, ideaPack, ideaId, chainGroup, chainLocked, null);
        }

        /** What the Autopilot passes: all of the above, and the three new options off. */
        public NewDream(String idea, String look, Integer shots, String quality, String defaultMode,
                        Map<String, String> modes, Integer reviewMinutes, String music, Boolean scout,
                        Boolean musicEdit, Boolean ambience, Boolean pacing, Boolean producerDecides) {
            this(idea, look, shots, quality, defaultMode, modes, reviewMinutes, music, scout, musicEdit, ambience, pacing,
                    producerDecides, null, null, null, null, null, null, null, null);
        }

        public NewDream(String idea, String look, Integer shots, String quality, String defaultMode,
                        Map<String, String> modes, Integer reviewMinutes, String music, Boolean scout,
                        Boolean musicEdit, Boolean ambience, Boolean pacing, Boolean producerDecides,
                        Boolean hookTitle, String hookText, String hookColor, Boolean chainShots, String ideaPack,
                        String ideaId) {
            this(idea, look, shots, quality, defaultMode, modes, reviewMinutes, music, scout, musicEdit, ambience, pacing,
                    producerDecides, hookTitle, hookText, hookColor, chainShots, ideaPack, ideaId, null, null);
        }

        public NewDream(String idea, String look, Integer shots, String quality, String defaultMode,
                        Map<String, String> modes, Integer reviewMinutes, String music, Boolean scout,
                        Boolean musicEdit, Boolean ambience, Boolean pacing, Boolean producerDecides,
                        Boolean hookTitle, String hookText, String hookColor, Boolean chainShots, String ideaPack,
                        String ideaId, Integer chainGroup) {
            this(idea, look, shots, quality, defaultMode, modes, reviewMinutes, music, scout, musicEdit, ambience, pacing,
                    producerDecides, hookTitle, hookText, hookColor, chainShots, ideaPack, ideaId, chainGroup, null);
        }

        /** The dashboard's form (the Producer follows the global trust switch). */
        public NewDream(String idea, String look, Integer shots, String quality, String defaultMode, Map<String, String> modes,
                        Integer reviewMinutes, String music, Boolean scout, Boolean musicEdit, Boolean ambience, Boolean pacing) {
            this(idea, look, shots, quality, defaultMode, modes, reviewMinutes, music, scout, musicEdit, ambience, pacing, null);
        }
    }

    private final JdbcClient jdbc;
    private final WorkflowClient temporal;
    private final ObjectMapper json;
    private final DreamService dreams;
    private final com.projectv.video.ClipProgress clipProgress;
    private final DreamProducer producer;

    public DreamStudio(JdbcClient jdbc, WorkflowClient temporal, ObjectMapper json, DreamService dreams,
                       com.projectv.video.ClipProgress clipProgress, DreamProducer producer) {
        this.producer = producer;
        this.jdbc = jdbc;
        this.temporal = temporal;
        this.json = json;
        this.dreams = dreams;
        this.clipProgress = clipProgress;
    }

    public UUID create(NewDream req) {
        return create(req, null);
    }

    /** A continued episode: the parent, the path picked and the parent's last clip (shot 1 starts from its last frame). */
    record Continuation(UUID parentId, int episode, String chosenPath, String pickSource, UUID startClipAssetId, long seed) {
    }

    UUID create(NewDream req, Continuation cont) {
        // Unfiltered (owner, 20 Nov 2026): the prompt is used exactly as written, so nothing that rewrites or judges it is on:
        // no idea pack, Scout, hook title, path-choice end card, editing rules (music cut, ambience, pacing) or Producer.
        boolean raw = DreamService.UNFILTERED.equals(req.look());
        boolean seeded = !raw && req.ideaPack() != null && !req.ideaPack().isBlank() && req.ideaId() != null && !req.ideaId().isBlank();
        var seed = seeded ? DreamIdeaPacks.idea(req.ideaPack(), req.ideaId()) : java.util.Optional.<DreamIdeaPacks.Idea>empty();
        if (seeded && seed.isEmpty()) {
            throw new IllegalArgumentException("Unknown idea: " + req.ideaPack() + "/" + req.ideaId());
        }
        if (!seeded && (req.idea() == null || req.idea().isBlank())) {
            throw new IllegalArgumentException("Type an idea first");
        }
        String ideaText = req.idea() == null || req.idea().isBlank() ? seed.get().concept() : req.idea().strip();
        String look = req.look() != null && DreamService.LOOKS.containsKey(req.look()) ? req.look() : DreamService.LOOK_ORDER.getFirst();
        if (seeded) { // the pack decides the look; the idea comes from the pack, so there is nothing for the Scout to research
            look = DreamIdeaPacks.pack(req.ideaPack()).map(DreamIdeaPacks.Pack::look).filter(DreamService.LOOKS::containsKey).orElse(look);
        }
        boolean hookTitle = !raw && Boolean.TRUE.equals(req.hookTitle());
        String hookText = !hookTitle ? null : req.hookText() != null && !req.hookText().isBlank() ? req.hookText().strip()
                : seed.map(DreamIdeaPacks.Idea::hook).orElse(null);
        // Unfiltered: one shot per non-empty line of the prompt (the writing step sets the real count).
        int shots = raw ? (int) Math.max(1, Math.min(8, ideaText.lines().filter(l -> !l.isBlank()).count()))
                : req.shots() == null ? 5 : Math.max(2, Math.min(8, req.shots()));
        String gpuHold = "manual".equalsIgnoreCase(req.gpuHold()) ? "manual" : "auto";
        int gpuIdle = req.gpuIdleMinutes() == null ? 10 : Math.max(5, Math.min(30, req.gpuIdleMinutes()));
        String quality = "720p".equals(req.quality()) ? "720p" : "480p";
        int review = req.reviewMinutes() == null ? 10 : Math.max(1, Math.min(240, req.reviewMinutes()));
        // One chain style per Short: first-last frame wins over last-frame chaining (the two need different pictures).
        boolean bridge = Boolean.TRUE.equals(req.chainBridge());
        boolean lastFrame = !bridge && Boolean.TRUE.equals(req.chainShots());
        StageMode defaultMode = StageMode.parse(req.defaultMode() == null ? "REVIEW" : req.defaultMode());
        Map<DreamStep, StageMode> modes = new EnumMap<>(DreamStep.class);
        if (req.modes() != null) {
            req.modes().forEach((k, v) -> modes.put(DreamStep.valueOf(k.toUpperCase()), StageMode.parse(v)));
        }
        UUID id = UUID.randomUUID();
        String workflowId = "dream-" + id;
        Map<String, String> modeNames = new LinkedHashMap<>();
        for (DreamStep s : DreamStep.values()) {
            modeNames.put(s.name(), modes.getOrDefault(s, defaultMode).name());
        }
        jdbc.sql("""
                        INSERT INTO dream_short (id, idea, look, shot_count, quality, modes_json, review_minutes, status, step, workflow_id,
                                                 music_source, scout, edit_music, edit_ambience, edit_pacing, producer_decides,
                                                 hook_title, hook_text, hook_color, hook_locked, chain_shots, idea_ref,
                                                 chain_group, chain_locked, path_choice, parent_id, episode, chosen_path,
                                                 pick_source, start_clip_asset_id, scene_seed, chain_bridge, gpu_hold, gpu_idle_minutes)
                        VALUES (:id, :idea, :look, :n, :q, CAST(:modes AS jsonb), :r, 'RUNNING', 'SCOUT', :wf, :music, :scout,
                                :em, :ea, :ep, :pd, :ht, :htext, :hcol, :hlock, :chain, :iref, :cgroup, :clocked, :pc, :parent,
                                :episode, :cpath, :psrc, :sclip, :seed, :bridge, :ghold, :gidle)
                        """)
                .param("bridge", bridge).param("ghold", gpuHold).param("gidle", gpuIdle)
                .param("pc", !raw && Boolean.TRUE.equals(req.pathChoice()))
                .param("parent", cont == null ? null : cont.parentId()).param("episode", cont == null ? 1 : cont.episode())
                .param("cpath", cont == null ? null : cont.chosenPath()).param("psrc", cont == null ? null : cont.pickSource())
                .param("sclip", cont == null ? null : cont.startClipAssetId()).param("seed", cont == null ? null : cont.seed())
                .param("clocked", lastFrame && Boolean.TRUE.equals(req.chainLocked())
                        && (req.chainGroup() == null || req.chainGroup() == 0))
                .param("cgroup", !lastFrame || req.chainGroup() == null ? 0 : Math.max(0, Math.min(8, req.chainGroup())))
                .param("ht", hookTitle).param("htext", hookText)
                .param("hcol", req.hookColor() == null || req.hookColor().isBlank() ? null : req.hookColor().strip())
                .param("hlock", hookText != null).param("chain", lastFrame)
                .param("iref", seeded ? DreamIdeaPacks.ref(req.ideaPack(), req.ideaId()) : null)
                .param("pd", !raw && Boolean.TRUE.equals(req.producerDecides()))
                .param("em", !raw && (req.musicEdit() == null || req.musicEdit()))
                .param("ea", !raw && (req.ambience() == null || req.ambience()))
                .param("ep", !raw && (req.pacing() == null || req.pacing()))
                .param("scout", !seeded && !raw && (req.scout() == null || req.scout()))
                .param("music", "lyria".equalsIgnoreCase(req.music()) ? "lyria" : "bed")
                .param("id", id).param("idea", ideaText).param("look", look).param("n", shots).param("q", quality)
                .param("modes", json.writeValueAsString(modeNames)).param("r", review).param("wf", workflowId).update();
        DreamShortWorkflow wf = temporal.newWorkflowStub(DreamShortWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(workflowId).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new DreamRequest(id.toString(), defaultMode, modes, review));
        var created = new java.util.LinkedHashMap<String, Object>(Map.of("look", look, "shots", shots, "quality", quality,
                "music", "lyria".equalsIgnoreCase(req.music()) ? "lyria" : "bed", "mode", defaultMode.name()));
        if (hookTitle) created.put("hookTitle", true);
        if (lastFrame) created.put("chainShots", true);
        if (bridge) created.put("chainBridge", true);
        if (seeded) created.put("idea", DreamIdeaPacks.ref(req.ideaPack(), req.ideaId()));
        if (Boolean.TRUE.equals(req.pathChoice())) created.put("pathChoice", true);
        if (cont != null) created.putAll(Map.of("parent", cont.parentId().toString(), "episode", cont.episode(),
                "path", cont.chosenPath(), "pickedBy", cont.pickSource()));
        dreams.evidence().event(id, null, "create", null, ideaText, created);
        return id;
    }

    /**
     * Redo a finished (or cancelled) Short from a step: that step and the ones after it run again; earlier results
     * stay. Modes come from the Short's saved settings.
     */
    public void rerun(UUID id, DreamStep from) {
        Map<String, Object> d = dreams.dream(id);
        String st = (String) d.get("status");
        if (!"COMPLETED".equals(st) && !"CANCELLED".equals(st)) {
            throw new IllegalStateException("This Short is still running: use approve / redo on its current step");
        }
        Map<String, String> saved = json.readValue(d.get("modes_json").toString(),
                new tools.jackson.core.type.TypeReference<Map<String, String>>() {
                });
        Map<DreamStep, StageMode> modes = new EnumMap<>(DreamStep.class);
        saved.forEach((k, v) -> {
            try {
                modes.put(DreamStep.valueOf(k), StageMode.parse(v));
            } catch (IllegalArgumentException ignored) { // a step that no longer exists
            }
        });
        jdbc.sql("UPDATE dream_short SET status = 'RUNNING', step = :s, message = NULL, updated_at = now() WHERE id = :id")
                .param("s", from.name()).param("id", id).update();
        DreamShortWorkflow wf = temporal.newWorkflowStub(DreamShortWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId("dream-" + id).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new DreamRequest(id.toString(), StageMode.AUTO, modes,
                ((Number) d.get("review_minutes")).intValue(), from));
        dreams.evidence().event(id, from.name(), "rerun", null, null, null);
    }

    public List<Map<String, Object>> list() {
        return jdbc.sql("""
                        SELECT d.id, d.idea, d.title, d.look, d.status, d.step, d.message, d.final_asset_id, d.created_at, d.updated_at,
                               d.episode, d.parent_id,
                               (SELECT p.picture_asset_id FROM dream_shot p WHERE p.dream_short_id = d.id ORDER BY p.ord LIMIT 1) AS thumb_asset_id,
                               COALESCE((SELECT sum(amount_inr) FROM cost_entry c WHERE c.dream_short_id = d.id), 0) AS spent_inr
                        FROM dream_short d ORDER BY d.created_at DESC LIMIT 100
                        """).query().listOfRows();
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> detail(UUID id) {
        Map<String, Object> out = new LinkedHashMap<>(dreams.dream(id));
        out.remove("world_json");
        out.put("branch_json", out.get("branch_json") == null ? null : out.get("branch_json").toString());
        out.put("chain", chain(id));
        out.put("children", jdbc.sql("SELECT id, title, chosen_path, status FROM dream_short WHERE parent_id = :d ORDER BY created_at")
                .param("d", id).query().listOfRows());
        out.put("scout_json", out.get("scout_json") == null ? null : out.get("scout_json").toString());
        out.put("check_json", out.get("check_json") == null ? null : out.get("check_json").toString());
        out.put("picture_check_json", out.get("picture_check_json") == null ? null : out.get("picture_check_json").toString());
        out.put("shots", jdbc.sql("""
                        SELECT id, ord, scene, picture_prompt, motion_prompt, picture_asset_id, clip_asset_id, picture_takes,
                               clip_takes, error FROM dream_shot WHERE dream_short_id = :d ORDER BY ord
                        """).param("d", id).query().listOfRows());
        out.put("costs", jdbc.sql("""
                        SELECT provider_id, amount_inr, units, unit_type, estimated, note, ts FROM cost_entry
                        WHERE dream_short_id = :d ORDER BY ts
                        """).param("d", id).query().listOfRows());
        out.put("decisions", jdbc.sql("""
                        SELECT stage, capability, chosen_provider_id, reason_text, candidates_json::text AS candidates, created_at
                        FROM routing_decision WHERE dream_short_id = :d ORDER BY created_at
                        """).param("d", id).query().listOfRows());
        var events = dreams.evidence().events(id);
        out.put("events", events);
        List<DreamTimeline.Event> parsed = events.stream().map(this::event).toList();
        List<DreamTimeline.Cost> costs = ((List<Map<String, Object>>) out.get("costs")).stream()
                .map(c -> new DreamTimeline.Cost(((Number) c.get("amount_inr")).doubleValue(), instant(c.get("ts")))).toList();
        out.put("timeline", DreamTimeline.steps(parsed, costs, java.time.Instant.now(), typicalStepTimes(id)));
        out.put("log", DreamTimeline.log(parsed));
        out.put("producer", producer.decisions(id));
        out.put("metrics", dreams.evidence().metrics(id));
        var progress = clipProgress.get(id);
        out.put("clipProgress", progress == null ? null : progress.clips());
        try {
            out.put("view", stub(id).view());
        } catch (Exception e) { // finished workflows can still be queried; a missing one just has no live view
            out.put("view", null);
        }
        return out;
    }

    public void approve(UUID id, DreamStep step) {
        stub(id).approve(step);
        producer.recordOwnerDecision(id, step.name(), "approve", null);
        dreams.evidence().event(id, step.name(), "approve", null, null, null);
    }

    public void redo(UUID id, DreamStep step, List<String> shotIds, String notes) {
        stub(id).redo(step, shotIds, notes);
        producer.recordOwnerDecision(id, step.name(), "reject", notes);
        dreams.evidence().event(id, step.name(), "redo", shotIds, notes, null);
    }

    public void setMode(UUID id, DreamStep step, StageMode mode) {
        stub(id).setMode(step, mode);
        dreams.evidence().event(id, step.name(), "mode", null, null, Map.of("mode", mode.name()));
        jdbc.sql("UPDATE dream_short SET modes_json = jsonb_set(modes_json, CAST(:path AS text[]), to_jsonb(CAST(:m AS text))) WHERE id = :id")
                .param("path", "{" + step.name() + "}").param("m", mode.name()).param("id", id).update();
    }

    public void retry(UUID id) {
        stub(id).retry();
        dreams.evidence().event(id, null, "retry", null, null, null);
    }

    /**
     * The owner's Cancel. A Short whose workflow has already ended (a second click, or it finished meanwhile) is not an
     * error: Temporal answers NOT_FOUND for a signal to a closed workflow, which used to surface as HTTP 500.
     */
    public void cancel(UUID id) {
        Map<String, Object> d = dreams.dream(id);
        boolean clipRunning = "CLIPS".equals(d.get("step")) && "RUNNING".equals(d.get("status"));
        clipProgress.requestStop(id);
        try {
            stub(id).cancel();
        } catch (io.temporal.client.WorkflowNotFoundException e) {
            jdbc.sql("""
                            UPDATE dream_short SET status = 'CANCELLED', updated_at = now()
                            WHERE id = :id AND status NOT IN ('COMPLETED', 'CANCELLED')
                            """).param("id", id).update();
            dreams.evidence().event(id, (String) d.get("step"), "cancel", null, null, Map.of("alreadyEnded", true));
            return;
        }
        dreams.evidence().event(id, (String) d.get("step"), "cancel", null, null,
                clipRunning ? Map.of("afterClip", true) : null);
    }

    /** The owner's Stop: clips in flight stop after the one being rendered (rule 8). */
    public void stopClips(UUID id) {
        clipProgress.requestStop(id);
        dreams.evidence().event(id, "CLIPS", "stop", null, null, null);
    }

    /** The owner edits the two path options of the end card; they show at the next finish and the writer never replaces them. */
    public void setPaths(UUID id, String a, String b) {
        dreams.setPaths(id, a, b);
        dreams.evidence().event(id, "FINISH", "paths", null, a + " / " + b, null);
    }

    /**
     * "Continue this dream": a new Short, the next episode, down the picked path in the same world. Shot 1 starts from the
     * parent's last frame (chained-shots machinery); the writer reads the parent's branch memory; settings and the world's
     * seed are inherited. Picking is a {@link DreamBranches.PathPick}: the owner by hand now, the comment vote later.
     */
    @SuppressWarnings("unchecked")
    public UUID continueDream(UUID parentId, DreamBranches.PathPick pick) {
        Map<String, Object> p = dreams.dream(parentId);
        if (!Boolean.TRUE.equals(p.get("path_choice"))) {
            throw new IllegalStateException("This Short has no path choice: turn on Choose the path for a new Short");
        }
        if (!"COMPLETED".equals(p.get("status")) || p.get("final_asset_id") == null || p.get("branch_json") == null) {
            throw new IllegalStateException("Finish this Short first: its last frame starts the next episode");
        }
        String path = DreamBranches.pathOf(p, pick.path());
        if (path == null || path.isBlank()) {
            throw new IllegalStateException("Path " + pick.path() + " has no text yet");
        }
        Map<String, Object> branch = json.readValue(p.get("branch_json").toString(),
                new tools.jackson.core.type.TypeReference<Map<String, Object>>() {
                });
        Object lastClip = branch.get("lastClipAssetId");
        if (lastClip == null) {
            throw new IllegalStateException("The last shot has no clip: redo it first");
        }
        Map<String, String> modes = json.readValue(p.get("modes_json").toString(),
                new tools.jackson.core.type.TypeReference<Map<String, String>>() {
                });
        int episode = ((Number) p.get("episode")).intValue() + 1;
        // Same settings as the parent: same look, length, quality, editing, hook and chain options, path choice stays on.
        NewDream req = new NewDream(DreamBranches.childIdea(p.get("title"), path, episode), (String) p.get("look"),
                ((Number) p.get("shot_count")).intValue(), (String) p.get("quality"), "REVIEW", modes,
                ((Number) p.get("review_minutes")).intValue(), (String) p.get("music_source"), false,
                (Boolean) p.get("edit_music"), (Boolean) p.get("edit_ambience"), (Boolean) p.get("edit_pacing"),
                (Boolean) p.get("producer_decides"), (Boolean) p.get("hook_title"), null, (String) p.get("hook_color"),
                (Boolean) p.get("chain_shots"), null, null, ((Number) p.get("chain_group")).intValue(),
                (Boolean) p.get("chain_locked"), true, (Boolean) p.get("chain_bridge"));
        UUID child = create(req, new Continuation(parentId, episode, path, pick.source(), UUID.fromString(lastClip.toString()),
                ((Number) branch.get("seed")).longValue()));
        dreams.evidence().event(parentId, null, "continue", null, path,
                pick.votes() == null ? Map.of("child", child.toString(), "path", pick.path(), "source", pick.source())
                        : Map.of("child", child.toString(), "path", pick.path(), "source", pick.source(), "votes", pick.votes()));
        return child;
    }

    /** The episode chain this Short belongs to, oldest first (episode 1, 2, 3...), following parent links. */
    public List<Map<String, Object>> chain(UUID id) {
        return jdbc.sql("""
                        WITH RECURSIVE up AS (
                            SELECT id, parent_id, 0 AS depth FROM dream_short WHERE id = :id
                            UNION ALL SELECT d.id, d.parent_id, up.depth + 1 FROM dream_short d JOIN up ON d.id = up.parent_id
                              WHERE up.depth < 100)
                        SELECT d.id, d.episode, d.title, d.chosen_path, d.pick_source, d.status, d.final_asset_id
                        FROM up JOIN dream_short d ON d.id = up.id ORDER BY d.episode
                        """).param("id", id).query().listOfRows();
    }

    /** The owner edits the hook title (and its colour); it shows at the next finish and the writer never replaces it. */
    public void setHook(UUID id, String text, String color) {
        dreams.setHook(id, text, color);
        dreams.evidence().event(id, "FINISH", "hook", null, text, null);
    }

    /** The owner picks which of the Scout's angles the writer follows (1-based); then approves the Scout step. */
    public void pickAngle(UUID id, int angle) {
        jdbc.sql("UPDATE dream_short SET scout_angle = :a, updated_at = now() WHERE id = :id").param("a", Math.max(1, angle))
                .param("id", id).update();
        dreams.evidence().event(id, "SCOUT", "angle", null, null, Map.of("angle", angle));
    }

    /** The editing switches (music-led cuts, J-cut ambience, pacing); they apply at the next finish or re-finish. */
    public void editing(UUID id, Boolean musicEdit, Boolean ambience, Boolean pacing) {
        jdbc.sql("""
                        UPDATE dream_short SET edit_music = COALESCE(:em, edit_music), edit_ambience = COALESCE(:ea, edit_ambience),
                               edit_pacing = COALESCE(:ep, edit_pacing), updated_at = now() WHERE id = :id
                        """).param("em", musicEdit).param("ea", ambience).param("ep", pacing).param("id", id).update();
        var data = new java.util.HashMap<String, Object>();
        if (musicEdit != null) data.put("musicEdit", musicEdit);
        if (ambience != null) data.put("ambience", ambience);
        if (pacing != null) data.put("pacing", pacing);
        dreams.evidence().event(id, "FINISH", "editing", null, null, data);
    }

    public List<Map<String, Object>> calibration() {
        return dreams.evidence().calibration();
    }

    /** Median work time per step over the last 20 completed Shorts (from their status events). */
    private Map<String, Long> typicalStepTimes(UUID except) {
        Map<Object, List<DreamTimeline.Event>> byShort = new LinkedHashMap<>();
        jdbc.sql("""
                        SELECT dream_short_id, step, kind, notes, data::text AS data, created_at FROM dream_event
                        WHERE kind = 'status' AND dream_short_id IN (
                            SELECT id FROM dream_short WHERE status = 'COMPLETED' AND id <> :d ORDER BY updated_at DESC LIMIT 20)
                        ORDER BY created_at
                        """).param("d", except).query().listOfRows()
                .forEach(r -> byShort.computeIfAbsent(r.get("dream_short_id"), k -> new java.util.ArrayList<>()).add(event(r)));
        return DreamTimeline.typical(byShort.values().stream().map(DreamTimeline::finishedWork).toList());
    }

    private DreamTimeline.Event event(Map<String, Object> r) {
        Object data = r.get("data");
        Map<String, Object> parsed = data == null ? null : json.readValue(data.toString(),
                new tools.jackson.core.type.TypeReference<Map<String, Object>>() {
                });
        return new DreamTimeline.Event((String) r.get("step"), (String) r.get("kind"), (String) r.get("notes"), parsed,
                instant(r.get("created_at")));
    }

    private static java.time.Instant instant(Object ts) {
        return switch (ts) {
            case java.sql.Timestamp t -> t.toInstant();
            case java.time.OffsetDateTime o -> o.toInstant();
            case java.time.Instant i -> i;
            default -> java.time.Instant.parse(ts.toString());
        };
    }

    private DreamShortWorkflow stub(UUID id) {
        return temporal.newWorkflowStub(DreamShortWorkflow.class, "dream-" + id);
    }
}
