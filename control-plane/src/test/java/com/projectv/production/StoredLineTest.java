package com.projectv.production;

import com.projectv.production.ProductionService.AudioVersion;
import com.projectv.production.ProductionService.StoredLine;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class StoredLineTest {

    private static StoredLine voiced(String text) {
        UUID a = UUID.randomUUID();
        // A line voiced before versions existed (old JSON): audio set, no versions list.
        return new StoredLine(4, text, "Act 1", "hook", true, a, "s3://b/old.wav", 5.0, null, null, false, null, null, null);
    }

    @Test
    void editingTextFlagsTheLineAsNeedingVoiceAndKeepsTheOriginal() {
        StoredLine l = voiced("A star is born.").withEdit("A star ignites.", true);
        assertTrue(l.needsVoice());
        assertTrue(l.edited());
        assertEquals("A star is born.", l.originalText());
        StoredLine again = l.withEdit("A star ignites in silence.", true);
        assertEquals("A star is born.", again.originalText(), "the writer's original survives later edits");
    }

    @Test
    void savingTheSameTextChangesNothing() {
        StoredLine l = voiced("Same.").withEdit("Same.", true);
        assertFalse(l.needsVoice());
        assertFalse(l.edited());
        assertNull(l.originalText());
    }

    @Test
    void aNewTakeKeepsTheOldTakeAsAVersionAndClearsNeedsVoice() {
        StoredLine old = voiced("Old text").withEdit("New text", true);
        UUID newAsset = UUID.randomUUID();
        StoredLine l = old.withNewTake(new AudioVersion(newAsset, "s3://b/new.wav", 4.2, "now", "coverage 100%", null, "New text"), true);
        assertEquals(newAsset, l.audioAssetId());
        assertEquals(2, l.audioVersions().size(), "old take kept as a version");
        assertFalse(l.needsVoice());
        assertEquals("READY", l.voiceStatus());
        StoredLine back = l.withActive(l.audioVersions().get(1));
        assertEquals(old.audioAssetId(), back.audioAssetId(), "switching back to the old take works");
        assertTrue(back.needsVoice(), "the old take says the old text, so it no longer matches");
    }

    @Test
    void editingALineBackToWhatItsVoiceSaysClearsNeedsVoice() {
        StoredLine l = voiced("A star is born.").withEdit("A star ignites.", true);
        assertTrue(l.needsVoice());
        StoredLine reverted = l.withEdit("A star is born.", true);
        assertFalse(reverted.needsVoice(), "the audio says this text again");
        assertFalse(reverted.edited(), "back to the writer's text");
        assertNull(reverted.originalText());
    }

    @Test
    void anImperfectTakeIsMarkedSo() {
        StoredLine l = voiced("x").withNewTake(new AudioVersion(UUID.randomUUID(), "u", 1, "now", "only 60% spoken", null, "x"), false);
        assertEquals("IMPERFECT", l.voiceStatus());
    }
}
