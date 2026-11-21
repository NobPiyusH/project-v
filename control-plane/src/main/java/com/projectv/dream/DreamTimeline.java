package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamStep;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * The Dream Short page's step timeline and live log, worked out from the "status" events {@link DreamService#status}
 * records on every change. Work time counts only the RUNNING stretches: waiting for the owner or a cost OK is not work.
 */
final class DreamTimeline {

    /** Fewer finished Shorts than this and the page says "no estimate yet". */
    static final int MIN_SAMPLES = 2;
    static final int LOG_SIZE = 20;

    record Event(String step, String kind, String notes, Map<String, ?> data, Instant at) {
        String status() {
            return data == null || data.get("status") == null ? null : data.get("status").toString();
        }
    }

    record Cost(double inr, Instant at) {
    }

    record StepTime(String step, String status, Instant startedAt, Instant finishedAt, long workSeconds, double costInr,
                    Long typicalSeconds) {
    }

    private static final class Acc {
        String status = "PENDING";
        Instant start;
        Instant end;
        long workMillis;
        double cost;
    }

    private DreamTimeline() {
    }

    /** Per step in DREAM_STEPS order; {@code typical} = median work seconds per step (may lack a step). */
    static List<StepTime> steps(List<Event> events, List<Cost> costs, Instant now, Map<String, Long> typical) {
        Map<DreamStep, Acc> acc = walk(events, now);
        // Each cost goes to the step that was active when it was booked (reruns add up).
        List<Event> statuses = events.stream().filter(e -> "status".equals(e.kind()) && step(e) != null).toList();
        for (Cost c : costs) {
            DreamStep at = null;
            for (Event e : statuses) {
                if (e.at().isAfter(c.at())) {
                    break;
                }
                at = step(e);
            }
            if (at != null) {
                acc.get(at).cost += c.inr();
            }
        }
        List<StepTime> out = new ArrayList<>();
        for (DreamStep s : DreamStep.values()) {
            Acc a = acc.get(s);
            out.add(new StepTime(s.name(), a.status, a.start, a.end, a.workMillis / 1000, Math.round(a.cost * 100) / 100.0,
                    typical.get(s.name())));
        }
        return out;
    }

    /** Work seconds of every step that finished, for the median of past Shorts. */
    static Map<String, Long> finishedWork(List<Event> events) {
        Map<String, Long> out = new LinkedHashMap<>();
        walk(events, null).forEach((s, a) -> {
            if (a.end != null && "DONE".equals(a.status) && a.workMillis > 0) {
                out.put(s.name(), a.workMillis / 1000);
            }
        });
        return out;
    }

    /** Median work seconds per step over past Shorts; a step with fewer than MIN_SAMPLES samples is left out. */
    static Map<String, Long> typical(List<Map<String, Long>> pastShorts) {
        Map<String, Long> out = new LinkedHashMap<>();
        for (DreamStep s : DreamStep.values()) {
            List<Double> xs = pastShorts.stream().map(m -> m.get(s.name())).filter(Objects::nonNull)
                    .map(Long::doubleValue).toList();
            if (xs.size() >= MIN_SAMPLES) {
                out.put(s.name(), Math.round(DreamEvidence.median(xs)));
            }
        }
        return out;
    }

