package com.projectv.autopilot;

import com.projectv.agents.Llm;
import com.projectv.autopilot.AutopilotTypes.PostKit;
import com.projectv.dream.DreamService;
import com.projectv.dream.DreamStudio;
import com.projectv.dream.DreamStudio.NewDream;
import com.projectv.workflow.StageMode;
import com.projectv.workflow.TaskQueues;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.sql.Timestamp;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

/**
 * Daily autopilot (owner approved 19 Nov 2026). Off until the owner turns it on. Each day at the start time it takes
 * the next idea (the owner's list first, then an unused Scout angle), starts one Dream Short with the AI Producer
 * deciding inside the ₹ limits, and when the Short is finished writes a title, description and hashtags and marks it
 * READY. Nothing is uploaded until the owner taps Post. If a run fails or needs the owner, no new run starts until
 * the owner deals with it.
 */
@Service
public class AutopilotService {

    private static final Logger log = LoggerFactory.getLogger(AutopilotService.class);

    /** Runs that are still in hand: a new day's run waits until none of these is left. */
    static final Set<String> BLOCKING = Set.of("WAITING_START", "RUNNING", "NEEDS_YOU", "FAILED");
    static final List<String> STEPS = List.of("SCOUT", "WRITE", "PICTURES", "CLIPS", "MUSIC", "FINISH");

    private final JdbcClient jdbc;
    private final ObjectMapper json;
    private final DreamStudio studio;
    private final WorkflowClient temporal;
    private final Llm llm;
    private final ZoneId zone;
    private final Clock clock;

    @org.springframework.beans.factory.annotation.Autowired
    public AutopilotService(JdbcClient jdbc, ObjectMapper json, DreamStudio studio, WorkflowClient temporal, Llm llm,
                            @Value("${projectv.autopilot.zone:Asia/Kolkata}") String zone) {
        this(jdbc, json, studio, temporal, llm, ZoneId.of(zone), Clock.systemUTC());
    }

    AutopilotService(JdbcClient jdbc, ObjectMapper json, DreamStudio studio, WorkflowClient temporal, Llm llm, ZoneId zone,
                     Clock clock) {
        this.jdbc = jdbc;
        this.json = json;
        this.studio = studio;
        this.temporal = temporal;
        this.llm = llm;
        this.zone = zone;
        this.clock = clock;
    }

    // ------------------------------------------------------------------------------------------- settings

    public record Settings(boolean enabled, String startTime, String startMode, int reviewMinutes, BigDecimal dailyCapInr,
                           Map<String, String> stepModes, String look, int shots, boolean useScout, String privacy) {
    }

    public Settings settings() {
        Map<String, Object> r = jdbc.sql("SELECT * FROM autopilot_setting WHERE id = 1").query().singleRow();
        return new Settings((Boolean) r.get("enabled"), r.get("start_time").toString().substring(0, 5), (String) r.get("start_mode"),
                ((Number) r.get("review_minutes")).intValue(), (BigDecimal) r.get("daily_cap_inr"),
                json.readValue(r.get("step_modes").toString(), new TypeReference<LinkedHashMap<String, String>>() {
                }), (String) r.get("look"), ((Number) r.get("shots")).intValue(), (Boolean) r.get("use_scout"),
                (String) r.get("privacy"));
    }

    public Settings saveSettings(Settings s) {
        StageMode.parse(s.startMode()); // validates
        if (StageMode.parse(s.startMode()) == StageMode.PRODUCER) {
            throw new IllegalArgumentException("Start mode is Auto, Review or Manual");
        }
        Map<String, String> modes = new LinkedHashMap<>();
        for (String step : STEPS) {
            String m = s.stepModes() == null ? null : s.stepModes().get(step);
            modes.put(step, StageMode.parse(m == null ? "PRODUCER" : m).name());
        }
        if (!Set.of("private", "unlisted", "public").contains(s.privacy())) {
            throw new IllegalArgumentException("privacy must be private, unlisted or public");
        }
        if (s.dailyCapInr() == null || s.dailyCapInr().signum() < 0) {
            throw new IllegalArgumentException("daily cap must be ₹0 or more");
        }
        jdbc.sql("""
                        UPDATE autopilot_setting SET enabled = :e, start_time = CAST(:t AS time), start_mode = :sm, review_minutes = :rm,
                               daily_cap_inr = :cap, step_modes = CAST(:modes AS jsonb), look = :look, shots = :shots,
                               use_scout = :scout, privacy = :p, updated_at = now() WHERE id = 1
                        """)
                .param("e", s.enabled()).param("t", LocalTime.parse(s.startTime()).toString())
                .param("sm", StageMode.parse(s.startMode()).name()).param("rm", Math.max(1, Math.min(240, s.reviewMinutes())))
                .param("cap", s.dailyCapInr()).param("modes", json.writeValueAsString(modes))
                .param("look", s.look() == null || s.look().isBlank() ? null : s.look())
                .param("shots", Math.max(2, Math.min(8, s.shots()))).param("scout", s.useScout()).param("p", s.privacy()).update();
        return settings();
    }

