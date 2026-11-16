package com.projectv.agents;

import com.projectv.agents.AgentTypes.Concept;
import com.projectv.agents.AgentTypes.Script;
import com.projectv.media.MediaTypes.Fact;
import com.projectv.planner.StylePresets;
import com.projectv.planner.VideoPlan;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.UUID;
import java.util.stream.Collectors;

/** Concept and script (HANDOVER §10: Writer). Narration length is derived from the plan's duration and style. */
@Component
public class WriterAgent {

    /** Gemini TTS prebuilt voices offered to the writer (verify the list when the TTS model changes). */
    public static final List<String> VOICES = List.of("Charon", "Kore", "Orus", "Iapetus", "Algieba", "Gacrux",
            "Sadaltager", "Enceladus", "Algenib", "Schedar", "Rasalgethi", "Achird", "Fenrir", "Puck", "Zephyr", "Aoede");

    private final Llm llm;
    private final StylePresets presets;

    public WriterAgent(Llm llm, StylePresets presets) {
        this.llm = llm;
        this.presets = presets;
    }

    public Concept concept(UUID videoId, String prompt, VideoPlan plan, List<Fact> facts, String ownerNotes) {
        StylePresets.Preset preset = presets.byIdOrDefault(plan.stylePreset());
        String system = """
                You are the head writer of a premium documentary studio. Style preset: %s (%s).
                Design the concept for this video: one big idea that makes the audience feel something, an optional
                on-screen "clock" (a counter such as years, distance or temperature that drives the narrative; leave
                clockLabel empty if it does not fit), and 3-6 acts whose approxSec add up to the target duration.
                Return only the JSON object.""".formatted(preset.name(), preset.description());
        String user = """
                Owner's prompt: %s
                Plan: %s, %d seconds, tone %s, language %s, narration %s.
                Must include: %s
                Research facts:
                %s
                %s""".formatted(prompt, plan.title(), plan.durationSec(), plan.tone(), plan.language(), plan.narration(),
                String.join(", ", plan.mustInclude()), factsText(facts), notes(ownerNotes));
        return llm.call(videoId, "CONCEPT", system, user, Concept.class);
    }

    public Script script(UUID videoId, String prompt, VideoPlan plan, Concept concept, List<Fact> facts, String ownerNotes) {
        StylePresets.Preset preset = presets.byIdOrDefault(plan.stylePreset());
        String lengthRule = switch (plan.narration()) {
            case "none" -> "There is NO narration. Write %d visual beats (spoken=false), each a vivid description of what the audience sees and feels."
                    .formatted(Math.max(6, plan.durationSec() / 8));
            case "sparse" -> "Narration is SPARSE and poetic: about %d words in total, in %d-%d short lines (spoken=true), leaving long musical silences. Add a few spoken=false visual beats where music alone should carry the moment."
                    .formatted(Math.round(plan.durationSec() * 0.9), Math.max(6, plan.durationSec() / 20), Math.max(8, plan.durationSec() / 12));
            default -> "Narration is FULL: about %d words in total (≈2.3 words per second), split into lines of 1-2 sentences (spoken=true)."
                    .formatted(Math.round(plan.durationSec() * 2.3 * 0.85));
        };
        String system = """
                You are the head writer of a premium documentary studio. Style preset: %s (%s).
                Write the script as an ordered list of lines. %s
                Every factual claim must come from the research facts provided. Write in language "%s".
                Pick voiceName from %s to suit the tone, describe the reading style in voiceStyle as a short phrase of at most 8 words (e.g. "slow, hushed and awe-struck"), and write a one-paragraph
                musicBrief for the composer (mood, instruments, tempo feel, how it should build). No sound-effect cues in lines.
                Return only the JSON object.""".formatted(preset.name(), preset.description(), lengthRule, plan.language(), VOICES);
        String user = """
                Owner's prompt: %s
                Title: %s. Duration: %d s. Tone: %s.
                Concept: %s
                Big idea: %s
                Clock: %s
                Acts: %s
                Must include: %s
                Research facts:
                %s
                %s""".formatted(prompt, plan.title(), plan.durationSec(), plan.tone(), concept.logline(), concept.bigIdea(),
                blank(concept.clockLabel()) ? "none" : concept.clockLabel(),
                concept.acts() == null ? "" : concept.acts().stream().map(a -> a.title() + " (" + a.approxSec() + "s): " + a.purpose()).collect(Collectors.joining("; ")),
                String.join(", ", plan.mustInclude()), factsText(facts), notes(ownerNotes));
        return llm.call(videoId, "SCRIPT", system, user, Script.class);
    }

    static String factsText(List<Fact> facts) {
        if (facts == null || facts.isEmpty()) {
            return "(no research for this video)";
        }
        return facts.stream().map(f -> "- " + f.text()).collect(Collectors.joining("\n"));
    }

    static String notes(String ownerNotes) {
        return blank(ownerNotes) ? "" : "The owner rejected the previous version with these notes; follow them exactly:\n" + ownerNotes;
    }

    static boolean blank(String s) {
        return s == null || s.isBlank();
    }
}
