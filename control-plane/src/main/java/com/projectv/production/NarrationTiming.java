package com.projectv.production;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Places narration on the timeline from the REAL voice lengths (owner report 16 Nov 2026: the last sentence of a
 * Short was cut off at 60 s and lines drifted up to 1.7 s late, because lines were chained one after another and the
 * video was trimmed to a fixed length). Pure function, unit-tested.
 *
 * <ul>
 *   <li>every line gets a fixed start time; shots are then cut to those times (no cumulative drift)</li>
 *   <li>short breathing gaps between spoken lines; visual-only beats become music-only moments</li>
 *   <li>the video is never shorter than the speech; a Short (max 60 s) may speed the voice up by at most 8 %</li>
 * </ul>
 */
public final class NarrationTiming {

    public static final double LEAD = 0.4;          // picture before the first word
    public static final double GAP = 0.5;           // breath between spoken lines
    public static final double VISUAL_BEAT = 2.5;   // music-only moment for a visual-only script line
    public static final double TAIL = 1.5;          // picture after the last word
    public static final double SHORT_MAX = 60.0;
    public static final double MAX_TEMPO = 1.08;

    public record Line(int index, boolean spoken, double durationSec) {
    }

    /**
     * @param starts   script line index -> time the line starts (spoken: first word; visual: start of its moment)
     * @param tempo    voice speed factor to apply (1.0 = unchanged)
     * @param speechEnd when the last word ends
     * @param totalSec video length (≥ target, ≥ speech + tail)
     * @param warning  null, or a plain-language note for the owner
     */
    public record Result(Map<Integer, Double> starts, double tempo, double speechEnd, double totalSec, String warning) {
    }

    private NarrationTiming() {
    }

    /** Breath lengths a Short may fall back to (before speeding the voice up any further). */
    private static final double[] SHORT_GAPS = {GAP, 0.35, 0.25};

    public static Result plan(List<Line> lines, double targetSec, boolean isShort) {
        double tempo = 1.0;
        Layout layout = layout(lines, tempo, GAP);
        String warning = null;
        if (isShort && layout.speechEnd + TAIL > SHORT_MAX) {
            // Smallest change first: shorter breaths at each speed, then 1 % faster, up to 8 %.
            search:
            for (int step = 0; step <= Math.round((MAX_TEMPO - 1) * 100); step++) {
                tempo = 1.0 + step / 100.0;
                for (double gap : SHORT_GAPS) {
                    layout = layout(lines, tempo, gap);
                    if (layout.speechEnd + TAIL <= SHORT_MAX) {
                        break search;
                    }
                }
            }
            if (layout.speechEnd + TAIL > SHORT_MAX) {
                warning = "The narration is %.0fs even at %d%% speed, too long for a Short (60 s). Shorten some lines (Edit) or redo the Script."
                        .formatted(layout.speechEnd + TAIL, Math.round((tempo - 1) * 100));
            } else if (tempo > 1.0) {
                warning = "Voice sped up %d%% so the Short fits in 60 s.".formatted(Math.round((tempo - 1) * 100));
            }
        }
        double total = Math.max(targetSec, layout.speechEnd + TAIL);
        if (isShort && warning == null) {
            total = Math.min(total, SHORT_MAX);
        }
        if (total > targetSec + 0.5 && warning == null) {
            warning = "Video lengthened from %.0fs to %.0fs so no words are cut. Redo Music if the score ends early."
                    .formatted(targetSec, total);
        }
        return new Result(layout.starts, tempo, layout.speechEnd, Math.round(total * 1000) / 1000.0, warning);
    }

    private record Layout(Map<Integer, Double> starts, double speechEnd) {
    }

    private static Layout layout(List<Line> lines, double tempo, double gap) {
        Map<Integer, Double> starts = new LinkedHashMap<>();
        double t = LEAD;
        double speechEnd = 0;
        boolean previousSpoken = false;
        for (Line l : lines) {
            if (l.spoken() && l.durationSec() > 0) {
                if (previousSpoken) {
                    t += gap;
                }
                starts.put(l.index(), round(t));
                t += l.durationSec() / tempo;
                speechEnd = t;
                previousSpoken = true;
            } else {
                starts.put(l.index(), round(t));
                t += VISUAL_BEAT;
                previousSpoken = false;
            }
        }
        return new Layout(starts, round(speechEnd));
    }

    private static double round(double v) {
        return Math.round(v * 1000) / 1000.0;
    }
}