    // ------------------------------------------------------------------------------------------- ideas

    public List<Map<String, Object>> ideas() {
        return jdbc.sql("SELECT id, idea, look, source, used_at, created_at FROM autopilot_idea ORDER BY used_at NULLS FIRST, created_at")
                .query().listOfRows();
    }

    public UUID addIdea(String idea, String look) {
        if (idea == null || idea.isBlank()) {
            throw new IllegalArgumentException("Type an idea first");
        }
        UUID id = UUID.randomUUID();
        jdbc.sql("INSERT INTO autopilot_idea (id, idea, look, source) VALUES (:id, :i, :l, 'owner')")
                .param("id", id).param("i", idea.strip()).param("l", look == null || look.isBlank() ? null : look).update();
        return id;
    }

    public void deleteIdea(UUID id) {
        jdbc.sql("DELETE FROM autopilot_idea WHERE id = :id").param("id", id).update();
    }

    /** Scout angles from earlier Shorts that were not the one used, added to the list once (source = scout). */
    int harvestScoutAngles() {
        int added = 0;
        for (Map<String, Object> r : jdbc.sql("""
                        SELECT id, look, scout_json::text AS scout, scout_angle FROM dream_short
                        WHERE scout_json IS NOT NULL ORDER BY created_at DESC LIMIT 30
                        """).query().listOfRows()) {
            Map<String, Object> scout = json.readValue((String) r.get("scout"), new TypeReference<Map<String, Object>>() {
            });
            Object brief = scout.get("brief");
            if (!(brief instanceof Map<?, ?> b) || !(b.get("angles") instanceof List<?> angles)) {
                continue;
            }
            int used = ((Number) r.get("scout_angle")).intValue();
            for (int i = 0; i < angles.size(); i++) {
                if (i + 1 == used || !(angles.get(i) instanceof Map<?, ?> a) || a.get("idea") == null) {
                    continue;
                }
                String idea = a.get("idea").toString().strip();
                added += jdbc.sql("""
                                INSERT INTO autopilot_idea (id, idea, look, source)
                                SELECT :id, :i, :l, 'scout' WHERE NOT EXISTS (SELECT 1 FROM autopilot_idea WHERE idea = :i)
                                """).param("id", UUID.randomUUID()).param("i", idea).param("l", r.get("look")).update();
            }
        }
        return added;
    }

    /** The next idea: the owner's own first (oldest), then Scout angles when allowed. null = none left. */
    Map<String, Object> nextIdea(boolean useScout) {
        String sql = """
                SELECT id, idea, look FROM autopilot_idea WHERE used_at IS NULL %s
                ORDER BY CASE source WHEN 'owner' THEN 0 ELSE 1 END, created_at LIMIT 1
                """.formatted(useScout ? "" : "AND source = 'owner'");
        var idea = jdbc.sql(sql).query().listOfRows();
        if (idea.isEmpty() && useScout && harvestScoutAngles() > 0) {
            idea = jdbc.sql(sql).query().listOfRows();
        }
        return idea.isEmpty() ? null : idea.getFirst();
    }

    // ------------------------------------------------------------------------------------------- the daily tick

    /** What today's tick should do. Pure, so the rules are tested without a database or clock. */
    enum Action { NOTHING, BLOCKED, CREATE, START_DUE }

