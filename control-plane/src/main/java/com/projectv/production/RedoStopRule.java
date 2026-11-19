package com.projectv.production;

import com.projectv.media.MediaTypes.ProducerItemVerdict;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;

import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * The AI Producer's stop rule (optimisation review item 4, owner approved 19 Nov 2026). Re-rolling the same prompt with
 * a new seed does not fix a prompt problem: the autopilot Short paid four GPU rounds for "visible human figures". So
 * when a redo review names the same issue as the review before it on that step:
 * <ul>
 *   <li>a known recurring cause (people, text, morphing, a still image) gets ONE firm prompt / negative-prompt fix;</li>
 *   <li>after that, or for any other repeated issue, the Producer asks the owner instead of re-rolling.</li>
 * </ul>
 * The round and ₹ limits still apply on top. Workflow code is untouched: the rule only changes the advice the
 * (recorded) save-review activity returns, so running workflows replay unchanged.
 */
public final class RedoStopRule {

    /** A cause that keeps coming back because of the prompt, and its fix. negative = extra Wan negative-prompt terms. */
    public record Cause(String key, String label, Pattern match, String promptFix, String negative) {
    }

    public static final List<Cause> CAUSES = List.of(
            new Cause("people", "visible people",
                    Pattern.compile("\\b(human|humans|person|persons|people|figure|figures|silhouette|silhouettes|man|men|"
                            + "woman|women|child|children|crowd|face|faces|body|bodies|hand|hands)\\b"),
                    "The scene is completely empty of people: no humans, no figures, no silhouettes, no faces, no hands, "
                            + "no reflections of people.",
                    "people, person, human figure, silhouette, face, hands, crowd, man, woman, child"),
            new Cause("text", "text or letters",
                    Pattern.compile("\\b(text|letters?|words?|caption|captions|watermark|logo|signage|writing)\\b"),
                    "No text anywhere: no letters, words, signs, numbers, logos or watermarks.",
                    "text, letters, words, signage, numbers, logo, watermark"),
            new Cause("morph", "morphing or deformed objects",
                    Pattern.compile("\\b(morph\\w*|warp\\w*|melt\\w*|deform\\w*|distort\\w*)\\b"),
                    "Every object keeps a solid, stable, physically real shape for the whole shot.",
                    "morphing, melting, warping, deformed, distorted, shape-shifting"),
            new Cause("still", "too little motion",
                    Pattern.compile("\\b(static|still|slideshow|frozen|near-still|barely moves?|no motion|no movement)\\b"),
                    "The camera moves clearly and continuously through the whole shot, like a real handheld or drone move.",
                    "static, still image, frozen, slideshow, no camera movement"));

    public enum Action { NONE, PROMPT_FIX, ASK }

    /** What the rule decided. verdict and redoNotes are what the Producer's advice should carry. */
    public record Outcome(Action action, Cause cause, String verdict, String redoNotes, String note) {
    }

    private RedoStopRule() {
    }

    /** The issues a review names: a cause key when one matches, otherwise the item's problem as normalised words. */
    static Set<String> issues(List<ProducerItemVerdict> items, String summary) {
        Set<String> out = new LinkedHashSet<>();
        for (ProducerItemVerdict i : items == null ? List.<ProducerItemVerdict>of() : items) {
            if (i.ok()) {
                continue;
            }
            String problem = Objects.toString(i.problem(), "").toLowerCase(Locale.ROOT).strip();
            Cause c = cause(problem);
            if (c != null) {
                out.add(c.key());
            } else if (!problem.isBlank()) {
                out.add("~" + problem.replaceAll("[^a-z0-9 ]", " ").replaceAll("\\s+", " ").strip());
            }
        }
        if (out.isEmpty()) {
            Cause c = cause(Objects.toString(summary, "").toLowerCase(Locale.ROOT));
            if (c != null) {
                out.add(c.key());
            }
        }
        return out;
    }

    static Cause cause(String text) {
        return CAUSES.stream().filter(c -> c.match().matcher(text).find()).findFirst().orElse(null);
    }

    public static Cause causeByKey(String key) {
        return CAUSES.stream().filter(c -> c.key().equals(key)).findFirst().orElse(null);
    }