    private static Map<DreamStep, Acc> walk(List<Event> events, Instant now) {
        Map<DreamStep, Acc> acc = new EnumMap<>(DreamStep.class);
        for (DreamStep s : DreamStep.values()) {
            acc.put(s, new Acc());
        }
        List<Event> st = events.stream().filter(e -> "status".equals(e.kind()) && step(e) != null && e.status() != null).toList();
        DreamStep prev = null;
        for (int i = 0; i < st.size(); i++) {
            Event e = st.get(i);
            DreamStep s = step(e);
            String status = e.status();
            Acc a = acc.get(s);
            if (prev != null && prev != s) {
                close(acc.get(prev), e.at(), "DONE");
            }
            if ("RUNNING".equals(status) && a.end != null) {
                acc.put(s, a = new Acc()); // run again (a redo from this step): the newest run counts
            }
            if (a.start == null) {
                a.start = e.at();
            }
            switch (status) {
                case "COMPLETED" -> close(a, e.at(), "DONE");
                case "CANCELLED" -> close(a, e.at(), "CANCELLED");
                default -> a.status = status;
            }
            Instant next = i + 1 < st.size() ? st.get(i + 1).at() : now;
            if ("RUNNING".equals(status) && next != null && next.isAfter(e.at())) {
                a.workMillis += Duration.between(e.at(), next).toMillis();
            }
            prev = s;
        }
        return acc;
    }

    private static void close(Acc a, Instant at, String status) {
        if (a.end == null) {
            a.end = at;
            a.status = status;
        }
    }

    private static DreamStep step(Event e) {
        try {
            return e.step() == null ? null : DreamStep.valueOf(e.step());
        } catch (IllegalArgumentException ignored) {
            return null;
        }
    }

    // ------------------------------------------------------------------------------------------- live log

    record LogLine(Instant at, String text) {
    }

    /** The last LOG_SIZE events in plain words, newest first. */
    static List<LogLine> log(List<Event> events) {
        List<LogLine> out = new ArrayList<>();
        for (int i = events.size() - 1; i >= 0 && out.size() < LOG_SIZE; i--) {
            String text = describe(events.get(i));
            if (text != null) {
                out.add(new LogLine(events.get(i).at(), text));
            }
        }
        return out;
    }

    static String describe(Event e) {
        String step = e.step() == null ? "" : name(e.step());
        String notes = e.notes() == null ? "" : " — " + (e.notes().length() > 160 ? e.notes().substring(0, 160) + "…" : e.notes());
        return switch (e.kind()) {
            case "status" -> switch (Objects.toString(e.status(), "")) {
                case "RUNNING" -> step + " started" + notes;
                case "WAITING" -> step + " is waiting for you" + notes;
                case "WAITING_REVIEW" -> step + " is waiting for your review (goes on by itself after the window)";
                case "WAITING_COST" -> step + " is waiting for your OK on the cost" + notes;
                case "WAITING_BATCH" -> step + " is waiting its turn on the shared GPU" + notes;
                case "FAILED_WAITING" -> null; // the "failure" event says it
                case "COMPLETED" -> "Short finished";
                case "CANCELLED" -> "Short cancelled (at " + step + ")";
                default -> step + ": " + e.status();
            };
            case "create" -> "Short created" + notes;
            case "rerun" -> "Redo from " + step + " started";
            case "approve" -> "You approved " + step;
            case "redo" -> "You asked to redo " + step + notes;
            case "mode" -> step + " mode set to " + (e.data() == null ? "?" : e.data().get("mode"));
            case "retry" -> "Retry requested";
            case "failure" -> step + " failed" + notes;
            case "stop" -> "Stop requested: clips stop after the current clip finishes";
            case "cancel" -> e.data() != null && Boolean.TRUE.equals(e.data().get("afterClip"))
                    ? "Cancel requested: it will apply after the current clip finishes"
                    : e.data() != null && Boolean.TRUE.equals(e.data().get("alreadyEnded"))
                    ? "Cancel requested: the Short had already ended"
                    : "Cancel requested";
            case "cost" -> step + " cost ₹" + (e.data() == null ? "?" : e.data().get("actualInr"))
                    + (e.data() == null ? "" : " (estimate ₹" + e.data().get("estimateInr") + ")");
            case "angle" -> "You picked Scout angle " + (e.data() == null ? "?" : e.data().get("angle"));
            case "editing" -> "Editing switches changed";
            default -> (step.isEmpty() ? "" : step + ": ") + e.kind() + notes;
        };
    }

    private static String name(String step) {
        return step.charAt(0) + step.substring(1).toLowerCase();
    }
}
