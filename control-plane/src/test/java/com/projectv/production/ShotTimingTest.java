package com.projectv.production;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ShotTimingTest {

    private static List<Double> beatGrid(double bpm, double total) {
        List<Double> beats = new ArrayList<>();
        for (double t = 0; t <= total; t += 60.0 / bpm) {
            beats.add(Math.round(t * 1000) / 1000.0);
        }
        return beats;
    }

    @Test
    void fitsExactlyToTotalAndSnapsCutsToBeats() {
        List<double[]> out = ShotTiming.fit(List.of(5.0, 7.3, 6.1, 4.4), 30, beatGrid(120, 30), 2.5);
        double sum = out.stream().mapToDouble(s -> s[1]).sum();
        assertEquals(30.0, sum, 1e-9);
        assertEquals(0.0, out.getFirst()[0]);
        for (int i = 1; i < out.size(); i++) {
            double cut = out.get(i)[0];
            assertEquals(0.0, Math.abs(cut * 2 - Math.round(cut * 2)), 1e-6, "cut " + cut + " should be on a 0.5 s beat");
            assertTrue(out.get(i)[1] >= 2.5);
        }
    }

    @Test
    void neverMakesAShotShorterThanTheMinimumAndWorksWithoutBeats() {
        List<double[]> out = ShotTiming.fit(List.of(1.0, 1.0, 20.0), 22, List.of(), 2.5);
        assertEquals(3, out.size());
        assertEquals(22.0, out.stream().mapToDouble(s -> s[1]).sum(), 1e-9);
        List<double[]> fallback = ShotTiming.fit(java.util.Arrays.asList(null, -3.0), 10, null, 1.0);
        assertEquals(5.0, fallback.getFirst()[1], 1e-9);
    }
}
