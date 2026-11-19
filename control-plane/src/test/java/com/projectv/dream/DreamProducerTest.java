package com.projectv.dream;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** What the Producer judges on a Dream Short, and its money rule. */
class DreamProducerTest {

    @Test
    void spendFitsOnlyUnderTheAskLineAndInsideTheLimit() {
        BigDecimal ask = BigDecimal.valueOf(50);
        assertTrue(DreamProducer.fits(BigDecimal.valueOf(12), ask, BigDecimal.valueOf(5), BigDecimal.valueOf(100)));
        assertFalse(DreamProducer.fits(BigDecimal.valueOf(51), ask, BigDecimal.ZERO, BigDecimal.valueOf(100)), "above ₹50: ask");
        assertFalse(DreamProducer.fits(BigDecimal.valueOf(20), ask, BigDecimal.valueOf(90), BigDecimal.valueOf(100)), "over the Short's limit");
        assertFalse(DreamProducer.fits(BigDecimal.valueOf(20), ask, BigDecimal.valueOf(25), BigDecimal.valueOf(40)), "over the daily cap");
    }

    @Test
    void itJudgesThePlanPicturesClipsMusicAndTheFinishedShort() {
        UUID music = UUID.randomUUID();
        Map<String, Object> d = Map.of("idea", "a station in the clouds", "title", "Cloud Station", "music_asset_id", music);
        List<Map<String, Object>> shots = List.of(
                Map.of("ord", 0, "scene", "platform", "motion_prompt", "slow push", "picture_uri", "s3://p0", "clip_uri", "s3://c0"),
                Map.of("ord", 1, "scene", "train", "motion_prompt", "pan", "picture_uri", "s3://p1"));
        assertEquals("text", DreamProducer.items("WRITE", d, shots).getFirst().kind());
        assertTrue(DreamProducer.items("WRITE", d, shots).getFirst().text().contains("2. train  [camera: pan]"));
        assertEquals(List.of("shot-1", "shot-2"), DreamProducer.items("PICTURES", d, shots).stream().map(i -> i.id()).toList());
        assertEquals(List.of("shot-1"), DreamProducer.items("CLIPS", d, shots).stream().map(i -> i.id()).toList(), "only shots with a clip");
        assertEquals("@" + music, DreamProducer.items("MUSIC", d, shots).getFirst().uri());
        assertTrue(DreamProducer.items("FINISH", d, shots).isEmpty(), "nothing to watch before the finish exists");
        assertTrue(DreamProducer.items("SCOUT", d, shots).isEmpty());
    }
}
