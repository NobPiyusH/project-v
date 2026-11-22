package com.projectv.agents;

import com.projectv.agents.AgentTypes.Concept;
import com.projectv.agents.AgentTypes.PlannedShot;
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

    /** projectv.video.stock-lane (default off): the Director may mark shots lane=stock. */
    @org.springframework.beans.factory.annotation.Value("${projectv.video.stock-lane:false}")
    private boolean stockLane;

    static final String STOCK_RULE = """
            - lane may also be "stock": a REAL stock video clip (Pexels, Pixabay, Wikimedia Commons) of ordinary things that are easy to
              film: a living room, hands at a workbench, a factory floor, an antenna at sunset, a street, a family in a dim room.
              Use it for realistic everyday places, objects and people-doing-things, never for mechanisms (those are diagrams), never for
              imaginary or abstract scenes, never when a specific brand, logo, famous person or exact historical moment is needed.
              The description is the search text: say plainly what is filmed. ALWAYS also fill keyframePrompt and motionPrompt for a stock
              shot: they are used if no real clip is found.
            """;

    static final String DIAGRAM_RULE = """
            - lane: "ai_video" (default) or "diagram". Use "diagram" for a shot whose line explains HOW something works
              (parts, flows, layers, signals, colour mixing, a process step by step): it is drawn as an animated, labelled
              diagram, accurate by construction. Never use it for atmosphere, people or places; AI video hallucinates
              mechanisms, so a mechanism shot must be "diagram". A diagram shot's keyframePrompt/motionPrompt may be empty;
              its description must say exactly what the diagram shows and in what order. Diagram shots last 5-12 seconds.
            """;

    /**
     * projectv.cost.shot-plan-split-tokens (default 0 = never split): when the plan's expected output (thinking + shots) reaches
     * this many tokens, it is asked for in two calls (the first half of the script lines, then the second half) instead of
     * one call that can hit the output ceiling, be cut off and be paid again in full (22 Nov 2026: 5 SHOT_PLAN calls, 3 cut off).
     */
    @org.springframework.beans.factory.annotation.Value("${projectv.cost.shot-plan-split-tokens:0}")
    private int splitTokens;

    void setSplitTokens(int tokens) {
        this.splitTokens = tokens;
    }

    /** Thinking effort plus the JSON the model writes for each shot (measured 22 Nov 2026: 8,069 and 8,357 tokens out for a 26-shot plan, so about 280 a shot plus thinking). */
    static final int THINKING_TOKENS = 1500;
    static final int TOKENS_PER_SHOT = 280;

    static int expectedOutputTokens(double totalSec, boolean isShort) {
        double perShot = isShort ? 2.2 : 6.0;
        return THINKING_TOKENS + (int) Math.ceil(totalSec / perShot) * TOKENS_PER_SHOT;
    }

    public ShotPlan plan(UUID videoId, VideoPlan plan, Concept concept, List<TimedLine> lines, double bpm,
                         double totalSec, String ownerNotes) {
        boolean isShort = "short".equals(plan.format());
        if (splitTokens <= 0 || lines.size() < 4 || expectedOutputTokens(totalSec, isShort) < splitTokens) {
            return planOnce(videoId, plan, concept, lines, bpm, totalSec, ownerNotes, null);
        }
        // Split at the line that brings the narration to half; each half gets its share of the seconds.
        double all = lines.stream().mapToDouble(TimedLine::durationSec).sum();
        int cut = 1;
        double run = 0;
        while (cut < lines.size() - 1 && run + lines.get(cut - 1).durationSec() < all / 2) {
            run += lines.get(cut - 1).durationSec();
            cut++;
        }
        List<TimedLine> first = lines.subList(0, cut);
        List<TimedLine> second = lines.subList(cut, lines.size());
        double firstSec = totalSec * first.stream().mapToDouble(TimedLine::durationSec).sum() / all;
        ShotPlan a = planOnce(videoId, plan, concept, first, bpm, firstSec, ownerNotes,
                "This is PART 1 of 2 of one continuous shot plan: only the script lines listed here, which are the first half of the "
                        + "video. Do not wrap up the story; part 2 follows in a second call.");
        if (a.shots() == null || a.shots().isEmpty()) {
            return a;
        }
        PlannedShot lastShot = a.shots().get(a.shots().size() - 1);
        ShotPlan b = planOnce(videoId, plan, concept, second, bpm, totalSec - firstSec, ownerNotes,
                "This is PART 2 of 2 of one continuous shot plan: only the script lines listed here, which are the second half of the "
                        + "video. Part 1 is done and ended on the shot \"" + lastShot.description() + "\" (scene \"" + lastShot.sceneTitle()
                        + "\"); continue from there without repeating it, with the same visual flow, and finish the video.");
        List<PlannedShot> all2 = new java.util.ArrayList<>(a.shots());
        if (b.shots() != null) {
            all2.addAll(b.shots());
        }
        return new ShotPlan(all2);
    }

    private ShotPlan planOnce(UUID videoId, VideoPlan plan, Concept concept, List<TimedLine> lines, double bpm,
                              double totalSec, String ownerNotes, String partNote) {
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
                bpm > 0 ? 60.0 / bpm : 0.5, (diagramLane && "16:9".equals(plan.aspectRatio()) ? DIAGRAM_RULE : "") + (stockLane ? STOCK_RULE : ""));
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
        if (partNote != null) {
            user.append('\n').append(partNote).append('\n');
        }
        return llm.call(videoId, "SHOT_PLAN", system, user.toString(), ShotPlan.class, storyModel);
    }
}
