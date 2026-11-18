package com.projectv.production;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MusicEnergyCurveTest {

    @Test
    void stretchesShotsToTheVideoAndRanksBigFastShotsHigher() {
        List<Map<String, Object>> shots = List.of(
                Map.of("dur", 1.5, "move", "flythrough", "type", "extreme_wide"),  // fast, big: high energy
                Map.of("dur", 6.0, "move", "static", "type", "close_up"));       // slow, still: calm
        List<Double> curve = MusicStage.energyCurve(shots, 15);
        assertEquals(15, curve.size());
        assertTrue(curve.get(0) > 0.8, "first shot is high energy: " + curve);
        assertTrue(curve.get(14) < 0.2, "last shot is calm: " + curve);
    }

    @Test
    void noShotsMeansNoPlan() {
        assertEquals(List.of(), MusicStage.energyCurve(List.of(), 60));
    }
}
