package com.projectv.planner;

import com.projectv.workflow.Stage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.client.ResponseEntity;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeoutException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * R1 Planner: free-text prompt -> {@link VideoPlan}. Uses Gemini when a key is configured; otherwise (or if the
 * call fails) a rule-based fallback, so the pipeline never blocks on the LLM.
 */
@Service
public class Planner {

    private static final Logger log = LoggerFactory.getLogger(Planner.class);

    private static final List<String> VISUAL_TYPES =
            List.of("ai_video", "ai_image_motion", "motion_graphics", "stock_footage", "text_cards", "data_counter");

    /** How a plan was produced, for the stage summary and the cost ledger. */
    public record Outcome(VideoPlan plan, List<Stage> stages, String source, Long totalTokens, String fallbackReason) {
    }

    private final ObjectProvider<ChatModel> chatModel;
    private final StylePresets presets;
    private final String apiKey;
    private final String modelName;
    private final long timeoutSeconds;
    private final String fallbacks;
    private final com.projectv.agents.OpenAiCompatChat nvidia;

    private record Out(VideoPlan plan, Long tokens) {
    }

    public Planner(ObjectProvider<ChatModel> chatModel, StylePresets presets, com.projectv.agents.OpenAiCompatChat nvidia,
                   @Value("${spring.ai.google.genai.api-key:}") String apiKey,
                   @Value("${spring.ai.google.genai.chat.options.model:}") String modelName,
                   @Value("${projectv.planner.timeout-seconds:90}") long timeoutSeconds,
                   @Value("${projectv.models.text-fallbacks:}") String fallbacks) {
        this.chatModel = chatModel;
        this.presets = presets;
        this.apiKey = apiKey;
        this.modelName = modelName;
        this.timeoutSeconds = timeoutSeconds;
        this.fallbacks = fallbacks;
        this.nvidia = nvidia;
    }

    public boolean llmConfigured() {
        return apiKey != null && !apiKey.isBlank() && chatModel.getIfAvailable() != null;
    }

    public String modelName() {
        return modelName;
    }

    public Outcome plan(String prompt, String ownerNotes) {
        if (!llmConfigured()) {
            return fallback(prompt, "No GEMINI_API_KEY configured");
        }
        try {
            // Hard time limit per model plus the ordered fallback list (Work X): a hung or retired model moves on to
            // the next one instead of blocking or failing the video; the rule-based plan is the last resort.
            com.projectv.agents.ModelFallback.Attempt<Out> attempt = com.projectv.agents.ModelFallback.run(
                    com.projectv.agents.ModelFallback.models(modelName, fallbacks), timeoutSeconds, "PLAN", m -> {
                        if (com.projectv.agents.OpenAiCompatChat.handles(m)) {
                            var n = nvidia.call(m, systemPrompt(), userPrompt(prompt, ownerNotes), VideoPlan.class);
                            return new Out(n.entity(), n.totalTokens());
                        }
                        ResponseEntity<ChatResponse, VideoPlan> response = ChatClient.create(chatModel.getObject())
                                .prompt()
                                .options(org.springframework.ai.chat.prompt.ChatOptions.builder().model(m))
                                .system(systemPrompt())
                                .user(userPrompt(prompt, ownerNotes))
                                .call()
                                .responseEntity(VideoPlan.class);
                        Long tokens = null;
                        if (response.response() != null && response.response().getMetadata() != null
                                && response.response().getMetadata().getUsage() != null) {
                            Integer total = response.response().getMetadata().getUsage().getTotalTokens();
                            tokens = total == null ? null : total.longValue();
                        }
                        return new Out(response.entity(), tokens);
                    });
            String usedModel = attempt.model();
            VideoPlan plan = normalise(attempt.value().plan(), prompt);
            String source = com.projectv.agents.OpenAiCompatChat.handles(usedModel) ? usedModel : "gemini:" + usedModel;
            return new Outcome(plan, stagesFor(plan), source, attempt.value().tokens(), null);
        } catch (TimeoutException e) {
            log.warn("Gemini planning timed out after {}s, using rule-based fallback", timeoutSeconds);
            return fallback(prompt, "No Gemini model answered within " + timeoutSeconds + "s");
        } catch (Exception e) {
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            // Never log the key; the exception message from the client does not contain it.
            Throwable cause = e instanceof ExecutionException && e.getCause() != null ? e.getCause() : e;
            log.warn("Gemini planning failed, using rule-based fallback: {}", cause.toString());
            return fallback(prompt, "Gemini call failed: " + cause.getClass().getSimpleName());
        }
    }

