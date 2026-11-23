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
    /** Gemini TTS voices with their character (Google's own one-word descriptions), so the writer casts by meaning:
     *  the voice suits the story's context and depth (owner, 18 Nov 2026). */
    public static final java.util.Map<String, String> VOICE_CHARACTER = new java.util.LinkedHashMap<>();
    static {
        String[][] v = {{"Charon", "informative, calm, deep"}, {"Kore", "firm, clear"}, {"Orus", "firm"},
                {"Iapetus", "clear"}, {"Algieba", "smooth"}, {"Gacrux", "mature"}, {"Sadaltager", "knowledgeable"},
                {"Enceladus", "breathy, intimate"}, {"Algenib", "gravelly"}, {"Schedar", "even"},
                {"Rasalgethi", "informative"}, {"Achird", "friendly"}, {"Fenrir", "excitable"}, {"Puck", "upbeat"},
                {"Zephyr", "bright"}, {"Aoede", "breezy, soft (owner liked it, 18 Nov 2026)"}, {"Sulafat", "warm"},
                {"Vindemiatrix", "gentle"}, {"Achernar", "soft"}, {"Despina", "smooth"}};
        for (String[] x : v) {
            VOICE_CHARACTER.put(x[0], x[1]);
        }
    }
    public static final List<String> VOICES = List.copyOf(VOICE_CHARACTER.keySet());

    private final Llm llm;
    private final StylePresets presets;
    /** Model tried first for this agent's most important call (Claude, owner-funded and capped); Gemini behind it. */
    private final String storyModel;
    /** Model tried first for the script itself (owner A/B, 23 Nov 2026: OpenAI scripts preferred); blank means storyModel. */
    private final String scriptModel;

    public WriterAgent(Llm llm, StylePresets presets,
                 @org.springframework.beans.factory.annotation.Value("${projectv.models.story:}") String storyModel,
                 @org.springframework.beans.factory.annotation.Value("${projectv.models.script:}") String scriptModel) {
        this.llm = llm;
        this.presets = presets;
        this.storyModel = storyModel;
        this.scriptModel = scriptModel == null || scriptModel.isBlank() ? storyModel : scriptModel;
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
        // Claude first: it carries the research's corrections into the acts (A/B, 17 Nov 2026: concept 9 vs 7).
        return llm.call(videoId, "CONCEPT", system, user, Concept.class, storyModel);
    }

    public Script script(UUID videoId, String prompt, VideoPlan plan, Concept concept, List<Fact> facts, String ownerNotes) {
        StylePresets.Preset preset = presets.byIdOrDefault(plan.stylePreset());
        String lengthRule = switch (plan.narration()) {
            case "none" -> "There is NO narration. Write %d visual beats (spoken=false), each a vivid description of what the audience sees and feels."
                    .formatted(Math.max(6, plan.durationSec() / 8));
            case "sparse" -> "Narration is SPARSE and poetic: about %d words in total, in %d-%d short lines (spoken=true), leaving long musical silences. Add a few spoken=false visual beats where music alone should carry the moment."
                    .formatted(Math.round(plan.durationSec() * 0.75), Math.max(6, plan.durationSec() / 20), Math.max(8, plan.durationSec() / 12));
            // ~2.1 spoken words/s for TTS narration, minus room for breaths between lines and music-only moments.
            default -> "Narration is FULL but must leave breathing room: at most %d words in total, split into lines of 1-2 sentences (spoken=true). Never exceed this; the video cannot be longer than %d seconds."
                    .formatted(Math.round(plan.durationSec() * 2.1 * 0.78), plan.durationSec());
        };
        String system = """
                You are the head writer of a premium documentary studio. Style preset: %s (%s).
                Write the script as an ordered list of lines. %s
                Every factual claim must come from the research facts provided. Write in language "%s".
                Cast the narrator: pick voiceName from these voices (name: character) to suit this story's context, depth and mood: %s.
                Describe the overall reading style in voiceStyle as a short phrase of at most 8 words (e.g. "slow, hushed and awe-struck").
                Give every spoken line a delivery: at most 6 words on how to read THAT line as the story moves (e.g. "hushed, curious", "rising excitement", "slow, tender", "punchy, fast"), so the voice follows the emotional arc. Then write a one-paragraph
                musicBrief for the composer (mood, instruments, tempo feel, how it should build). No sound-effect cues in lines.
                Return only the JSON object.""".formatted(preset.name(), preset.description(), lengthRule, plan.language(),
                        VOICE_CHARACTER.entrySet().stream().map(e -> e.getKey() + ": " + e.getValue()).collect(Collectors.joining("; ")));
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
        return llm.call(videoId, "SCRIPT", system, user, Script.class, scriptModel);
    }

    static String factsText(List<Fact> facts) {
        if (facts == null || facts.isEmpty()) {
            return "(no research for this video)";
        }
        // Facts whose numbers weren't found on their cited page are marked so the writer treats them with care.
        return facts.stream().map(f -> "- " + f.text() + ("unconfirmed".equals(f.check())
                ? " [numbers not confirmed by the source page: avoid stating them, or soften]" : ""))
                .collect(Collectors.joining("\n"));
    }

    static String notes(String ownerNotes) {
        if (blank(ownerNotes)) {
            return "";
        }
        // Notes from the Inspector / Creative Director already say who rejected what and why.
        return ownerNotes.startsWith("The Inspector") ? ownerNotes + "\n"
                : "The owner rejected the previous version with these notes; follow them exactly:\n" + ownerNotes;
    }

    static boolean blank(String s) {
        return s == null || s.isBlank();
    }
}
