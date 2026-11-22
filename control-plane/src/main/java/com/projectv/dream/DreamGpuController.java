package com.projectv.dream;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/api")
public class DreamGpuController {

    private final DreamGpu gpu;

    public DreamGpuController(DreamGpu gpu) {
        this.gpu = gpu;
    }

    /** The media worker pings this while it waits for the pod or runs jobs, so an active pod is never "idle" to the watchdog. */
    @PostMapping("/internal/gpu/{dreamId}/heartbeat")
    public Map<String, Object> heartbeat(@PathVariable UUID dreamId) {
        return Map.of("ok", true, "pod", gpu.heartbeat(dreamId).name());
    }

    /** The worker found the pod gone mid-step: rent a replacement (refused once the Short's rental was released). */
    @PostMapping("/internal/gpu/{dreamId}/replace")
    public Map<String, Object> replace(@PathVariable UUID dreamId, @RequestBody(required = false) ReplaceBody body) {
        var s = body == null ? gpu.replace(dreamId) : gpu.replace(dreamId, body.unreadyUrl(), body.why());
        return Map.of("url", s.url(), "readyTimeoutSeconds", s.readyTimeoutSeconds());
    }

    /** The media worker forwards the pod's boot timeline (its /health "timing") once, when the pod first answers ready. */
    @PostMapping("/internal/gpu/{dreamId}/boot-timing")
    public Map<String, Object> bootTiming(@PathVariable UUID dreamId, @RequestBody Map<String, Object> body) {
        if (body.get("timing") instanceof Map<?, ?> t) {
            @SuppressWarnings("unchecked") Map<String, Object> timing = (Map<String, Object>) t;
            gpu.reportBoot(dreamId, timing);
        }
        return Map.of("ok", true);
    }

    /** unreadyUrl: the pod address that never became ready (null = the pod vanished). */
    public record ReplaceBody(String unreadyUrl, String why) {
    }

    public record PickBody(String mode, String choice) {
    }

    /** Every GPU option rentable now (and the blocked ones with the reason), cheapest first: what a Manual pick chooses from. */
    @GetMapping("/gpu/broker/options")
    public java.util.List<com.projectv.gpu.broker.GpuBroker.Option> options() {
        return gpu.options();
    }

    @GetMapping("/dream/{dreamId}/gpu")
    public Map<String, Object> pick(@PathVariable UUID dreamId) {
        var p = gpu.pick(dreamId);
        var out = new java.util.LinkedHashMap<String, Object>();
        out.put("mode", p.mode());
        out.put("choice", p.choice() == null ? "" : p.choice());
        out.put("rented", gpu.isOpen(dreamId));
        out.put("estimate", gpu.rentalEstimate(dreamId)); // the same hours the daily-cap check uses before renting
        out.put("usersOnPod", gpu.usersOnPod()); // the ONE shared pod: how many Shorts are using it
        out.put("hold", gpu.holdManual(dreamId) ? "manual" : "auto");
        out.put("idleMinutes", gpu.idleMinutes(dreamId));
        out.put("offInSeconds", gpu.offInSeconds(dreamId, java.time.Instant.now())); // null unless Manual and a pod is on
        out.put("boot", gpu.bootTiming(dreamId).orElse(null)); // where the pod's boot time went (null = no pod / no data yet)
        return out;
    }

    public record HoldBody(String hold, Integer idleMinutes) {
    }

    /** GPU power: Auto (the studio turns the pod off when the GPU steps are over) or Manual (it stays on until turned off or idle). */
    @PostMapping("/dream/{dreamId}/gpu/hold")
    public Map<String, Object> setHold(@PathVariable UUID dreamId, @org.springframework.web.bind.annotation.RequestBody HoldBody body) {
        gpu.setHold(dreamId, body.hold(), body.idleMinutes());
        return pick(dreamId);
    }

    /** "Turn off GPU": ends this Short's rental now. */
    @PostMapping("/dream/{dreamId}/gpu/off")
    public Map<String, Object> turnOff(@PathVariable UUID dreamId) {
        gpu.turnOff(dreamId);
        return pick(dreamId);
    }

    /** Auto (the studio picks, learning from your past picks) or Manual (you pick one option from the list). */
    @PostMapping("/dream/{dreamId}/gpu")
    public Map<String, Object> setPick(@PathVariable UUID dreamId, @org.springframework.web.bind.annotation.RequestBody PickBody body) {
        gpu.setPick(dreamId, body.mode(), body.choice());
        return pick(dreamId);
    }

    @GetMapping("/gpu/broker")
    public Map<String, Object> status() {
        return gpu.enabled() ? Map.of("enabled", true, "today", gpu.today(), "providers", gpu.providerStatuses())
                : Map.of("enabled", false);
    }

    /** Every provider: configured / preflight ok / proven (only proven ones are searched by Auto). */
    @GetMapping("/gpu/broker/providers")
    public java.util.List<Map<String, Object>> providers() {
        return gpu.providerStatuses();
    }

    /** Free pre-check, rents nothing. */
    @PostMapping("/gpu/broker/providers/{provider}/preflight")
    public Map<String, Object> preflight(@PathVariable String provider) {
        var r = gpu.preflight(provider);
        return Map.of("provider", provider, "ok", r.ok(), "checks", r.checks());
    }

    /**
     * The canary: ONE real, tiny rental on this provider (its cheapest GPU, ends as soon as the worker answers /health).
     * Spends real money, so it needs {@code approve=true}, which only the owner sends.
     */
    @PostMapping("/gpu/broker/providers/{provider}/canary")
    public Map<String, Object> canary(@PathVariable String provider,
                                      @org.springframework.web.bind.annotation.RequestParam(defaultValue = "false") boolean approve,
                                      @org.springframework.web.bind.annotation.RequestParam(defaultValue = "0.60") double maxUsd) {
        if (!approve) {
            throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.BAD_REQUEST,
                    "The canary rents a real GPU for a few minutes. Send approve=true to confirm.");
        }
        return gpu.canary(provider, maxUsd);
    }
}