    /** The issues both reviews name: shared cause keys, or free-text problems sharing at least half their words. */
    static Set<String> repeated(Set<String> now, Set<String> before) {
        Set<String> out = new LinkedHashSet<>();
        for (String a : now) {
            for (String b : before) {
                if (a.equals(b) || (a.startsWith("~") && b.startsWith("~") && similar(a, b))) {
                    out.add(a);
                }
            }
        }
        return out;
    }

    private static boolean similar(String a, String b) {
        Set<String> x = words(a);
        Set<String> y = words(b);
        if (x.isEmpty() || y.isEmpty()) {
            return false;
        }
        Set<String> both = new HashSet<>(x);
        both.retainAll(y);
        Set<String> any = new HashSet<>(x);
        any.addAll(y);
        return both.size() * 2 >= any.size();
    }

    private static Set<String> words(String s) {
        return Arrays.stream(s.substring(1).split(" ")).filter(w -> w.length() > 2).collect(Collectors.toSet());
    }

    /**
     * Pure rule. previous = the review before this one on the same step (null = none); fixTried = a prompt fix was
     * already tried on this step.
     */
    public static Outcome decide(String verdict, List<ProducerItemVerdict> items, String summary, String redoNotes,
                                 List<ProducerItemVerdict> previousItems, String previousSummary, boolean hasPrevious,
                                 boolean fixTried) {
        if (!"redo".equals(verdict) || !hasPrevious) {
            return new Outcome(Action.NONE, null, verdict, redoNotes, null);
        }
        Set<String> same = repeated(issues(items, summary), issues(previousItems, previousSummary));
        if (same.isEmpty()) {
            return new Outcome(Action.NONE, null, verdict, redoNotes, null);
        }
        Cause known = same.stream().map(RedoStopRule::causeByKey).filter(Objects::nonNull).findFirst().orElse(null);
        if (known != null && !fixTried) {
            String notes = "RECURRING ISSUE (" + known.label() + "), firm fix: " + known.promptFix()
                    + (redoNotes == null || redoNotes.isBlank() ? "" : " " + redoNotes.strip());
            return new Outcome(Action.PROMPT_FIX, known, "redo", notes,
                    "Same issue twice in a row (" + known.label() + "): trying one firm prompt fix before asking you.");
        }
        String what = known != null ? known.label() : same.iterator().next().substring(1);
        return new Outcome(Action.ASK, known, "ask", redoNotes,
                "Same issue twice in a row (" + what + ")" + (fixTried ? " even after a prompt fix" : "")
                        + ": stopped re-rolling, please decide.");
    }

    /**
     * The rule against the database: reads the previous review of this step for this video or Short. ownerColumn is
     * {@code video_id} or {@code dream_short_id} (a constant, never user input).
     */
    public static Outcome check(JdbcClient jdbc, ObjectMapper json, String ownerColumn, UUID ownerId, String stage,
                                String verdict, List<ProducerItemVerdict> items, String summary, String redoNotes) {
        if (!"redo".equals(verdict)) {
            return new Outcome(Action.NONE, null, verdict, redoNotes, null);
        }
        if (!ownerColumn.equals("video_id") && !ownerColumn.equals("dream_short_id")) {
            throw new IllegalArgumentException(ownerColumn);
        }
        Map<String, Object> prev = jdbc.sql("SELECT items_json::text AS items, summary FROM producer_decision WHERE "
                        + ownerColumn + " = :o AND stage = :s ORDER BY created_at DESC LIMIT 1")
                .param("o", ownerId).param("s", stage).query().listOfRows().stream().findFirst().orElse(null);
        boolean fixTried = jdbc.sql("SELECT count(*) FROM producer_decision WHERE " + ownerColumn
                        + " = :o AND stage = :s AND stop_rule = 'prompt_fix'")
                .param("o", ownerId).param("s", stage).query(Long.class).single() > 0;
        List<ProducerItemVerdict> prevItems = List.of();
        if (prev != null && prev.get("items") != null) {
            prevItems = json.readValue((String) prev.get("items"), new TypeReference<List<ProducerItemVerdict>>() {
            });
        }
        return decide(verdict, items, summary, redoNotes, prevItems, prev == null ? null : (String) prev.get("summary"),
                prev != null, fixTried);
    }
}
