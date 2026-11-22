package com.projectv.dream;

import com.projectv.agents.Llm;
import com.projectv.dream.DreamTypes.DreamWorld;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import com.projectv.routing.Router;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

import static com.projectv.dream.DreamLooks.look;

/**
 * The Scout step of a Dream Short (split out of DreamService, 22 Nov 2026): Gemini watches similar Shorts that already
 * exist, a text model turns what it saw into clichés and angles, and the writer is later told what not to copy. Remembers
 * watched Shorts in {@code scout_watch} so a repeat idea does not pay to watch them again. Runs inside DreamService's
 * transactions (DreamService keeps the {@code @Transactional} entry points and delegates here).
 */
class DreamScout {

    /** Shorts watched per idea, and the estimate held before it runs. */
    static final int SCOUT_VIDEOS = 6;
    static final BigDecimal SCOUT_ESTIMATE_INR = new BigDecimal("6"); // measured Rs 4.56 and Rs 5.96 (19 Nov)

    private final DreamService svc;
    private final JdbcClient jdbc;
    private final ObjectMapper json;
    private final Llm llm;
    private final Pricing pricing;
    private final BudgetGuard budget;
    private final Router router;
    private final DreamEvidence evidence;
    private final String listenModel;
    private final String briefModel;

    DreamScout(DreamService svc, JdbcClient jdbc, ObjectMapper json, Llm llm, Pricing pricing, BudgetGuard budget, Router router,
               DreamEvidence evidence, DreamSettings settings) {
        this.svc = svc;
        this.jdbc = jdbc;
        this.json = json;
        this.llm = llm;
        this.pricing = pricing;
        this.budget = budget;
        this.router = router;
        this.evidence = evidence;
        this.listenModel = settings.listenModel();
        this.briefModel = settings.briefModel();
    }

    /** What the Scout found and the angle the owner picked, as the writer will read it. */
    record Scouted(DreamTypes.ScoutBrief brief, List<DreamTypes.ScoutVideo> videos, int angle) {
        /** The angle the owner picked (1-based; out of range falls back to the nearest), or null when there are none. */
        DreamTypes.ScoutAngle chosen() {
            return brief.angles() == null || brief.angles().isEmpty() ? null
                    : brief.angles().get(Math.min(Math.max(angle, 1), brief.angles().size()) - 1);
        }

        String forWriter() {
            StringBuilder s = new StringBuilder("\n\nWhat already exists (make this Short clearly different):");
            if (brief.cliches() != null && !brief.cliches().isEmpty()) {
                s.append("\nAvoid these clichés: ").append(String.join("; ", brief.cliches()));
            }
            if (brief.angles() != null && !brief.angles().isEmpty()) {
                DreamTypes.ScoutAngle a = brief.angles().get(Math.min(Math.max(angle, 1), brief.angles().size()) - 1);
                // The owner's chosen angle shapes the WHOLE Short; where it conflicts with the look's usual structure
                // (e.g. Lonely object's "a different place every shot"), the angle wins (19 Nov test).
                s.append("\nThe owner chose this angle; build EVERY shot around it, as consecutive moments of it (it overrides ")
                        .append("the look's usual shot structure where they conflict): ").append(a.title()).append(" - ").append(a.idea());
            }
            List<String> seen = videos.stream().map(DreamTypes.ScoutVideo::shows).filter(Objects::nonNull).limit(8).toList();
            if (!seen.isEmpty()) {
                s.append("\nDo not copy any of these existing Shorts: ").append(String.join(" | ", seen));
            }
            return s.toString();
        }
    }

    Scouted scouted(Map<String, Object> d) {
        if (d.get("scout_json") == null) {
            return null;
        }
        Map<String, Object> m = json.readValue(d.get("scout_json").toString(), new tools.jackson.core.type.TypeReference<Map<String, Object>>() {
        });
        DreamTypes.ScoutBrief brief = json.convertValue(m.get("brief"), DreamTypes.ScoutBrief.class);
        List<DreamTypes.ScoutVideo> videos = json.convertValue(m.get("videos"),
                new tools.jackson.core.type.TypeReference<List<DreamTypes.ScoutVideo>>() {
                });
        return brief == null ? null : new Scouted(brief, videos == null ? List.of() : videos, ((Number) d.get("scout_angle")).intValue());
    }

    public boolean scoutWanted(UUID id) {
        return Boolean.TRUE.equals(svc.dream(id).get("scout"));
    }

