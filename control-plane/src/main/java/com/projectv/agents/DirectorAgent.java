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

    public DirectorAgent(Llm llm, StylePresets presets) {
        this.llm = llm;
        this.presets = presets;
    }

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
                  light, colour, atmosphere). Never mention the camera move, text or captions. Never depict real people.
                - motionPrompt: how the shot moves over its duration (camera move + motion in the scene), for a video model
                - cameraMove: one of push_in, pull_out, orbit, drift_left, drift_right, crane_up, crane_down, static, flythrough
                - shotType: extreme_wide, wide, medium, close_up, macro, aerial, pov
                - priority: "hero" for the 10-20%% of shots that matter most (opening, reveals, climax, ending), else "standard"
                - overlayText: short on-screen text such as the clock value ("13.8 billion years ago"), or empty
                - footageQuery: only when REAL NASA footage would show this line better and truer than a generated
                  image (the real Sun, Earth from orbit, a rocket launch, the ISS, planets or galaxies as telescopes
                  saw them): 2-5 search words such as "solar flare SDO" or "Earth night lights ISS". Otherwise empty.
                  Never for imaginary, abstract or historical scenes.
                Keep a continuous visual flow between consecutive shots (scale, direction, colour).
                Return only the JSON object.""".formatted(preset.name(), preset.description(), totalSec, pacing, bpm,
                bpm > 0 ? 60.0 / bpm : 0.5);
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
        return llm.call(videoId, "SHOT_PLAN", system, user.toString(), ShotPlan.class);
    }
}
