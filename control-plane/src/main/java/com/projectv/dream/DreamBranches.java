package com.projectv.dream;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * "Choose the path" (owner's growth pick, 20 Nov 2026): a Short ends on a two-way choice, and the chosen path becomes the
 * next Short in the same world. Pure helpers (prompt text, option clean-up, branch memory); the database work is in
 * {@link DreamService} and {@link DreamStudio}.
 *
 * <p>Who picks is a {@link PathPick}: today the owner picks A or B by hand ({@code source = "owner"}); the YouTube comment
 * vote (step 3, later) only has to produce a {@code PathPick} with {@code source = "comments"} and call
 * {@link DreamStudio#continueDream(java.util.UUID, PathPick)}. Nothing else changes.
 */
public final class DreamBranches {

    private DreamBranches() {
    }

    public static final int MAX_WORDS = 4;
    public static final String OWNER = "owner";
    public static final String COMMENTS = "comments";

    /** Asked of the writer only when the Short has the path choice on (setting off = the prompt is unchanged). */
    static final String PATH_FIELD = ", \"paths\": exactly TWO short path options (2 to 4 words each, lower case, concrete "
            + "places or things the camera could go next from the LAST shot, e.g. [\"the door\", \"the stairs\"]); they must "
            + "both be visible or clearly suggested in the last shot, be clearly different, and make a viewer want to pick one";

    /**
     * Who picked which path. {@code path}: "A" or "B"; {@code source}: {@link #OWNER} (manual, now) or {@link #COMMENTS}
     * (the YouTube vote, later); {@code votes}: optional vote counts for the record ({@code {"A": 12, "B": 30}}).
     */
    public record PathPick(String path, String source, Map<String, Integer> votes) {
        public PathPick {
            path = path == null ? "" : path.strip().toUpperCase();
            if (!path.equals("A") && !path.equals("B")) {
                throw new IllegalArgumentException("Pick path A or B");
            }
            source = source == null || source.isBlank() ? OWNER : source.strip();
        }

        public static PathPick owner(String path) {
            return new PathPick(path, OWNER, null);
        }
    }

    /** One path option cleaned for the end card: quotes and arrows gone, at most 4 words; null when empty. */
    static String cleanPath(String p) {
        if (p == null) {
            return null;
        }
        String s = p.replaceAll("[\"*{}\\\\→←↑↓<>]", " ").replaceAll("\\s+", " ").strip();
        if (s.isEmpty()) {
            return null;
        }
        String[] words = s.split(" ");
        return words.length <= MAX_WORDS ? s : String.join(" ", java.util.Arrays.copyOf(words, MAX_WORDS));
    }

    /** The writer's two options, cleaned; null unless there are two different ones. */
    static List<String> cleanPaths(List<String> paths) {
        if (paths == null) {
            return null;
        }
        List<String> out = new ArrayList<>();
        for (String p : paths) {
            String c = cleanPath(p);
            if (c != null && out.stream().noneMatch(c::equalsIgnoreCase)) {
                out.add(c);
            }
        }
        return out.size() >= 2 ? out.subList(0, 2) : null;
    }

    /** The path text for "A"/"B" from a Short's row. */
    static String pathOf(Map<String, Object> d, String ab) {
        return (String) d.get("A".equals(ab) ? "path_a" : "path_b");
    }

    /**
     * What the writer reads for a continuation (user message): the world the parent built, where it ended, and the path
     * the viewers (or the owner) chose. Shot 1 starts on the parent's last frame, so it must begin exactly there.
     */
    static String forWriter(Map<String, Object> branch, String chosenPath, int episode) {
        StringBuilder s = new StringBuilder("\n\nThis Short is EPISODE ").append(episode)
                .append(" of a series: it continues the previous episode in the SAME world (same materials, palette, light, ")
                .append("scale and style; keep the recurring hero object exactly as described).");
        append(s, "Previous title", branch.get("title"));
        append(s, "World", branch.get("world"));
        append(s, "Palette", branch.get("palette"));
        append(s, "Light", branch.get("light"));
        append(s, "Hero", branch.get("hero"));
        append(s, "The previous episode ended on", branch.get("lastScene"));
        s.append("\nThe audience chose the path: ").append(Objects.toString(chosenPath, "").strip().toUpperCase())
                .append(". Shot 1 begins EXACTLY on the previous episode's last frame (described above) and the camera ")
                .append("moves into that path; the rest of the Short explores where that path leads. Never recap the ")
                .append("previous episode; never jump to an unrelated place.");
        return s.toString();
    }

    private static void append(StringBuilder s, String label, Object v) {
        if (v != null && !v.toString().isBlank()) {
            s.append('\n').append(label).append(": ").append(v.toString().strip());
        }
    }

    /** The idea text of a continuation (shown in the list; the writer reads the full branch memory). */
    static String childIdea(Object parentTitle, String chosenPath, int episode) {
        return "Episode %d: %s - the path chosen: %s".formatted(episode, Objects.toString(parentTitle, "the dream").strip(),
                chosenPath);
    }
}