    static Action decide(boolean enabled, LocalTime now, LocalTime startTime, Map<String, Object> todaysRun,
                         boolean earlierRunInHand, Instant nowInstant) {
        if (!enabled) {
            return Action.NOTHING;
        }
        if (todaysRun != null) {
            Object startsAt = todaysRun.get("starts_at");
            boolean due = "WAITING_START".equals(todaysRun.get("status")) && startsAt != null
                    && !toInstant(startsAt).isAfter(nowInstant);
            return due ? Action.START_DUE : Action.NOTHING;
        }
        if (now.isBefore(startTime)) {
            return Action.NOTHING;
        }
        return earlierRunInHand ? Action.BLOCKED : Action.CREATE;
    }

    private static Instant toInstant(Object ts) {
        return ts instanceof Timestamp t ? t.toInstant() : ts instanceof java.time.OffsetDateTime o ? o.toInstant() : (Instant) ts;
    }

    /** Called every minute by {@link AutopilotScheduler}. Keeps runs in step with their Shorts, then maybe starts today's. */
    public synchronized void tick() {
        refreshRuns();
        Settings s = settings();
        ZonedDateTime now = ZonedDateTime.now(clock).withZoneSameInstant(zone);
        LocalDate today = now.toLocalDate();
        Map<String, Object> run = run(today);
        boolean inHand = jdbc.sql("SELECT count(*) FROM autopilot_run WHERE run_date < :d AND status IN (:st)")
                .param("d", today).param("st", BLOCKING).query(Long.class).single() > 0;
        switch (decide(s.enabled(), now.toLocalTime(), LocalTime.parse(s.startTime()), run, inHand, now.toInstant())) {
            case CREATE -> create(today, s, now.toInstant());
            case START_DUE -> start((UUID) run.get("id"), s);
            case BLOCKED, NOTHING -> {
            }
        }
    }

    private Map<String, Object> run(LocalDate day) {
        return jdbc.sql("SELECT * FROM autopilot_run WHERE run_date = :d").param("d", day).query().listOfRows().stream()
                .findFirst().orElse(null);
    }

    /** Today's run: picks the idea now, then starts (Auto), waits the review window (Review) or waits for Start (Manual). */
    void create(LocalDate today, Settings s, Instant now) {
        Map<String, Object> idea = nextIdea(s.useScout());
        UUID id = UUID.randomUUID();
        if (idea == null) {
            jdbc.sql("INSERT INTO autopilot_run (id, run_date, status, message) VALUES (:id, :d, 'SKIPPED', :m) ON CONFLICT DO NOTHING")
                    .param("id", id).param("d", today).param("m", "No ideas left: add some to the list.").update();
            return;
        }
        StageMode mode = StageMode.parse(s.startMode());
        Instant startsAt = switch (mode) {
            case AUTO -> now;
            case MANUAL -> null;
            default -> now.plusSeconds(60L * s.reviewMinutes());
        };
        int inserted = jdbc.sql("""
                        INSERT INTO autopilot_run (id, run_date, status, message, idea, starts_at)
                        VALUES (:id, :d, 'WAITING_START', :m, :idea, :at) ON CONFLICT (run_date) DO NOTHING
                        """).param("id", id).param("d", today)
                .param("m", mode == StageMode.MANUAL ? "Waiting for you to tap Start." : "Starts at the set time.")
                .param("idea", idea.get("idea")).param("at", startsAt == null ? null : Timestamp.from(startsAt)).update();
        if (inserted == 0) {
            return;
        }
        jdbc.sql("UPDATE autopilot_idea SET used_at = now() WHERE id = :id").param("id", idea.get("id")).update();
        jdbc.sql("UPDATE autopilot_run SET message = :m WHERE id = :id").param("id", id)
                .param("m", (mode == StageMode.MANUAL ? "Waiting for you to tap Start." : "Starts soon.")
                        + (idea.get("look") == null ? "" : " Look: " + idea.get("look"))).update();
        if (mode == StageMode.AUTO) {
            start(id, s);
        }
    }

