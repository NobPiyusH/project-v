package com.projectv.production;

import com.projectv.media.MediaTypes.AnimaticRequest;
import com.projectv.media.MediaTypes.AnimaticResult;
import com.projectv.media.MediaTypes.KeyframeRequest;
import com.projectv.media.MediaTypes.KeyframeResult;
import com.projectv.media.MediaTypes.MusicRequest;
import com.projectv.media.MediaTypes.MusicResult;
import com.projectv.media.MediaTypes.NarrationRequest;
import com.projectv.media.MediaTypes.NarrationResult;
import com.projectv.media.MediaTypes.ResearchRequest;
import com.projectv.media.MediaTypes.ResearchResult;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Front door for the production stages (activities, API and services call this). Each stage lives in its own class
 * (ResearchStage, StoryStage, MusicStage, VoiceStage, ShotStage, AnimaticStage, ProductionReadModel); this class keeps
 * the shared records and delegates, so callers did not change when the stages were split (Work X, 16 Nov 2026).
 */
@Service
public class ProductionService {

    /** One generated voice take of a line. All takes are kept; the line points at the active one. */
    /** @param text exactly what this take says (so the system knows whether an edited line still matches it) */
    public record AudioVersion(UUID assetId, String uri, double durationSec, String createdAt, String check, String style,
                               String text) {
    }

    /**
     * One stored script line, enriched with its narration audio once generated.
     *
     * @param voiceStatus  null | REDOING | READY | IMPERFECT (last take still failed the transcript check) | FAILED
     * @param needsVoice   true when the owner edited the text after it was voiced (the audio no longer matches)
     * @param originalText the writer's text before the owner's first edit (kept for taste learning)
     */
    public record StoredLine(int index, String text, String act, String purpose, boolean spoken,
                             UUID audioAssetId, String audioUri, Double durationSec,
                             String voiceStatus, Boolean needsVoice, Boolean edited, String originalText,
                             List<AudioVersion> audioVersions, String check, String delivery) {

        public StoredLine(int index, String text, String act, String purpose, boolean spoken) {
            this(index, text, act, purpose, spoken, null);
        }

        /** @param delivery how to read THIS line (owner, 18 Nov 2026: the voice follows the story line by line) */
        public StoredLine(int index, String text, String act, String purpose, boolean spoken, String delivery) {
            this(index, text, act, purpose, spoken, null, null, null, null, null, false, null, List.of(), null, delivery);
        }

        List<AudioVersion> versions() {
            return audioVersions == null ? List.of() : audioVersions;
        }

        StoredLine withStatus(String status, String why) {
            return new StoredLine(index, text, act, purpose, spoken, audioAssetId, audioUri, durationSec, status,
                    needsVoice, edited, originalText, audioVersions, why, delivery);
        }

        StoredLine withNewTake(AudioVersion v, boolean passed) {
            List<AudioVersion> all = new ArrayList<>(versions());
            if (all.isEmpty() && audioAssetId != null) {
                // Lines voiced before versions existed: keep the current take as the first version.
                all.add(new AudioVersion(audioAssetId, audioUri, durationSec == null ? 0 : durationSec, "", check, null, voicedText()));
            }
            all.addFirst(v);
            return new StoredLine(index, text, act, purpose, spoken, v.assetId(), v.uri(), v.durationSec(),
                    passed ? "READY" : "IMPERFECT", false, edited, originalText, all, v.check(), delivery);
        }

        StoredLine withActive(AudioVersion v) {
            boolean needs = spoken && v.text() != null && !v.text().equals(text);
            return new StoredLine(index, text, act, purpose, spoken, v.assetId(), v.uri(), v.durationSec(), "READY",
                    needs, edited, originalText, audioVersions, v.check(), delivery);
        }

        /** The text the active take actually speaks (older data: the text before the owner's first edit). */
        String voicedText() {
            if (audioAssetId == null) {
                return null;
            }
            return versions().stream().filter(v -> audioAssetId.equals(v.assetId()) && v.text() != null)
                    .map(AudioVersion::text).findFirst()
                    .orElse(originalText != null ? originalText : (Boolean.TRUE.equals(needsVoice) ? null : text));
        }

        StoredLine withEdit(String newText, boolean newSpoken) {
            boolean needs = newSpoken && !newText.equals(voicedText());
            String writers = originalText != null ? originalText : text;   // the writer's version, before any edit
            boolean differs = !newText.equals(writers);
            return new StoredLine(index, newText, act, purpose, newSpoken, audioAssetId, audioUri, durationSec,
                    voiceStatus, needs, differs, differs ? writers : null, audioVersions, check, delivery);
        }
    }

    /**
     * @param lineStarts script line index -> start time on the timeline (set by the shot plan, from real voice lengths)
     * @param tempo      voice speed factor the animatic/edit applies (1.0 = unchanged; Shorts may use up to 1.08)
     */
    public record StoredScript(int version, List<StoredLine> lines, String voiceName, String voiceStyle, String musicBrief,
                               Map<String, Double> lineStarts, Double tempo) {
    }

