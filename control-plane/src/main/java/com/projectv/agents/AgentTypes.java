package com.projectv.agents;

import java.util.List;

/** Structured outputs the agents ask the LLM for. Keep fields simple: they double as the JSON schema. */
public final class AgentTypes {

    private AgentTypes() {
    }

    public record Act(String title, String purpose, int approxSec) {
    }

    /**
     * @param clockLabel e.g. "Years since the cloud began to collapse"; empty when the video has no on-screen clock
     */
    public record Concept(String logline, String bigIdea, String clockLabel, List<Act> acts, String emotionalArc) {
    }

    /**
     * One narration line (or, for videos without narration, one visual beat).
     *
     * @param spoken false for purely visual beats (no voice)
     */
    /** @param delivery how to read this line, a few words ("hushed, slow", "rising excitement"); optional */
    public record ScriptLine(String text, String act, String purpose, boolean spoken, String delivery) {
    }

    /**
     * @param voiceName one of the Gemini TTS prebuilt voices offered in the prompt
     * @param voiceStyle how to read it, e.g. "slow, hushed, awe-struck"
     */
    public record Script(List<ScriptLine> lines, String voiceName, String voiceStyle, String musicBrief) {
    }

    /**
     * @param lineIndex index of the script line this shot covers (null for pure visual/music moments)
     * @param priority  hero | standard | filler
     * @param footageQuery 2-5 exact search words when real footage (NASA, Wikimedia Commons) would show this
     *                  better, else empty
     * @param sfxQuery    2-4 words for a background sound (e.g. "underwater ambience"), else empty
     * @param referenceQuery name (scientific when possible) of a real subject that must look accurate, for real
     *                  reference photos given to the image model, else empty
     */
    public record PlannedShot(String sceneTitle, Integer lineIndex, double durationSec, String shotType,
                              String cameraMove, String description, String keyframePrompt, String motionPrompt,
                              String priority, String overlayText, String footageQuery, String referenceQuery,
                              String sfxQuery) {
    }

    public record ShotPlan(List<PlannedShot> shots) {
    }

    // ---- Creative Director (story-level review at three levels: shot, paragraph/act, whole video). Scores 1-5.

    /**
     * @param shot       1-based shot number
     * @param meaning    does the shot show the IDEA of its line in the context of its paragraph (not one keyword)?
     * @param accuracy   is what we see factually/scientifically right?
     * @param continuity does it belong with its neighbours (style, colour, scale, direction)?
     * @param literal    true when the shot illustrates a single word of the line instead of its meaning
     * @param writing    true when the picture shows (or its prompt asks for) generated lettering, which comes out garbled
     * @param fixPrompt  for any score of 3 or less: a complete replacement image prompt, else empty
     */
    public record ShotCritique(int shot, int meaning, int accuracy, int continuity, boolean literal, boolean writing,
                               String problem, String fixPrompt) {
    }

    /**
     * @param flow       do the paragraph's shots, in order, explain its point?
     * @param keyMoment  does the paragraph's main idea get the strongest image?
     * @param shotsToFix shot numbers that would fix this paragraph's problem (each needs a ShotCritique fixPrompt)
     */
    public record ActCritique(String act, int flow, int keyMoment, int continuity, int mood, String summary,
                              String problem, List<Integer> shotsToFix) {
    }

    /**
     * @param arc     hook -> build -> payoff -> ending, as a whole
     * @param variety no repeated images or ideas
     */
    public record VideoCritique(int arc, int hook, int ending, int variety, String summary, String problem) {
    }

    public record CreativeReview(VideoCritique video, List<ActCritique> acts, List<ShotCritique> shots) {
    }
}