    /** The owner's Start (Manual), or the time came: start the Dream Short for this run. */
    public synchronized void start(UUID runId, Settings s) {
        Map<String, Object> run = jdbc.sql("SELECT * FROM autopilot_run WHERE id = :id").param("id", runId).query().singleRow();
        if (!"WAITING_START".equals(run.get("status"))) {
            return;
        }
        String idea = (String) run.get("idea");
        String look = jdbc.sql("SELECT look FROM autopilot_idea WHERE idea = :i AND look IS NOT NULL LIMIT 1").param("i", idea)
                .query(String.class).optional().orElse(s.look());
        if (look == null) { // rotate through the looks, one per day
            List<String> looks = DreamService.LOOK_ORDER;
            look = looks.get(((LocalDate) toLocalDate(run.get("run_date"))).getDayOfYear() % looks.size());
        }
        try {
            UUID dream = studio.create(new NewDream(idea, look, s.shots(), "480p", "PRODUCER", s.stepModes(), 10, "bed", true,
                    true, true, true, true));
            jdbc.sql("""
                            UPDATE autopilot_run SET status = 'RUNNING', dream_short_id = :d, message = :m, starts_at = now(), updated_at = now()
                            WHERE id = :id
                            """).param("d", dream).param("m", "Making the Short (" + look + ").").param("id", runId).update();
        } catch (RuntimeException e) {
            log.warn("Autopilot could not start today's Short", e);
            jdbc.sql("UPDATE autopilot_run SET status = 'FAILED', message = :m, updated_at = now() WHERE id = :id")
                    .param("m", "Could not start: " + e.getMessage()).param("id", runId).update();
        }
    }

    public void startNow(UUID runId) {
        start(runId, settings());
    }

    private static Object toLocalDate(Object d) {
        return d instanceof java.sql.Date sd ? sd.toLocalDate() : d;
    }

    /** Status of a run from its Short's status. Pure. */
    static String runStatus(String dreamStatus) {
        return switch (dreamStatus == null ? "" : dreamStatus) {
            case "COMPLETED" -> "READY";
            case "WAITING", "WAITING_COST", "FAILED_WAITING" -> "NEEDS_YOU";
            case "CANCELLED" -> "FAILED";
            default -> "RUNNING";
        };
    }

    /** Follows each running Short: needs you / failed / finished (then writes the publishing kit and marks READY). */
    void refreshRuns() {
        for (Map<String, Object> r : jdbc.sql("""
                        SELECT r.id, r.status, d.id AS dream_id, d.status AS dream_status, d.message AS dream_message
                        FROM autopilot_run r JOIN dream_short d ON d.id = r.dream_short_id
                        WHERE r.status IN ('RUNNING', 'NEEDS_YOU')
                        """).query().listOfRows()) {
            String next = runStatus((String) r.get("dream_status"));
            if ("READY".equals(next)) {
                try {
                    PostKit kit = postKit((UUID) r.get("dream_id"));
                    jdbc.sql("""
                                    UPDATE autopilot_run SET status = 'READY', title = :t, description = :desc, hashtags = :h,
                                           privacy = (SELECT privacy FROM autopilot_setting WHERE id = 1),
                                           message = 'Ready to post: check it and tap Post.', updated_at = now() WHERE id = :id
                                    """).param("t", kit.title()).param("desc", kit.description())
                            .param("h", String.join(" ", kit.hashtags())).param("id", r.get("id")).update();
                } catch (RuntimeException e) {
                    log.warn("Autopilot could not write the post kit", e);
                    jdbc.sql("UPDATE autopilot_run SET status = 'NEEDS_YOU', message = :m, updated_at = now() WHERE id = :id")
                            .param("m", "Finished, but the title/description failed: " + e.getMessage()
                                    + " Write them yourself, or tap Retry.").param("id", r.get("id")).update();
                }
            } else if (!next.equals(r.get("status")) || "NEEDS_YOU".equals(next)) {
                jdbc.sql("UPDATE autopilot_run SET status = :s, message = :m, updated_at = now() WHERE id = :id")
                        .param("s", next).param("m", Objects.toString(r.get("dream_message"), "")).param("id", r.get("id")).update();
            }
        }
    }

