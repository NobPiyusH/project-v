package com.projectv.dream;

import com.projectv.dream.DreamTypes.PlannedScene;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * SVI scenes (21 Nov 2026, off by default): pure rules shared by the writer, pictures, clips, estimate and finish.
 * A Short holds 1-3 scenes; a scene is one continuous SVI 2.0 Pro shot of 10/15/20 s made of 5 s beats (81 frames at
 * 16 fps). Beat k > 1 re-draws the last 12 frames of beat k-1 (cross-faded on the laptop), so a scene of n beats has
 * 81 + 68 (n - 1) frames before it is cut to length: 10 s = 3 beats, 15 s = 4, 20 s = 5. One picture per scene (beat 1).
 * A single long shot is simply one scene. Every beat is a dream_shot row tagged with scene_no / beat_no.
 */
final class DreamSvi {

    static final int MAX_SCENES = 3;
    static final int MAX_BEATS = 5;           // 20 s: content drifts after that (bench)
    static final int BEAT_FRAMES = 81;
    static final int NEW_FRAMES = 68;         // a later beat adds 81 - 1 (anchor) - 12 (overlap) frames
    static final int FPS = 16;
    /** First scene job on a pod: KJNodes + 2 x 1.23 GB LoRAs + a ComfyUI restart (estimate only, measured ~2-3 min). */
    static final double SETUP_SECONDS = 180;
    static final List<Integer> LENGTHS = List.of(10, 15, 20);

    private DreamSvi() {
    }

    /** Whether this Short uses SVI scenes. */
    static boolean on(Map<String, Object> d) {
        return d.get("svi_scenes") != null && ((Number) d.get("svi_scenes")).intValue() > 0;
    }

    static int scenes(Map<String, Object> d) {
        return Math.max(1, Math.min(MAX_SCENES, ((Number) d.get("svi_scenes")).intValue()));
    }

    static int seconds(Map<String, Object> d) {
        return ((Number) d.get("svi_scene_seconds")).intValue();
    }

    /** Clamp the form's scene length to 10/15/20 s (anything else: 20). */
    static int cleanSeconds(Integer s) {
        return s != null && LENGTHS.contains(s) ? s : 20;
    }

    /** Beats needed for a scene of {@code seconds} (10 s: 3, 15 s: 4, 20 s: 5). */
    static int beatsFor(int seconds) {
        int want = seconds * FPS + 1;
        int n = 1;
        while (framesOf(n) < want) {
            n++;
        }
        return Math.min(MAX_BEATS, n);
    }

    /** Frames a scene of {@code beats} beats has before it is cut to length. */
    static int framesOf(int beats) {
        return beats <= 0 ? 0 : BEAT_FRAMES + (beats - 1) * NEW_FRAMES;
    }

    /** "cut" (default) or "dissolve" between scenes. */
    static String cleanBetween(String b) {
        return "dissolve".equalsIgnoreCase(b) ? "dissolve" : "cut";
    }

    /** The writer's structure for SVI scenes (replaces the look's own shot structure; the look still sets the style). */
    static String structure(int scenes, int seconds, int beats) {
        return ("Invent %d scene%s in the same world%s. Each scene is ONE continuous, unbroken camera shot of %d seconds told in "
                + "%d beats of about 5 seconds: the camera never cuts inside a scene and keeps moving smoothly from one beat into "
                + "the next, each beat continuing exactly where the one before left off. For each scene give \"scene\": what the "
                + "camera sees at the START of the scene (one or two concrete sentences; it becomes the scene's only picture) and "
                + "\"beats\": exactly %d motion lines, one per beat, each saying how the camera keeps moving and what slowly "
                + "changes in view. %s")
                .formatted(scenes, scenes == 1 ? "" : "s", scenes > 1 ? " that tell one small story (a beginning, a turn and an end)" : "",
                        seconds, beats, beats,
                        scenes > 1 ? "Between scenes there is a cut to a new view of the same world (another place or moment)." : "");
    }

    /** The JSON the writer returns for SVI scenes, in place of the usual "shots" list. */
    static String jsonFields(int scenes, int beats) {
        return "\"style\": ONE line of visual style (lens, colour, light, texture) shared by every scene, \"scenes\": [{\"scene\": "
                + "the opening view, \"beats\": [%d motion lines]}] with exactly %d scenes".formatted(beats, scenes);
    }

    /** One planned beat, flattened for the dream_shot table. */
    record Beat(int scene, int beat, String sceneText, String motion) {
    }

    /** The writer's scenes as beats, cut or padded to the Short's shape (a missing beat repeats the scene's last motion). */
    static List<Beat> flatten(List<PlannedScene> planned, int scenes, int beats) {
        List<Beat> out = new ArrayList<>();
        if (planned == null) {
            return out;
        }
        for (int s = 0; s < Math.min(scenes, planned.size()); s++) {
            PlannedScene p = planned.get(s);
            if (p == null || p.scene() == null || p.scene().isBlank()) {
                continue;
            }
            List<String> lines = p.beats() == null ? List.of() : p.beats().stream().filter(Objects::nonNull)
                    .map(String::strip).filter(l -> !l.isEmpty()).toList();
            String last = lines.isEmpty() ? "the camera keeps gliding slowly forward" : lines.getLast();
            int sceneNo = out.isEmpty() ? 1 : out.getLast().scene() + 1;
            for (int b = 0; b < beats; b++) {
                out.add(new Beat(sceneNo, b + 1, p.scene().strip(), b < lines.size() ? lines.get(b) : last));
            }
        }
        return out;
    }

    /**
     * Unfiltered SVI: a blank line separates scenes and each line is a beat, the words used exactly as written. At most
     * 3 scenes of 5 beats; anything over that is dropped.
     */
    static List<Beat> unfiltered(String prompt) {
        List<Beat> out = new ArrayList<>();
        int scene = 0;
        int beat = 0;
        boolean gap = true;
        for (String raw : Objects.toString(prompt, "").split("\\R", -1)) {
            String line = raw.strip();
            if (line.isEmpty()) {
                gap = true;
                continue;
            }
            if (gap) {
                scene++;
                beat = 0;
                gap = false;
            }
            if (scene > MAX_SCENES) {
                break;
            }
            if (++beat <= MAX_BEATS) {
                out.add(new Beat(scene, beat, line, line));
            }
        }
        return out;
    }

    /** Beats per scene, in order, from the shot rows (ordered by ord). */
    static List<Integer> beatsPerScene(List<Map<String, Object>> shots) {
        List<Integer> out = new ArrayList<>();
        int scene = -1;
        for (Map<String, Object> s : shots) {
            int n = ((Number) s.get("scene_no")).intValue();
            if (n != scene) {
                out.add(0);
                scene = n;
            }
            out.set(out.size() - 1, out.getLast() + 1);
        }
        return out;
    }

    static int sceneOf(Map<String, Object> shot) {
        return shot.get("scene_no") == null ? 0 : ((Number) shot.get("scene_no")).intValue();
    }

    static int beatOf(Map<String, Object> shot) {
        return shot.get("beat_no") == null ? 0 : ((Number) shot.get("beat_no")).intValue();
    }
}