    public DreamTypes.DreamScoutRequest scoutRequest(UUID id, String notes) {
        Map<String, Object> d = svc.dream(id);
        budget.require(SCOUT_ESTIMATE_INR, "Dream Short Scout (watch similar Shorts)");
        String query = Objects.toString(d.get("idea"), "") + (notes == null || notes.isBlank() ? "" : " " + notes.strip());
        return new DreamTypes.DreamScoutRequest(DreamService.storageId(id), query.strip(), listenModel, SCOUT_VIDEOS,
                look((String) d.get("look")).brief(), watchedShorts());
    }

    static final java.util.regex.Pattern YT_ID = java.util.regex.Pattern.compile(
            "(?:youtube\\.com/(?:shorts/|watch\\?v=)|youtu\\.be/)([A-Za-z0-9_-]{11})");

    static String videoKey(String url) {
        var m = YT_ID.matcher(Objects.toString(url, ""));
        return m.find() ? m.group(1) : null;
    }

    /** Shorts the Scout already watched (last 90 days, newest 200), so it doesn't pay to watch them again. */
    Map<String, DreamTypes.ScoutVideo> watchedShorts() {
        Map<String, DreamTypes.ScoutVideo> out = new java.util.LinkedHashMap<>();
        jdbc.sql("""
                        SELECT video_key, watched::text AS w FROM scout_watch
                        WHERE watched_at > now() - interval '90 days' ORDER BY watched_at DESC LIMIT 200
                        """).query().listOfRows()
                .forEach(r -> out.put((String) r.get("video_key"), json.readValue((String) r.get("w"), DreamTypes.ScoutVideo.class)));
        return out;
    }

    void rememberWatched(List<DreamTypes.ScoutVideo> videos, String model) {
        for (DreamTypes.ScoutVideo v : videos == null ? List.<DreamTypes.ScoutVideo>of() : videos) {
            String key = videoKey(v.url());
            boolean watched = v.shows() != null || "not about this idea".equals(v.error());
            if (key == null || !watched) {
                continue; // a failed watch (private, removed, timeout) is not remembered: next time it is tried again
            }
            jdbc.sql("""
                            INSERT INTO scout_watch (video_key, url, watched, model) VALUES (:k, :u, CAST(:w AS jsonb), :m)
                            ON CONFLICT (video_key) DO NOTHING
                            """).param("k", key).param("u", v.url()).param("w", json.writeValueAsString(v)).param("m", model).update();
        }
    }

