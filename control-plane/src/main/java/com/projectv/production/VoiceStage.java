package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.ledger.BudgetGuard;
import com.projectv.media.MediaTypes.NarrationClip;
import com.projectv.media.MediaTypes.NarrationLine;
import com.projectv.media.MediaTypes.NarrationRequest;
import com.projectv.media.MediaTypes.NarrationResult;
import com.projectv.media.MediaTypes.Usage;
import com.projectv.planner.VideoPlan;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import com.projectv.production.ProductionService.*;

/** Narration: whole-script voicing, per-line re-voicing, direct line editing, take selection. */
@Service
public class VoiceStage extends StageSupport {

    public VoiceStage(ProductionDeps d) {
        super(d);
    }

    public NarrationRequest narrationRequest(UUID videoId) {
        VideoPlan plan = plan(videoId);
        StoredScript s = latestScript(videoId).orElseThrow(() -> new IllegalStateException("No script yet"));
        List<NarrationLine> lines = s.lines().stream().filter(StoredLine::spoken)
                .map(l -> new NarrationLine(l.index(), l.text())).toList();
        // ~25 audio tokens per second of speech at ~2.3 words per second.
        long words = lines.stream().mapToLong(l -> l.text().split("\\s+").length).sum();
        budget.require(pricing.ttsInr(new Usage(words * 2, Math.round(words / 2.3 * 25 * 1.5), 0, 0)), "NARRATION");
        // Short "in a <style> voice" phrase: long instructions get read aloud by the TTS model (owner feedback).
        String raw = s.voiceStyle() == null ? "" : s.voiceStyle().strip().replaceAll("[.\\s]+$", "");
        String[] styleWords = raw.split("\\s+");
        String shortStyle = String.join(" ", java.util.Arrays.copyOf(styleWords, Math.min(styleWords.length, 8))).toLowerCase(java.util.Locale.ROOT);
        String style = shortStyle.isBlank() ? "in a calm documentary narrator's voice" : "in a " + shortStyle + " voice";
        return new NarrationRequest(videoId.toString(), ttsModel, textModel, s.voiceName(), style, plan.language(), lines);
    }

    @Transactional
    public StageResult saveNarration(UUID videoId, NarrationResult r) {
        applyClips(videoId, r, null, "NARRATION");

        int retakes = r.lines().stream().mapToInt(c -> Math.max(0, c.takes() - 1)).sum();
        List<String> failed = r.lines().stream().filter(c -> !c.passed()).map(c -> "#" + c.index() + " (" + c.check() + ")").toList();
        double seconds = r.lines().stream().mapToDouble(NarrationClip::durationSec).sum();
        String summary = "%d lines voiced by %s, %.0fs of speech. Every line checked by transcription: %d re-voiced automatically%s."
                .formatted(r.lines().size(), r.voiceName(), seconds, retakes,
                        failed.isEmpty() ? ", all pass" : "; still imperfect after 3 takes: " + String.join(", ", failed));
        return StageResult.of(summary, json.writeValueAsString(Map.of("lines", r.lines().size(), "retakes", retakes, "failed", failed)));
    }

    /** Stores new voice takes (whole stage or single lines): asset + provenance per take, version kept, cost recorded. */
    private void applyClips(UUID videoId, NarrationResult r, String style, String purpose) {
        StoredScript s = lockScript(videoId);
        Map<Integer, NarrationClip> byIndex = new LinkedHashMap<>();
        Usage total = Usage.NONE;
        for (NarrationClip c : r.lines()) {
            byIndex.put(c.index(), c);
            if (c.usage() != null) {
                total = new Usage(total.promptTokens() + c.usage().promptTokens(), total.outputTokens() + c.usage().outputTokens(),
                        total.thoughtTokens() + c.usage().thoughtTokens(), total.totalTokens() + c.usage().totalTokens());
            }
        }
        String now = java.time.Instant.now().toString();
        List<StoredLine> lines = new ArrayList<>();
        for (StoredLine l : s.lines()) {
            NarrationClip c = byIndex.get(l.index());
            if (c == null) {
                lines.add(l);
                continue;
            }
            UUID assetId = assets.create(new NewAsset(videoId, "audio", c.uri(), "audio/wav", null, null, c.durationSec(),
                    "gemini_api", r.model(), "[" + r.voiceName() + "] " + l.text(), null, AssetStore.LICENCE_GEMINI, true));
            lines.add(l.withNewTake(new AudioVersion(assetId, c.uri(), c.durationSec(), now, c.check(), style, l.text()), c.passed()));
        }
        writeScript(videoId, withLines(s, lines));
        ledger.record(videoId, BudgetGuard.GEMINI, pricing.ttsInr(total), BigDecimal.valueOf(total.totalTokens()), "tokens",
                true, purpose + " (" + r.model() + ", " + r.lines().size() + " lines)");
    }

