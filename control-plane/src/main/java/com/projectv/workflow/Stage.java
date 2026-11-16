package com.projectv.workflow;

/**
 * Production stages in run order (HANDOVER §9.1, plus the R1 PLAN stage).
 * The Planner decides which of these a given video needs; skipped stages are recorded as SKIPPED.
 */
public enum Stage {
    PLAN("Plan", "Turn the prompt into a video plan: format, length, style, what's needed"),
    RESEARCH("Research", "Gather facts with sources"),
    CONCEPT("Concept", "Big idea, structure, the 'clock'"),
    SCRIPT("Script", "Narration text and beat sheet (or a visual beat sheet when there is no narration)"),
    MUSIC("Music", "Choose or generate the score and map its beats"),
    SHOT_PLAN("Shot plan", "Scenes and shots timed to the beats, with prompts per model"),
    KEYFRAMES("Keyframes", "One still per shot, before any video spend"),
    ANIMATIC("Animatic", "Stills + scratch voice + music cut together to lock story and pacing"),
    LIBRARY_MATCH("Library match", "Reuse good clips from earlier videos"),
    DRAFT_GEN("Draft generation", "Cheap low-res drafts of the remaining shots"),
    FINAL_GEN("Final generation", "Full-quality renders of the chosen drafts"),
    CRITIC("Critic", "Automatic quality checks on every take"),
    TAKE_SELECTION("Take selection", "Pick the best take per shot"),
    UPSCALE("Upscale", "Upscale to 4K, interpolate, colour-match"),
    AUDIO("Audio", "Final narration, sound effects, mix and loudness"),
    ASSEMBLY("Assembly", "Timeline, Premiere XML and the master render"),
    ROUGH_CUT_REVIEW("Rough cut review", "Review the cut and turn notes into changes"),
    PACKAGING("Packaging", "Captions, dubs, titles, thumbnails, Shorts"),
    FINAL_APPROVAL("Final approval", "Last check before publishing"),
    PUBLISH("Publish", "Upload as private to YouTube / Instagram");

    private final String label;
    private final String description;

    Stage(String label, String description) {
        this.label = label;
        this.description = description;
    }

    public String label() {
        return label;
    }

    public String description() {
        return description;
    }
}
