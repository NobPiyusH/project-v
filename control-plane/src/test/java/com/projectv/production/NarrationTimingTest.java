package com.projectv.production;

import com.projectv.production.NarrationTiming.Line;
import com.projectv.production.NarrationTiming.Result;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.TreeMap;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class NarrationTimingTest {

    /** The real "Why is the night sky dark?" Short that cut off its last sentence (owner report, 16 Nov 2026). */
    private static final double[] NIGHT_SKY = {5.3, 10.4, 8.8, 9.0, 12.1, 9.4, 5.9};

    private static List<Line> spoken(double... durations) {
        List<Line> lines = new ArrayList<>();
        for (int i = 0; i < durations.length; i++) {
            lines.add(new Line(i, true, durations[i]));
        }
        return lines;
    }

    @Test
    void nightSkyShortFitsIn60sWithoutCuttingWords() {
        Result r = NarrationTiming.plan(spoken(NIGHT_SKY), 60, true);
        assertTrue(r.tempo() > 1.0 && r.tempo() <= NarrationTiming.MAX_TEMPO, "voice sped up a little, never more than 8%");
        assertTrue(r.speechEnd() + NarrationTiming.TAIL <= 60.0 + 1e-6, "last word ends before the Short ends");
        assertEquals(60.0, r.totalSec(), 1e-9);
        assertNotNull(r.warning());
    }

    @Test
    void linesNeverOverlapAndBreathe() {
        Result r = NarrationTiming.plan(spoken(4, 6, 5), 30, false);
        double prevEnd = -1;
        double[] d = {4, 6, 5};
        for (int i = 0; i < 3; i++) {
            double start = r.starts().get(i);
            if (prevEnd >= 0) {
                assertEquals(prevEnd + NarrationTiming.GAP, start, 1e-6, "a breath between lines, no drift");
            }
            prevEnd = start + d[i];
        }
        assertEquals(1.0, r.tempo());
        assertNull(r.warning());
        assertEquals(30.0, r.totalSec(), 1e-9, "target kept when the speech fits");
    }

    @Test
    void longVideoIsLengthenedRatherThanCuttingSpeech() {
        Result r = NarrationTiming.plan(spoken(30, 30, 30), 60, false);
        assertTrue(r.totalSec() >= r.speechEnd() + NarrationTiming.TAIL - 1e-6);
        assertEquals(1.0, r.tempo(), "long-form never speeds the voice up");
        assertTrue(r.warning().contains("lengthened"));
    }

    @Test
    void tooLongForAShortSaysSoInsteadOfCutting() {
        Result r = NarrationTiming.plan(spoken(40, 40), 60, true);
        assertEquals(NarrationTiming.MAX_TEMPO, r.tempo(), 1e-9);
        assertTrue(r.totalSec() > 60, "words are never cut, even past 60 s");
        assertTrue(r.warning().contains("too long for a Short"));
    }

    @Test
    void visualBeatsBecomeMusicOnlyMoments() {
        List<Line> lines = List.of(new Line(0, true, 5), new Line(1, false, 0), new Line(2, true, 5));
        Result r = NarrationTiming.plan(lines, 30, false);
        assertEquals(r.starts().get(1) + NarrationTiming.VISUAL_BEAT, r.starts().get(2), 1e-6);
    }

    @Test
    void anchoredShotsStartExactlyWhenTheirLineDoes() {
        List<Double> rough = List.of(4.0, 4.0, 4.0, 4.0, 4.0, 4.0);
        TreeMap<Integer, Double> anchors = new TreeMap<>();
        anchors.put(2, 9.3);
        anchors.put(4, 17.1);
        List<double[]> out = ShotTiming.fitAnchored(rough, anchors, 25, List.of(), 1.2);
        assertEquals(9.3, out.get(2)[0], 1e-9);
        assertEquals(17.1, out.get(4)[0], 1e-9);
        assertEquals(25.0, out.stream().mapToDouble(s -> s[1]).sum(), 1e-9);
        // An anchor going backwards in time is ignored rather than breaking the timeline.
        anchors.put(5, 3.0);
        List<double[]> safe = ShotTiming.fitAnchored(rough, anchors, 25, List.of(), 1.2);
        for (double[] s : safe) {
            assertTrue(s[1] > 0);
        }
    }
}
