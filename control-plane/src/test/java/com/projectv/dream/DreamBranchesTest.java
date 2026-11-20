package com.projectv.dream;

import com.projectv.dream.DreamBranches.PathPick;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** "Choose the path" pure helpers: option clean-up, the pick, and what the writer reads for a continuation. */
class DreamBranchesTest {

    @Test
    void pathOptionsAreCleanedToTwoShortDifferentOnes() {
        assertEquals(List.of("the door", "the stairs"), DreamBranches.cleanPaths(List.of(" \"the door\" ", "the *stairs*")));
        assertEquals("the long red hallway", DreamBranches.cleanPath("the long red hallway beyond the arch"), "at most 4 words");
        assertEquals("left door", DreamBranches.cleanPath("← left door"));
        assertNull(DreamBranches.cleanPaths(List.of("the door", "THE DOOR")), "two different options or none");
        assertNull(DreamBranches.cleanPaths(List.of("the door")));
        assertNull(DreamBranches.cleanPaths(Arrays.asList("  ", null, "x")));
        assertNull(DreamBranches.cleanPaths(null));
        assertEquals(List.of("a", "b"), DreamBranches.cleanPaths(List.of("a", "b", "c")), "only two are kept");
    }

    @Test
    void aPickIsAOrBAndRemembersWhoPicked() {
        assertEquals("B", PathPick.owner(" b ").path());
        assertEquals(DreamBranches.OWNER, PathPick.owner("A").source());
        var vote = new PathPick("a", DreamBranches.COMMENTS, Map.of("A", 30, "B", 12)); // how step 3 will plug in
        assertEquals(DreamBranches.COMMENTS, vote.source());
        assertThrows(IllegalArgumentException.class, () -> PathPick.owner("C"));
        assertThrows(IllegalArgumentException.class, () -> PathPick.owner(null));
    }

    @Test
    void theWriterContinuesTheSameWorldFromTheLastFrameDownTheChosenPath() {
        String s = DreamBranches.forWriter(Map.of("title", "Glass City", "world", "a city of twisted glass", "hero", "a red kiosk",
                "lastScene", "an arch with a door on the left and stairs on the right"), "the stairs", 2);
        assertTrue(s.contains("EPISODE 2") && s.contains("SAME world"));
        assertTrue(s.contains("a city of twisted glass") && s.contains("a red kiosk"));
        assertTrue(s.contains("ended on: an arch with a door"));
        assertTrue(s.contains("chose the path: THE STAIRS") && s.contains("last frame"));
        assertTrue(DreamBranches.forWriter(Map.of(), "x", 3).contains("EPISODE 3"), "missing memory never breaks the writer");
        assertEquals("Episode 2: Glass City - the path chosen: the stairs", DreamBranches.childIdea("Glass City", "the stairs", 2));
    }
}