    private String systemPrompt() {
        StringBuilder sb = new StringBuilder("""
                You are the Planner of an AI video studio. From the user's prompt, decide everything the video needs.
                Return ONLY the JSON object for the plan. Rules:
                - format: "long_form" or "short". Shorts/Reels are vertical 9:16 and 15-60 seconds.
                - durationSec: respect any length in the prompt; otherwise use the chosen preset's default.
                - aspectRatio: "16:9" for long_form, "9:16" for short, unless the prompt says otherwise.
                - outputResolution: "2160p" for long_form, "1080p" for short.
                - stylePreset: one of the preset ids below; pick the best fit for the prompt.
                - language: ISO 639-1 code of the narration language the user wants (default "en").
                - needsResearch: true when the video makes factual claims (science, history, tech, finance, how-things-work).
                - narration: "full", "sparse" or "none". voice: a short description of the ideal narrator voice, or "" when none.
                - music: "lead" (edit is cut to the music), "support" or "none". soundDesign: true unless clearly unwanted.
                - visualTypes: choose from %s. Prefer real moving footage (ai_video); never plan a slideshow of panned stills.
                - shortsCount: how many 30-60 s vertical cut-downs to make (0 for a short; usually 3-5 for long_form).
                - mustInclude: concrete items the user explicitly asked for.
                - title: a strong working title. summary: 1-2 sentences. rationale: 1-2 sentences on why these choices.
                Style presets:
                """.formatted(VISUAL_TYPES));
        for (StylePresets.Preset p : presets.all()) {
            sb.append("- ").append(p.id()).append(" (").append(p.name()).append("): ")
                    .append(p.description()).append(" Defaults: ").append(p.defaults()).append('\n');
        }
        return sb.toString();
    }

    private static String userPrompt(String prompt, String ownerNotes) {
        String base = "Prompt:\n" + prompt;
        if (ownerNotes != null && !ownerNotes.isBlank()) {
            base += "\n\nThe owner rejected the previous plan with these notes; follow them:\n" + ownerNotes;
        }
        return base;
    }

    /** Fill gaps and clamp values so later stages can trust the plan regardless of where it came from. */
    VideoPlan normalise(VideoPlan p, String prompt) {
        StylePresets.Preset preset = presets.byIdOrDefault(p.stylePreset());
        Map<String, Object> d = preset.defaults();
        String format = oneOf(p.format(), List.of("long_form", "short"), String.valueOf(d.getOrDefault("format", "long_form")));
        boolean isShort = format.equals("short");
        int duration = p.durationSec() > 0 ? p.durationSec() : ((Number) d.getOrDefault("durationSec", 480)).intValue();
        duration = isShort ? Math.max(10, Math.min(duration, 90)) : Math.max(30, Math.min(duration, 3600));
        List<String> visuals = p.visualTypes() == null ? List.of() : p.visualTypes().stream().filter(VISUAL_TYPES::contains).toList();
        if (visuals.isEmpty()) {
            visuals = List.of("ai_video");
        }
        String narration = oneOf(p.narration(), List.of("full", "sparse", "none"), String.valueOf(d.getOrDefault("narration", "full")));
        return new VideoPlan(
                blankTo(p.title(), shortTitle(prompt)),
                blankTo(p.summary(), prompt),
                format,
                duration,
                oneOf(p.aspectRatio(), List.of("16:9", "9:16", "1:1", "4:5"), isShort ? "9:16" : "16:9"),
                oneOf(p.outputResolution(), List.of("2160p", "1080p"), isShort ? "1080p" : "2160p"),
                preset.id(),
                blankTo(p.tone(), "engaging"),
                blankTo(p.language(), "en").toLowerCase(Locale.ROOT),
                p.needsResearch(),
                narration,
                narration.equals("none") ? "" : blankTo(p.voice(), "warm, clear documentary narrator"),
                oneOf(p.music(), List.of("lead", "support", "none"), String.valueOf(d.getOrDefault("music", "support"))),
                p.soundDesign(),
                visuals,
                isShort ? 0 : Math.max(0, Math.min(p.shortsCount(), 10)),
                p.mustInclude() == null ? List.of() : p.mustInclude(),
                blankTo(p.rationale(), ""));
    }