    /**
     * Owner edits a line. The writer's original is kept; a spoken line whose text changed is flagged "needs voice"
     * (its old audio no longer matches) until it is re-voiced. Recorded as an edit for taste learning.
     */
    @Transactional
    public StoredLine editLine(UUID videoId, int index, String text, Boolean spoken) {
        String clean = text == null ? "" : text.strip().replaceAll("\\s+", " ");
        if (clean.isEmpty()) {
            throw new IllegalStateException("A line can't be empty");
        }
        StoredScript s = lockScript(videoId);
        List<StoredLine> lines = new ArrayList<>();
        StoredLine updated = null;
        for (StoredLine l : s.lines()) {
            if (l.index() == index) {
                updated = l.withEdit(clean, spoken == null ? l.spoken() : spoken);
                if (!clean.equals(l.text())) {
                    jdbc.sql("""
                                    INSERT INTO preference_event (id, kind, video_id, context_json, chosen, rejected)
                                    VALUES (:id, 'edit_diff', :v, CAST(:ctx AS jsonb), :chosen, :rejected)
                                    """)
                            .param("id", UUID.randomUUID()).param("v", videoId)
                            .param("ctx", json.writeValueAsString(Map.of("stage", "SCRIPT", "line", index)))
                            .param("chosen", clean).param("rejected", l.text()).update();
                }
                lines.add(updated);
            } else {
                lines.add(l);
            }
        }
        if (updated == null) {
            throw new java.util.NoSuchElementException("No line " + index);
        }
        writeScript(videoId, withLines(s, lines));
        return updated;
    }

    /** Re-voice only the given lines. {@code styleNotes} (e.g. "slower and more hushed") replaces the reading style. */
    public NarrationRequest lineNarrationRequest(UUID videoId, List<Integer> indexes, String styleNotes) {
        NarrationRequest base = narrationRequest(videoId);
        java.util.Set<Integer> wanted = new java.util.HashSet<>(indexes);
        List<NarrationLine> lines = base.lines().stream().filter(l -> wanted.contains(l.index())).toList();
        if (lines.isEmpty()) {
            throw new IllegalStateException("None of those lines are spoken lines of this video");
        }
        String style = base.style();
        if (styleNotes != null && !styleNotes.isBlank()) {
            String[] w = styleNotes.strip().replaceAll("[.\\s]+$", "").split("\\s+");
            // Kept short on purpose: long style text gets read aloud by the TTS model.
            style = "in a voice that is " + String.join(" ", java.util.Arrays.copyOf(w, Math.min(w.length, 12))).toLowerCase(java.util.Locale.ROOT);
        }
        return new NarrationRequest(base.videoId(), base.model(), base.checkModel(), base.voiceName(), style,
                base.language(), lines);
    }

    @Transactional
    public StageResult saveLineNarration(UUID videoId, NarrationResult r, String style) {
        applyClips(videoId, r, style, "VOICE REDO");
        long imperfect = r.lines().stream().filter(c -> !c.passed()).count();
        return StageResult.of("%d line(s) re-voiced%s.".formatted(r.lines().size(),
                imperfect == 0 ? ", all pass the transcript check" : "; " + imperfect + " still imperfect"), null);
    }

    @Transactional
    public void markLines(UUID videoId, List<Integer> indexes, String status, String why) {
        StoredScript s = lockScript(videoId);
        java.util.Set<Integer> wanted = new java.util.HashSet<>(indexes);
        writeScript(videoId, withLines(s, s.lines().stream()
                .map(l -> wanted.contains(l.index()) ? l.withStatus(status, why) : l).toList()));
    }

    /** Switch a line back (or forward) to any earlier voice take. Free. */
    @Transactional
    public void selectLineVoice(UUID videoId, int index, UUID assetId) {
        StoredScript s = lockScript(videoId);
        boolean found = false;
        List<StoredLine> lines = new ArrayList<>();
        for (StoredLine l : s.lines()) {
            if (l.index() == index) {
                AudioVersion v = l.versions().stream().filter(x -> x.assetId().equals(assetId)).findFirst()
                        .orElseThrow(() -> new IllegalStateException("That take is not a version of line " + index));
                lines.add(l.withActive(v));
                found = true;
            } else {
                lines.add(l);
            }
        }
        if (!found) {
            throw new java.util.NoSuchElementException("No line " + index);
        }
        writeScript(videoId, withLines(s, lines));
    }
}
