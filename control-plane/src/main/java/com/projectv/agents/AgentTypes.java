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
    public record ScriptLine(String text, String act, String purpose, boolean spoken) {
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
     */
    public record PlannedShot(String sceneTitle, Integer lineIndex, double durationSec, String shotType,
                              String cameraMove, String description, String keyframePrompt, String motionPrompt,
                              String priority, String overlayText) {
    }

    public record ShotPlan(List<PlannedShot> shots) {
    }
}