        public String saveScout(UUID id, DreamTypes.DreamScoutRequest req, DreamTypes.DreamScoutResult r) {
        double paid = 0;
        if (r.usage() != null) {
            for (var u : r.usage()) {
                BigDecimal inr = pricing.textInr(u);
                paid += inr.doubleValue();
                svc.cost(id, BudgetGuard.GEMINI, inr, u.totalTokens(), "token", false, "Dream Short Scout: Gemini watched a Short");
            }
        }
        if (r.searches() > 0 && "google".equals(r.source())) {
            BigDecimal inr = pricing.groundingInr(r.searches());
            paid += inr.doubleValue();
            svc.cost(id, BudgetGuard.GEMINI, inr, r.searches(), "query", false, "Dream Short Scout: Google Search");
        }
        UUID decision = router.recordExternal(null, "DREAM_SCOUT", "video.watch", BudgetGuard.GEMINI,
                "found %d similar Shorts via %s; Gemini watches them from their links (only model that takes video)"
                        .formatted(r.videos() == null ? 0 : r.videos().size(), r.source()), List.of(Map.of("provider", BudgetGuard.GEMINI)));
        svc.tag("routing_decision", decision, id);
        rememberWatched(r.videos(), req == null ? null : req.model());
        List<DreamTypes.ScoutVideo> seen = r.videos() == null ? List.of()
                : r.videos().stream().filter(v -> v.error() == null && v.shows() != null).toList();
        if (seen.isEmpty()) {
            return noAngles(id, paid, "found no similar Shorts" + (r.error() == null ? "" : " (" + r.error() + ")"));
        }
        Map<String, Object> d = svc.dream(id);
        java.time.Instant since = java.time.Instant.now();
        String system = """
                You advise a private studio that makes short, wordless, music-led vertical videos (YouTube Shorts) that
                feel warm, nostalgic and real. Below are Shorts that already exist for the owner's idea, as a viewer saw
                them. Find what most of them do (the clichés to avoid), what none of them do (the gaps), and propose 3
                concrete angles that would make the owner's Short clearly different yet still feel-good and believable
                (a new object or place, a new camera idea, a new time of light, a twist at the end...). Never suggest
                copying any of them. Return JSON: {"cliches": [short phrases], "gaps": [short phrases],
                "angles": [{"title": 2-5 words, "idea": one or two concrete sentences, "why": why it stands out}]}.
                """;
        StringBuilder user = new StringBuilder("Owner's idea: " + d.get("idea") + "\nLook: " + look((String) d.get("look")).brief() + "\n");
        int n = 1;
        for (var v : seen) {
            user.append("\nShort ").append(n++).append(v.views() == null ? "" : " (" + v.views() + " views)").append(": ")
                    .append(v.shows()).append(" | look: ").append(v.look()).append(" | camera: ").append(v.camera())
                    .append(" | shots: ").append(v.shots()).append(" | music: ").append(v.music()).append(" | hook: ")
                    .append(v.hook()).append(" | why it works: ").append(v.works());
        }
        DreamTypes.ScoutBrief brief = llm.call(null, "dream.scout", system, user.toString(), DreamTypes.ScoutBrief.class, briefModel);
        svc.linkLlmCosts(id, "dream.scout", since);
        if (brief == null || brief.angles() == null || brief.angles().isEmpty()) {
            return noAngles(id, paid + svc.llmCostsSince(id, "dream.scout", since), "found Shorts but no useful angle");
        }
        jdbc.sql("UPDATE dream_short SET scout_json = CAST(:j AS jsonb), scout_angle = 1, updated_at = now() WHERE id = :d")
                .param("j", json.writeValueAsString(Map.of("videos", seen, "brief", brief, "source", r.source())))
                .param("d", id).update();
        evidence.costCheck(id, "SCOUT", SCOUT_ESTIMATE_INR.doubleValue(), paid + svc.llmCostsSince(id, "dream.scout", since));
        long top = seen.stream().map(DreamTypes.ScoutVideo::views).filter(Objects::nonNull).mapToLong(Long::longValue).max().orElse(0);
        return "Watched %d similar Shorts%s. Most do: %s. Angle 1: %s (pick another before approving).".formatted(seen.size(),
                top > 0 ? " (top one %,d views)".formatted(top) : "",
                brief.cliches() == null || brief.cliches().isEmpty() ? "-" : brief.cliches().getFirst(),
                brief.angles().getFirst().title());
    }

    /**
     * Nothing similar exists (owner, 19 Nov 2026): carry on from the idea and the look alone instead of stopping, so Auto
     * mode and the autopilot never wait on an unusual idea. The writer then uses the look's own shot structure.
     */
    private String noAngles(UUID id, double paid, String why) {
        evidence.costCheck(id, "SCOUT", SCOUT_ESTIMATE_INR.doubleValue(), paid);
        evidence.event(id, "SCOUT", "scout", null, "Scout " + why + "; carrying on from the idea alone", null);
        return "Scout " + why + ", so the writer works from your idea and the look alone (no angle).";
    }

    /** Our shots too close to an existing Short? Returns rewrite notes, or null. Cheap Gemini text call. */
    String tooSimilar(UUID id, DreamWorld w, Scouted scouted) {
        List<String> theirs = scouted.videos().stream().map(DreamTypes.ScoutVideo::shows).filter(Objects::nonNull).toList();
        if (theirs.isEmpty()) {
            return null;
        }
        StringBuilder user = new StringBuilder("Our shots:");
        for (int i = 0; i < w.shots().size(); i++) {
            user.append("\n").append(i + 1).append(". ").append(w.shots().get(i).scene());
        }
        user.append("\n\nExisting Shorts:");
        for (int i = 0; i < theirs.size(); i++) {
            user.append("\n").append((char) ('A' + i)).append(". ").append(theirs.get(i));
        }
        DreamTypes.Similar s = llm.call(null, "dream.similar", """
                Compare our planned shots with Shorts that already exist. A shot is too similar only when it is nearly the
                same scene (same object in the same kind of place, seen the same way). Sharing a mood or a colour is fine.
                Return JSON {"similar": [{"shot": our shot number, "like": the letter it copies, "why": a few words}]};
                an empty list when nothing is too close.
                """, user.toString(), DreamTypes.Similar.class);
        if (s == null || s.similar() == null || s.similar().isEmpty()) {
            return null;
        }
        String notes = s.similar().stream().map(x -> "shot %d is too close to an existing Short (%s): make it a different scene"
                .formatted(x.shot(), x.why())).collect(java.util.stream.Collectors.joining("; "));
        evidence.event(id, "WRITE", "similar", null, notes, null);
        return notes;
    }
}
