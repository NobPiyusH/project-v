package com.projectv.dream;

import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The impossible-architecture idea pack is plain data: it must load, be big enough, and every idea must be usable as written. */
class DreamIdeaPacksTest {

    static final Pattern STAR = Pattern.compile("\\*[^*\\s]+\\*");

    @Test
    void thePackLoadsWithAtLeastTwentyIdeasInADreamLook() {
        var pack = DreamIdeaPacks.pack("impossible-architecture").orElseThrow();
        assertTrue(pack.ideas().size() >= 20, "at least 20 concept seeds, got " + pack.ideas().size());
        assertTrue(DreamService.LOOKS.containsKey(pack.look()), "the pack's look exists: " + pack.look());
        assertTrue(DreamService.SELECTABLE_LOOKS.contains(pack.look()), "and the form offers it");
        assertFalse(DreamService.LOOK_ORDER.contains(pack.look()), "the Autopilot's rotation is unchanged");
    }

    @Test
    void everyIdeaHasAUniqueIdAHookOfThreeToSixWordsWithOneBigWordAndACameraNote() {
        Set<String> ids = new HashSet<>();
        for (var i : DreamIdeaPacks.pack("impossible-architecture").orElseThrow().ideas()) {
            assertTrue(ids.add(i.id()), "duplicate id " + i.id());
            for (String field : new String[]{i.title(), i.concept(), i.camera(), i.palette()}) {
                assertTrue(field != null && !field.isBlank(), i.id() + " has an empty field");
            }
            int words = i.hook().trim().split("\\s+").length;
            assertTrue(words >= 3 && words <= 6, i.id() + " hook has " + words + " words: " + i.hook());
            Matcher m = STAR.matcher(i.hook());
            assertTrue(m.find() && !m.find(), i.id() + " needs exactly one *starred* word: " + i.hook());
            assertEquals(i.hook().toUpperCase(), i.hook(), i.id() + " hook is upper case");
        }
    }

    @Test
    void referencesRoundTripAndUnknownOnesAreEmpty() {
        var idea = DreamIdeaPacks.idea("impossible-architecture", "looping-bridge").orElseThrow();
        assertEquals(idea, DreamIdeaPacks.resolve(DreamIdeaPacks.ref("impossible-architecture", "looping-bridge")).orElseThrow());
        assertTrue(DreamIdeaPacks.resolve(null).isEmpty());
        assertTrue(DreamIdeaPacks.resolve("impossible-architecture/nope").isEmpty());
        assertTrue(DreamIdeaPacks.resolve("garbage").isEmpty());
        assertTrue(DreamIdeaPacks.forWriter(idea).contains(idea.camera()));
    }

    @Test
    void theArchitectureLookHasItsOwnCameraRuleShotLengthAndHardCuts() {
        var look = DreamService.look("architecture");
        assertEquals(2.5, look.shotSeconds());
        assertEquals(0, look.dissolveSec(), "hard cuts: chained shots already flow into each other");
        assertTrue(DreamService.FEEL_ARCHITECTURE.contains("glides slowly and continuously"));
        assertTrue(DreamService.CHAIN_STRUCTURE.formatted(5).contains("first frame IS the previous shot"));
    }
}