    /** Title (no hashtags), a short description and 3-5 hashtags incl. #Shorts, from the Short's own plan. */
    PostKit postKit(UUID dreamId) {
        Map<String, Object> d = jdbc.sql("SELECT idea, title, look FROM dream_short WHERE id = :d").param("d", dreamId).query().singleRow();
        List<String> scenes = jdbc.sql("SELECT scene FROM dream_shot WHERE dream_short_id = :d ORDER BY ord").param("d", dreamId)
                .query(String.class).list();
        Instant since = Instant.now();
        PostKit kit = llm.call(null, "autopilot.post", """
                        You write the YouTube publishing kit for a Dreamscapes Short: a surreal, dreamlike vertical video with
                        music only. Return JSON {"title", "description", "hashtags"}. title: max 70 characters, evocative,
                        no hashtags, no clickbait, no emoji spam. description: 2-3 calm sentences that invite the viewer into
                        the place, then one line "Made with AI tools; music by the channel." hashtags: 3-5, each starting with
                        #, the first is #Shorts. Never claim it is real footage.""",
                "Idea: " + d.get("idea") + "\nWorking title: " + Objects.toString(d.get("title"), "") + "\nLook: " + d.get("look")
                        + "\nShots:\n- " + String.join("\n- ", scenes), PostKit.class);
        jdbc.sql("""
                        UPDATE cost_entry SET dream_short_id = :d
                        WHERE dream_short_id IS NULL AND video_id IS NULL AND note LIKE 'autopilot.post%' AND ts >= :since
                        """).param("d", dreamId).param("since", Timestamp.from(since)).update();
        return clean(kit);
    }

    /** Keeps the kit inside YouTube's limits (title 100, description 5000, no angle brackets) with #Shorts first. */
    static PostKit clean(PostKit kit) {
        if (kit == null || kit.title() == null || kit.title().isBlank()) {
            throw new IllegalStateException("the writer returned no title");
        }
        String title = kit.title().replaceAll("[<>]", "").replaceAll("#\\S+", "").strip();
        title = title.length() > 100 ? title.substring(0, 100).strip() : title;
        String desc = Objects.toString(kit.description(), "").replaceAll("[<>]", "").strip();
        List<String> tags = new ArrayList<>();
        tags.add("#Shorts");
        if (kit.hashtags() != null) {
            for (String h : kit.hashtags()) {
                String t = "#" + h.replaceAll("[^\\p{L}\\p{N}_]", "");
                if (t.length() > 1 && tags.stream().noneMatch(x -> x.equalsIgnoreCase(t)) && tags.size() < 5) {
                    tags.add(t);
                }
            }
        }
        desc = desc.length() > 4500 ? desc.substring(0, 4500) : desc;
        return new PostKit(title, desc, tags);
    }

    // ------------------------------------------------------------------------------------------- owner actions

    public List<Map<String, Object>> runs() {
        return jdbc.sql("""
                        SELECT r.*, d.final_asset_id, d.status AS dream_status, d.step AS dream_step,
                               COALESCE((SELECT sum(amount_inr) FROM cost_entry c WHERE c.dream_short_id = r.dream_short_id), 0) AS spent_inr
                        FROM autopilot_run r LEFT JOIN dream_short d ON d.id = r.dream_short_id
                        ORDER BY r.run_date DESC LIMIT 30
                        """).query().listOfRows();
    }

    /** What the screen shows at the top: on/off, next start, and why it is waiting (if it is). */
    public Map<String, Object> status() {
        Settings s = settings();
        ZonedDateTime now = ZonedDateTime.now(clock).withZoneSameInstant(zone);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("enabled", s.enabled());
        out.put("zone", zone.getId());
        out.put("now", now.toLocalDateTime().toString());
        List<String> held = jdbc.sql("SELECT run_date::text || ': ' || status FROM autopilot_run WHERE run_date < :d AND status IN (:st)")
                .param("d", now.toLocalDate()).param("st", BLOCKING).query(String.class).list();
        out.put("waitingOn", held);
        out.put("ideasLeft", jdbc.sql("SELECT count(*) FROM autopilot_idea WHERE used_at IS NULL").query(Long.class).single());
        out.put("youtubeConnected", youtubeConnected());
        return out;
    }

    /** The media worker keeps the owner's sign-in; the control plane only checks that the file exists (never reads it). */
    boolean youtubeConnected() {
        String path = System.getenv().getOrDefault("YOUTUBE_TOKEN_FILE", ".secrets/youtube-token.json");
        return java.nio.file.Files.exists(java.nio.file.Path.of(path))
                || java.nio.file.Files.exists(java.nio.file.Path.of("..").resolve(path));
    }

