package com.projectv.agents;

import com.projectv.agents.AgentTypes.Concept;
import com.projectv.agents.AgentTypes.ShotPlan;
import com.projectv.planner.StylePresets;
import com.projectv.planner.VideoPlan;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.UUID;

/** Shot plan (HANDOVER §10: Director). Timing is refined afterwards in code (snapping to the music's beats). */
@Component
public class DirectorAgent {

    /** A script line with its real narration length, as measured from the generated voice. */
    public record TimedLine(int index, String text, boolean spoken, double durationSec) {
    }

    private final Llm llm;
    private final StylePresets presets;
    /** Model tried first for this agent's most important call (Claude, owner-funded and capped); Gemini behind it. */
    private final String storyModel;
    /** projectv.video.diagram-lane (default off): the Director may mark shots lane=diagram. */
    @org.springframework.beans.factory.annotation.Value("${projectv.video.diagram-lane:false}")
    private boolean diagramLane;

    public DirectorAgent(Llm llm, StylePresets presets,
                 @org.springframework.beans.factory.annotation.Value("${projectv.models.story:}") String storyModel) {
        this.llm = llm;
        this.presets = presets;
        this.storyModel = storyModel;
    }

    static final String DIAGRAM_RULE = """
            - lane: "ai_video" (default) or "diagram". Use "diagram" for a shot whose line explains HOW something works
              (parts, flows, layers, signals, colour mixing, a process step by step): it is drawn as an animated, labelled
              diagram, accurate by construction. Never use it for atmosphere, people or places; AI video hallucinates
              mechanisms, so a mechanism shot must be "diagram". A diagram shot's keyframePrompt/motionPrompt may be empty;
              its description must say exactly what the diagram shows and in what order. Diagram shots last 5-12 seconds.
            """;

    public ShotPlan plan(UUID videoId, VideoPlan plan, Concept concept, List<TimedLine> lines, double bpm,
                         double totalSec, String ownerNotes) {
        StylePresets.Preset preset = presets.byIdOrDefault(plan.stylePreset());
        boolean isShort = "short".equals(plan.format());
        String pacing = isShort ? "fast cuts: shots of 1.5-3 seconds" : "cinematic pacing: shots of 4-9 seconds, longer for awe moments";
        String system = """
                You are the director of a premium documentary studio. Style preset: %s (%s).
                Break the video into an ordered list of shots covering exactly %.0f seconds in total, with %s.
                Every script line must be covered: set lineIndex on the shot(s) during which that line is heard, in order;
                a spoken line's shots must together last at least its duration plus ~0.5 s. Use lineIndex null for
                music-only moments. Music tempo is about %.0f BPM: prefer durations that are multiples of one beat (%.2f s).
                For each shot write:
                - description: what we see, in plain words
                - keyframePrompt: a rich, self-contained image prompt for the FIRST frame (subject, composition, lens,
                  light, colour, atmosphere). Never mention the camera move. Never ask for text, words, letters,
                  numbers, titles, signs, logos, or clock or screen digits: image and video models can't spell, so
                  anything the viewer must read goes in overlayText (the edit draws it). Never depict real people.
                - motionPrompt: how the shot moves over its duration (camera move + motion in the scene), for a video model
                - cameraMove: one of push_in, pull_out, orbit, drift_left, drift_right, crane_up, crane_down, static, flythrough
                - shotType: extreme_wide, wide, medium, close_up, macro, aerial, pov
                - priority: "hero" for the 10-20%% of shots that matter most (opening, reveals, climax, ending), else "standard"
                - overlayText: short on-screen text such as the clock value ("13.8 billion years ago"), or empty
                - footageQuery: whenever a REAL recording of this exact thing is likely to exist and would show the
                  line as well as or truer than a generated image (it is free, and real footage reads as authentic):
                  space (the real Sun, Earth from orbit, launches, telescope views) from NASA, or real nature, animals,
                  insects, plants, weather, microscopy, lab experiments, places, historic events and expeditions (e.g.
                  NOAA deep-sea dives) from Wikimedia Commons. In science videos, aim for roughly a quarter of the
                  shots where it fits. 2-5 exact search words, such as "solar flare SDO", "Okeanos Explorer",
                  "firefly bioluminescence". Otherwise empty. Always for a real event or "first ever" claim. Never for
                  imaginary, dreamlike or abstract scenes, and never for hero shots that need a specific composition.
                - sfxQuery: 2-4 words for a quiet background sound that makes the shot feel real ("underwater
                  ambience", "train interior", "wind over grass", "crackling fire"); keep the same words across
                  consecutive shots in one place so the ambience flows. Empty for silence or pure music moments.
                - referenceQuery: for a shot showing a real creature, object or place that must look accurate, its
                  name for finding real reference photos, scientific name when it has one (e.g. "Melanocetus
                  johnsonii", "Architeuthis dux", "Hubble Space Telescope"). Otherwise empty.
                Keep a continuous visual flow between consecutive shots (scale, direction, colour).
                %sReturn only the JSON object.""".formatted(preset.name(), preset.description(), totalSec, pacing, bpm,
                bpm > 0 ? 60.0 / bpm : 0.5, diagramLane && "16:9".equals(plan.aspectRatio()) ? DIAGRAM_RULE : "");
        StringBuilder user = new StringBuilder();
        user.append("Title: ").append(plan.title()).append(". Aspect ratio ").append(plan.aspectRatio()).append(".\n");
        user.append("Concept: ").append(concept.logline()).append("\nBig idea: ").append(concept.bigIdea()).append('\n');
        if (!WriterAgent.blank(concept.clockLabel())) {
            user.append("On-screen clock: ").append(concept.clockLabel()).append('\n');
        }
        user.append("Script lines (index | spoken | seconds | text):\n");
        for (TimedLine l : lines) {
            user.append(l.index()).append(" | ").append(l.spoken() ? "voice" : "visual").append(" | ")
                    .append(String.format("%.1f", l.durationSec())).append(" | ").append(l.text()).append('\n');
        }
        user.append(WriterAgent.notes(ownerNotes));
        return llm.call(videoId, "SHOT_PLAN", system, user.toString(), ShotPlan.class, storyModel);
    }
}
