package com.projectv.dream;

import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

class RentalEstimateTest {

    final RentalEstimate e = RentalEstimate.DEFAULT; // 900 s start, FLUX 3 s, Z-Image 5 s, 76 s per clip, SVI setup 180 s

    @Test
    void oneSviSceneOfFourClipsIsAboutAThirdOfAnHour() {
        double h = e.hours(Map.of("shot_count", 4, "picture_model", "flux2-klein", "svi_scenes", 1));
        assertEquals((900 + 3 + 4 * 76 + 180) / 3600.0, h, 1e-9);
        assertEquals(0.35, h, 0.05, "not the old fixed 0.75 h");
    }

    @Test
    void sixShotWanShortWithZImage() {
        double h = e.hours(Map.of("shot_count", 6, "picture_model", "zimage", "svi_scenes", 0));
        assertEquals((900 + 6 * 5 + 6 * 76) / 3600.0, h, 1e-9); // no SVI setup
    }

    @Test
    void unknownWorkFallsBackToTheConfiguredHours() {
        assertEquals(0.75, e.hours(null));
        assertEquals(0.75, e.hours(Map.of("picture_model", "flux2-klein")), "no shot count");
        Map<String, Object> m = new HashMap<>(Map.of("shot_count", 4));
        m.put("picture_model", null);
        assertEquals(0.75, e.hours(m), "unknown picture model");
        assertEquals(0.75, e.hours(Map.of("shot_count", 0, "picture_model", "zimage")));
        assertEquals(0.75, e.hours(4, "sdxl", 4, false));
    }

    @Test
    void everyNumberComesFromConfig() {
        RentalEstimate slow = new RentalEstimate(600, 10, 20, 100, 0, 1.0);
        assertEquals((600 + 2 * 10 + 2 * 100) / 3600.0, slow.hours(2, "flux2-klein", 2, true), 1e-9);
        assertEquals(1.0, slow.hours(null));
    }
}
