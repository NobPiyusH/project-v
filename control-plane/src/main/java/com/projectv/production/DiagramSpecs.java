package com.projectv.production;

import com.projectv.agents.Llm;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * The Director's diagram shots: turns a narration line into a scene spec (JSON, drawn by workers/diagram/render.py).
 * One LLM call through the studio's text client, validated here; an invalid spec gets ONE repair call that shows the
 * model its errors; still invalid (or the model fails) = the clear animated text-card spec, never a panned still.
 * The Python worker validates again before drawing (defence in depth, it is the authority on the schema).
 * Off unless {@code projectv.video.diagram-lane=true}.
 */
@Component
public class DiagramSpecs {

    private static final Logger log = LoggerFactory.getLogger(DiagramSpecs.class);

    /** What the model returns. Elements and steps stay loose maps: the schema is in the prompt and in {@link #validate}. */
    public record SceneSpec(String title, Double duration, List<Map<String, Object>> elements, List<Map<String, Object>> steps) {
    }

    static final Set<String> TYPES = Set.of("box", "layer", "circle", "line", "arrow", "beam", "text", "particles", "dots_grid",
            "rgb_mix", "callout");
    static final Set<String> ACTIONS = Set.of("show", "hide", "move", "color", "scale", "pulse", "draw", "flow", "mix");
    static final Map<String, List<String>> NUMS = Map.of("box", List.of("x", "y", "w", "h"), "layer", List.of("x", "y", "w", "h"),
            "circle", List.of("x", "y", "r"), "line", List.of("x1", "y1", "x2", "y2"), "arrow", List.of("x1", "y1", "x2", "y2"),
            "beam", List.of("x1", "y1", "x2", "y2"), "text", List.of("x", "y"), "particles", List.of("x", "y", "count"),
            "dots_grid", List.of("x", "y", "cols", "rows", "dx", "dy"), "rgb_mix", List.of("x", "y", "r"));

    static final String SYSTEM = """
            You draw one animated explainer diagram (16:9, dark background, clean labelled style) as a JSON scene spec.
            Return ONLY this JSON object: {"title": str, "duration": 5..12, "elements": [...], "steps": [...]}.
            Coordinates: x -7..7 (left to right), y -4..4 (bottom to top). Keep y between -3.3 and 3.2 for parts (a title
            sits at y=3.5, a caption at y=-3.7). Do not overlap labels. Text must be short (under 40 characters per line).
            Colours: a name (cyan blue violet orange red green yellow white text muted pure_red pure_green pure_blue) or #RRGGBB.
            Element types (every element has a unique "id" and optional "color"):
              box {x,y,w,h,text?,size?} | layer {x,y,w,h,skew?,text?} (a slanted plate, for stacks) | circle {x,y,r,text?}
              line/arrow/beam {x1,y1,x2,y2,width?,dashed?} | text {x,y,text,size?} | particles {x,y,count,spread?}
              dots_grid {x,y,cols,rows,dx,dy,r?} | rgb_mix {x,y,r} (red/green/blue circles that overlap) | callout {x,y,text,tx,ty}
            Steps run on a timeline in seconds, each {"at","dur","do","target", ...}; every step must end by the duration:
              show (optional "from": up|down|left|right) | hide | draw (outline grows; box/layer/circle/line/arrow/beam)
              move {dx,dy or x,y} | color {to} | scale {to} | pulse {amount?} | flow {count?,loops?,color?,reverse?} (dots travel
              along a line/arrow/beam) | mix (rgb_mix only). Optional "ease": linear smooth ease_in ease_out ease_in_out bounce.
            Elements start visible unless a show/draw step targets them. Reveal the parts in the order the narration
            explains them, then animate what happens (a beam flows, a layer changes colour). Be technically accurate: only
            draw parts and flows that really exist. Use at most 25 elements and 30 steps.""";

    private final Llm llm;
    private final ObjectMapper json;
    private final boolean enabled;

    public DiagramSpecs(Llm llm, ObjectMapper json, @Value("${projectv.video.diagram-lane:false}") boolean enabled) {
        this.llm = llm;
        this.json = json;
        this.enabled = enabled;
    }

    public boolean enabled() {
        return enabled;
    }

    /**
     * The spec for one diagram shot as a map ready for prompt_json. {@code fallback=true} in the result means a text card
     * will be drawn (title + lines); the reason is in {@code fallbackReason}.
     */
    public Map<String, Object> specFor(UUID videoId, String videoTitle, String narrationLine, String description, double seconds) {
        double dur = Math.max(5.0, Math.min(12.0, Math.ceil(seconds)));
        String user = "Video: " + videoTitle + "\nNarration line this shot explains: " + narrationLine
                + "\nWhat the shot must show: " + description + "\nDuration: " + dur + " seconds.";
        String lastErr = null;
        Map<String, Object> spec = null;
        List<String> errs = List.of();
        for (int attempt = 0; attempt < 2; attempt++) { // the call, then ONE repair
            try {
                String prompt = attempt == 0 ? user : user + "\n\nYour previous spec was invalid:\n" + json.writeValueAsString(spec)
                        + "\nProblems:\n- " + String.join("\n- ", errs) + "\nReturn the corrected JSON object only.";
                SceneSpec s = llm.call(videoId, attempt == 0 ? "DIAGRAM_SPEC" : "DIAGRAM_SPEC_REPAIR", SYSTEM, prompt, SceneSpec.class);
                spec = json.convertValue(s, new tools.jackson.core.type.TypeReference<LinkedHashMap<String, Object>>() {
                });
                spec.put("duration", dur);
                spec.putIfAbsent("version", 1);
                errs = validate(spec);
                if (errs.isEmpty()) {
                    return spec;
                }
                lastErr = String.join("; ", errs.subList(0, Math.min(3, errs.size())));
            } catch (RuntimeException e) {
                lastErr = e.getClass().getSimpleName() + ": " + e.getMessage();
                log.warn("Diagram spec call {} failed: {}", attempt + 1, lastErr);
                break; // a failing model call is not repaired by asking again with the same text
            }
        }
        return fallback(narrationLine, description, dur, lastErr);
    }

