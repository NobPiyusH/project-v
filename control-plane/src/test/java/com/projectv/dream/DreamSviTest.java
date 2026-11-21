package com.projectv.dream;

import com.projectv.dream.DreamTypes.PlannedScene;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** SVI scenes: the pure rules (beats per length, writer shape, Unfiltered parsing). */
class DreamSviTest {

    @Test
    void sceneLengthsNeedThreeFourOrFiveBeats() {
        assertEquals(3, DreamSvi.beatsFor(10));
        assertEquals(4, DreamSvi.beatsFor(15));
        assertEquals(5, DreamSvi.beatsFor(20));
        assertEquals(353, DreamSvi.framesOf(5), "81 + 4 x 68 frames, cut to 321 (20 s) on the laptop");
        assertEquals(20, DreamSvi.cleanSeconds(30), "capped at 20 s: content drifts after that");
        assertEquals(15, DreamSvi.cleanSeconds(15));
        assertEquals("cut", DreamSvi.cleanBetween(null));
        assertEquals("dissolve", DreamSvi.cleanBetween("Dissolve"));
    }

    @Test
    void writerScenesAreFlattenedToBeatsAndPadded() {
        var beats = DreamSvi.flatten(List.of(new PlannedScene("a pier", List.of("walk", "turn")),
                new PlannedScene("a door", List.of("rise", "drift", "push", "extra"))), 2, 3);
        assertEquals(6, beats.size());
        assertEquals(new DreamSvi.Beat(1, 3, "a pier", "turn"), beats.get(2), "a missing beat repeats the scene's last motion");
        assertEquals(new DreamSvi.Beat(2, 1, "a door", "rise"), beats.get(3));
        assertEquals("push", beats.get(5).motion(), "beats beyond the Short's shape are dropped");
        assertTrue(DreamSvi.flatten(null, 2, 3).isEmpty());
    }

    @Test
    void unfilteredBlankLineSeparatesScenesAndEachLineIsABeat() {
        var beats = DreamSvi.unfiltered("pink pier at dawn\nwalk to the end\n\n\na red door opens\n");
        assertEquals(List.of(new DreamSvi.Beat(1, 1, "pink pier at dawn", "pink pier at dawn"),
                new DreamSvi.Beat(1, 2, "walk to the end", "walk to the end"),
                new DreamSvi.Beat(2, 1, "a red door opens", "a red door opens")), beats);
        assertEquals(List.of(1, 1, 1), DreamSvi.unfiltered("a\n\nb\n\nc\n\nd").stream().map(DreamSvi.Beat::beat).toList(),
                "at most 3 scenes");
        assertEquals(5, DreamSvi.unfiltered("1\n2\n3\n4\n5\n6\n7").size(), "at most 5 beats (20 s) a scene");
    }

    @Test
    void writerStructureAsksForScenesBeatsAndOneStyle() {
        String s = DreamSvi.structure(3, 20, 5);
        assertTrue(s.contains("3 scenes") && s.contains("5 beats") && s.contains("never cuts inside a scene"));
        assertTrue(DreamSvi.structure(1, 10, 3).startsWith("Invent 1 scene in the same world."));
        assertTrue(DreamSvi.jsonFields(3, 5).contains("\"style\": ONE line"));
    }

    @Test
    void beatsPerSceneFromShotRows() {
        List<Map<String, Object>> rows = List.of(Map.of("scene_no", 1), Map.of("scene_no", 1), Map.of("scene_no", 2));
        assertEquals(List.of(2, 1), DreamSvi.beatsPerScene(rows));
        assertFalse(DreamSvi.on(Map.of("svi_scenes", 0)));
        assertTrue(DreamSvi.on(Map.of("svi_scenes", 2)));
    }
}