    /** Which image model a (re)render may use. AUTO: hero shots premium while the budget allows, the rest free. */
    public enum ImagePolicy { AUTO, PREMIUM, FREE }

    private final ResearchStage researchStage;
    private final StoryStage storyStage;
    private final MusicStage musicStage;
    private final VoiceStage voiceStage;
    private final ShotStage shotStage;
    private final AnimaticStage animaticStage;
    private final ProductionReadModel productionReadModel;

    public ProductionService(ResearchStage researchStage, StoryStage storyStage, MusicStage musicStage, VoiceStage voiceStage, ShotStage shotStage, AnimaticStage animaticStage, ProductionReadModel productionReadModel) {
        this.researchStage = researchStage;
        this.storyStage = storyStage;
        this.musicStage = musicStage;
        this.voiceStage = voiceStage;
        this.shotStage = shotStage;
        this.animaticStage = animaticStage;
        this.productionReadModel = productionReadModel;
    }

    public ResearchRequest researchRequest(UUID videoId, String ownerNotes) {
        return researchStage.researchRequest(videoId, ownerNotes);
    }

    public StageResult saveResearch(UUID videoId, ResearchResult r) {
        return researchStage.saveResearch(videoId, r);
    }

    public StageResult concept(UUID videoId, String ownerNotes) {
        return storyStage.concept(videoId, ownerNotes);
    }

    public StageResult script(UUID videoId, String ownerNotes) {
        return storyStage.script(videoId, ownerNotes);
    }

    public MusicRequest musicRequest(UUID videoId, String ownerNotes) {
        return musicStage.musicRequest(videoId, ownerNotes);
    }

    public StageResult saveMusic(UUID videoId, MusicResult r) {
        return musicStage.saveMusic(videoId, r);
    }

    public NarrationRequest narrationRequest(UUID videoId) {
        return voiceStage.narrationRequest(videoId);
    }

    public StageResult saveNarration(UUID videoId, NarrationResult r) {
        return voiceStage.saveNarration(videoId, r);
    }

    public StoredLine editLine(UUID videoId, int index, String text, Boolean spoken) {
        return voiceStage.editLine(videoId, index, text, spoken);
    }

    public NarrationRequest lineNarrationRequest(UUID videoId, List<Integer> indexes, String styleNotes) {
        return voiceStage.lineNarrationRequest(videoId, indexes, styleNotes);
    }

    public StageResult saveLineNarration(UUID videoId, NarrationResult r, String style) {
        return voiceStage.saveLineNarration(videoId, r, style);
    }

    public void markLines(UUID videoId, List<Integer> indexes, String status, String why) {
        voiceStage.markLines(videoId, indexes, status, why);
    }

    public void selectLineVoice(UUID videoId, int index, UUID assetId) {
        voiceStage.selectLineVoice(videoId, index, assetId);
    }

    public StageResult shotPlan(UUID videoId, String ownerNotes) {
        return shotStage.shotPlan(videoId, ownerNotes);
    }

    public KeyframeRequest keyframeRequest(UUID videoId, String ownerNotes) {
        return shotStage.keyframeRequest(videoId, ownerNotes);
    }

    public KeyframeRequest shotKeyframeRequest(UUID videoId, List<UUID> shotIds, String ownerNotes, ImagePolicy policy) {
        return shotStage.shotKeyframeRequest(videoId, shotIds, ownerNotes, policy);
    }

    public void markShots(UUID videoId, List<UUID> shotIds, String status, String error) {
        shotStage.markShots(videoId, shotIds, status, error);
    }

    public void editShotPrompt(UUID shotId, String keyframePrompt) {
        shotStage.editShotPrompt(shotId, keyframePrompt);
    }

    public List<Map<String, Object>> shotVersions(UUID shotId) {
        return shotStage.shotVersions(shotId);
    }

    public void selectKeyframe(UUID shotId, UUID assetId) {
        shotStage.selectKeyframe(shotId, assetId);
    }

    public UUID videoOfShot(UUID shotId) {
        return shotStage.videoOfShot(shotId);
    }

    /** Lets go of the rented GPU pod after a keyframe batch (no-op unless provider-keyframes=broker). */
    public void releaseGpu(UUID videoId, String why) {
        shotStage.releaseGpu(videoId, why);
    }

    public StageResult saveKeyframes(UUID videoId, KeyframeRequest request, KeyframeResult r) {
        return shotStage.saveKeyframes(videoId, request, r);
    }

    public AnimaticRequest animaticRequest(UUID videoId) {
        return animaticStage.animaticRequest(videoId);
    }

    public StageResult saveAnimatic(UUID videoId, AnimaticResult r) {
        return animaticStage.saveAnimatic(videoId, r);
    }

    public Map<String, Object> production(UUID videoId) {
        return productionReadModel.production(videoId);
    }
}