    /** Title + lines for the worker's animated text card (the worker builds the card; an invalid spec is its trigger). */
    static Map<String, Object> fallback(String line, String description, double dur, String why) {
        Map<String, Object> m = new LinkedHashMap<>();
        String title = description == null || description.isBlank() ? "Diagram" : description.strip();
        m.put("title", title.length() > 60 ? title.substring(0, 57) + "..." : title);
        m.put("duration", dur);
        m.put("fallback", true);
        m.put("fallbackReason", why == null ? "no spec" : why);
        m.put("lines", line == null || line.isBlank() ? List.of() : List.of(line.strip().length() > 90 ? line.strip().substring(0, 87) + "..." : line.strip()));
        return m;
    }

    /** Structural check (same rules as workers/diagram/spec.py validate_spec, the part a model gets wrong most). */
    static List<String> validate(Map<String, Object> spec) {
        List<String> errs = new ArrayList<>();
        Object d = spec.get("duration");
        double dur = d instanceof Number n ? n.doubleValue() : -1;
        if (dur < 5 || dur > 12) {
            errs.add("duration must be a number between 5 and 12");
            dur = 12;
        }
        if (!(spec.get("elements") instanceof List<?> els) || els.isEmpty()) {
            errs.add("elements must be a non-empty list");
            return errs;
        }
        if (els.size() > 60) {
            errs.add("at most 60 elements");
        }
        Map<String, String> ids = new LinkedHashMap<>();
        for (int i = 0; i < els.size(); i++) {
            if (!(els.get(i) instanceof Map<?, ?> e)) {
                errs.add("elements[" + i + "] must be an object");
                continue;
            }
            Object id = e.get("id");
            Object type = e.get("type");
            if (!(id instanceof String sid) || sid.isBlank()) {
                errs.add("elements[" + i + "].id must be a non-empty string");
            } else if (ids.putIfAbsent(sid, String.valueOf(type)) != null) {
                errs.add("elements[" + i + "].id '" + sid + "' is duplicated");
            }
            if (!(type instanceof String t) || !TYPES.contains(t)) {
                errs.add("elements[" + i + "].type must be one of " + new java.util.TreeSet<>(TYPES));
                continue;
            }
            List<String> need = new ArrayList<>(NUMS.getOrDefault(t, t.equals("callout") ? List.of("x", "y", "tx", "ty") : List.of()));
            for (String f : need) {
                if (!(e.get(f) instanceof Number n)) {
                    errs.add("elements[" + i + "] (" + t + ") needs numeric '" + f + "'");
                } else if (f.startsWith("x") || f.equals("tx")) {
                    if (Math.abs(n.doubleValue()) > 7.11) {
                        errs.add("elements[" + i + "]." + f + "=" + n + " is outside the frame (|x| <= 7.1)");
                    }
                } else if ((f.startsWith("y") || f.equals("ty")) && Math.abs(n.doubleValue()) > 4.01) {
                    errs.add("elements[" + i + "]." + f + "=" + n + " is outside the frame (|y| <= 4)");
                }
            }
            if ((t.equals("text") || t.equals("callout")) && !(e.get("text") instanceof String s && !s.isBlank())) {
                errs.add("elements[" + i + "] (" + t + ") needs text");
            }
        }
        Object st = spec.get("steps");
        if (st != null && !(st instanceof List<?>)) {
            errs.add("steps must be a list");
        } else if (st instanceof List<?> steps) {
            if (steps.size() > 80) {
                errs.add("at most 80 steps");
            }
            for (int i = 0; i < steps.size(); i++) {
                if (!(steps.get(i) instanceof Map<?, ?> s)) {
                    errs.add("steps[" + i + "] must be an object");
                    continue;
                }
                if (!(s.get("do") instanceof String act) || !ACTIONS.contains(act)) {
                    errs.add("steps[" + i + "].do '" + s.get("do") + "' is not allowed; use one of " + new java.util.TreeSet<>(ACTIONS));
                    continue;
                }
                if (!(s.get("at") instanceof Number at) || at.doubleValue() < 0 || !(s.get("dur") instanceof Number du) || du.doubleValue() <= 0) {
                    errs.add("steps[" + i + "] needs at >= 0 and dur > 0 (seconds)");
                } else if (at.doubleValue() + du.doubleValue() > dur + 0.01) {
                    errs.add("steps[" + i + "] ends after the duration " + dur + "s");
                }
                String target = String.valueOf(s.get("target"));
                String tt = ids.get(target);
                if (tt == null) {
                    errs.add("steps[" + i + "].target '" + target + "' is not an element id");
                } else if (act.equals("flow") && !Set.of("beam", "line", "arrow").contains(tt)) {
                    errs.add("steps[" + i + "]: flow works only on beam/line/arrow");
                } else if (act.equals("mix") && !tt.equals("rgb_mix")) {
                    errs.add("steps[" + i + "]: mix works only on rgb_mix");
                }
            }
        }
        return errs;
    }
}