    /** Which stages this plan needs. PLAN itself always runs. */
    public static List<Stage> stagesFor(VideoPlan plan) {
        List<Stage> stages = new ArrayList<>(List.of(Stage.values()));
        if (!plan.needsResearch()) {
            stages.remove(Stage.RESEARCH);
        }
        if ("none".equals(plan.music())) {
            stages.remove(Stage.MUSIC);
        }
        if ("none".equals(plan.narration())) {
            stages.remove(Stage.NARRATION);
        }
        return stages;
    }

    // ---------------------------------------------------------------- rule-based fallback

    private static final Pattern DURATION = Pattern.compile("(\\d{1,3})\\s*(seconds?|secs?|s|minutes?|mins?|m)\\b",
            Pattern.CASE_INSENSITIVE);

    Outcome fallback(String prompt, String reason) {
        String p = prompt.toLowerCase(Locale.ROOT);
        String preset;
        if (containsAny(p, "short", "reel", "tiktok", "vertical")) {
            preset = "shorts_punchy";
        } else if (containsAny(p, "ambient", "relax", "no narration", "calm", "sleep", "meditat")) {
            preset = "ambient_visual";
        } else if (containsAny(p, "timelapse", "time-lapse", "universe", "cosmos", "million years", "billion years")) {
            preset = "cinematic_awe";
        } else if (containsAny(p, "how ", "explain", "what is", "why ")) {
            preset = "explainer_clean";
        } else {
            preset = "documentary_story";
        }
        int duration = 0;
        Matcher m = DURATION.matcher(p);
        if (m.find()) {
            int n = Integer.parseInt(m.group(1));
            duration = m.group(2).toLowerCase(Locale.ROOT).startsWith("m") ? n * 60 : n;
        }
        String language = "en";
        if (p.contains("hindi")) {
            language = "hi";
        } else if (p.contains("tamil")) {
            language = "ta";
        } else if (p.contains("spanish")) {
            language = "es";
        }
        boolean factual = !preset.equals("ambient_visual");
        VideoPlan raw = new VideoPlan(shortTitle(prompt), prompt, null, duration, null, null, preset, null, language,
                factual, null, null, null, true, List.of("ai_video", "motion_graphics"), preset.equals("shorts_punchy") ? 0 : 3,
                List.of(), "Rule-based plan (" + reason + "). Connect Gemini for a smarter plan.");
        VideoPlan plan = normalise(raw, prompt);
        return new Outcome(plan, stagesFor(plan), "rules", null, reason);
    }

    private static boolean containsAny(String text, String... needles) {
        for (String n : needles) {
            if (text.contains(n)) {
                return true;
            }
        }
        return false;
    }

    private static String oneOf(String value, List<String> allowed, String dflt) {
        return value != null && allowed.contains(value) ? value : dflt;
    }

    private static String blankTo(String value, String dflt) {
        return value == null || value.isBlank() ? dflt : value;
    }

    private static String shortTitle(String prompt) {
        String t = prompt.strip().replaceAll("\\s+", " ");
        return t.length() <= 70 ? t : t.substring(0, 67) + "...";
    }
}