    /** The owner tapped Post: save their edits, then upload once. Refused unless the run is ready (no double posts). */
    public void post(UUID runId, String title, String description, String hashtags, String privacy) {
        if (!Set.of("private", "unlisted", "public").contains(privacy)) {
            throw new IllegalArgumentException("privacy must be private, unlisted or public");
        }
        if (title == null || title.isBlank()) {
            throw new IllegalArgumentException("A title is needed");
        }
        int claimed = jdbc.sql("""
                        UPDATE autopilot_run SET status = 'POSTING', title = :t, description = :desc, hashtags = :h, privacy = :p,
                               message = 'Uploading to YouTube…', updated_at = now()
                        WHERE id = :id AND status IN ('READY', 'POST_FAILED')
                        """).param("t", title.strip()).param("desc", Objects.toString(description, "").strip())
                .param("h", Objects.toString(hashtags, "").strip()).param("p", privacy).param("id", runId).update();
        if (claimed == 0) {
            throw new IllegalStateException("This Short is not ready to post (or is already posting)");
        }
        PostWorkflow wf = temporal.newWorkflowStub(PostWorkflow.class, WorkflowOptions.newBuilder()
                .setWorkflowId("post-" + runId + "-" + System.currentTimeMillis()).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::post, runId.toString());
    }

    /** A failed or stuck run the owner has dealt with: it stops holding up the next days. */
    public void dismiss(UUID runId) {
        jdbc.sql("""
                        UPDATE autopilot_run SET status = 'SKIPPED', message = 'Dismissed by the owner.', updated_at = now()
                        WHERE id = :id AND status IN ('WAITING_START', 'NEEDS_YOU', 'FAILED', 'RUNNING', 'READY', 'POST_FAILED')
                        """).param("id", runId).update();
    }

    /** Tries the publishing kit again for a finished Short whose kit failed. */
    public void retryKit(UUID runId) {
        jdbc.sql("UPDATE autopilot_run SET status = 'RUNNING', updated_at = now() WHERE id = :id AND status = 'NEEDS_YOU'")
                .param("id", runId).update();
        refreshRuns();
    }

    // ------------------------------------------------------------------------------------------- posting (activities)

    AutopilotTypes.YoutubeUploadRequest uploadRequest(UUID runId) {
        Map<String, Object> r = jdbc.sql("""
                        SELECT r.title, r.description, r.hashtags, r.privacy, a.uri_hot
                        FROM autopilot_run r JOIN dream_short d ON d.id = r.dream_short_id JOIN asset a ON a.id = d.final_asset_id
                        WHERE r.id = :id
                        """).param("id", runId).query().singleRow();
        List<String> tags = Objects.toString(r.get("hashtags"), "").isBlank() ? List.of()
                : List.of(r.get("hashtags").toString().trim().split("\\s+"));
        String desc = Objects.toString(r.get("description"), "") + (tags.isEmpty() ? "" : "\n\n" + String.join(" ", tags));
        List<String> plain = tags.stream().map(t -> t.replaceFirst("^#", "")).filter(t -> !t.isBlank())
                .map(t -> t.toLowerCase(Locale.ROOT)).toList();
        return new AutopilotTypes.YoutubeUploadRequest(runId.toString(), (String) r.get("uri_hot"), (String) r.get("title"),
                desc, plain, (String) r.get("privacy"));
    }

    String saveUpload(UUID runId, AutopilotTypes.YoutubeUploadResult result) {
        if (result.videoId() == null) {
            jdbc.sql("UPDATE autopilot_run SET status = 'POST_FAILED', message = :m, updated_at = now() WHERE id = :id")
                    .param("m", "Upload failed: " + result.error() + " Fix it and tap Post again.").param("id", runId).update();
            return "failed";
        }
        jdbc.sql("""
                        UPDATE autopilot_run SET status = 'POSTED', youtube_id = :y, privacy = :p, posted_at = now(),
                               message = :m, updated_at = now() WHERE id = :id
                        """).param("y", result.videoId()).param("p", result.privacy()).param("id", runId)
                .param("m", "Posted (" + result.privacy() + "): " + result.url()).update();
        // Rule 6: the upload is a free API call (YouTube quota, ~1600 units); logged at ₹0 against the Short.
        jdbc.sql("""
                        INSERT INTO cost_entry (id, video_id, dream_short_id, provider_id, amount_inr, units, unit_type, estimated, note)
                        SELECT :id, NULL, dream_short_id, 'youtube_api', 0, 1600, 'quota_unit', true, 'YouTube upload (videos.insert)'
                        FROM autopilot_run WHERE id = :run
                        """).param("id", UUID.randomUUID()).param("run", runId).update();
        return "posted";
    }
}
