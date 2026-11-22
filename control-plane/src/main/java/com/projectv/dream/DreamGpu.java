package com.projectv.dream;

import com.projectv.gpu.broker.GpuBroker;
import com.projectv.gpu.broker.GpuProvider;
import com.projectv.gpu.broker.PodBoot;
import com.projectv.gpu.broker.PreferenceStore;
import com.projectv.ledger.Pricing;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.UUID;

/**
 * Dream Shorts' door to the GPU broker (docs/plans/m3-gpu-router.md). Off unless {@code projectv.dream.gpu-provider=broker};
 * with the default {@code modal} nothing here is called. One rented pod per Short: warmed at the writing step so the weights
 * download overlaps it, reused for every picture and clip, released when the GPU steps are over or the Short ends.
 * The rental's whole time is booked as one CostEntry when it is released (no per-job entries: they would double count).
 */
@Component
public class DreamGpu {

    private static final Logger log = LoggerFactory.getLogger(DreamGpu.class);

    /** Where the Short's worker listens and how long to wait for it. */
    public record Session(String url, int readyTimeoutSeconds) {
    }

    /** Where RunPod mounts a network/global volume by default (not yet confirmed on a live pod: see the runbook). */
    static final String VOLUME_MOUNT = "/workspace";

    private final GpuBroker broker;
    private final org.springframework.jdbc.core.simple.JdbcClient jdbc;
    private final PreferenceStore prefs;
    private final Pricing pricing;
    private final boolean enabled;
    private final String workerFile;
    private final String image;
    private final String volumeId;
    private final int readyTimeoutMinutes;
    private final int port;
    private final double secondsPerPicture;
    private final double secondsPer81Frames;
    private final double startSeconds;
    private final double worstCaseHourlyUsd;
    private final RentalEstimate estimate;

    /** Tests and older callers: the default rental estimate. */
    public DreamGpu(GpuBroker broker, org.springframework.jdbc.core.simple.JdbcClient jdbc, PreferenceStore prefs, Pricing pricing,
                    String provider, String workerFile, String image, String volumeId, int readyTimeoutMinutes, int port,
                    double secondsPerPicture, double secondsPer81Frames, double startSeconds, double worstCaseHourlyUsd) {
        this(broker, jdbc, prefs, pricing, provider, workerFile, image, volumeId, readyTimeoutMinutes, port, secondsPerPicture,
                secondsPer81Frames, startSeconds, worstCaseHourlyUsd, RentalEstimate.DEFAULT);
    }

    @org.springframework.beans.factory.annotation.Autowired
    public DreamGpu(GpuBroker broker, org.springframework.jdbc.core.simple.JdbcClient jdbc, PreferenceStore prefs, Pricing pricing,
                    @Value("${projectv.dream.gpu-provider:modal}") String provider,
                    @Value("${projectv.gpu.pod-worker-file:workers/pod/server.py}") String workerFile,
                    @Value("${projectv.gpu.pod-image:runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404}") String image,
                    @Value("${projectv.gpu.pod-volume-id:}") String volumeId,
                    @Value("${projectv.gpu.pod-ready-minutes:30}") int readyTimeoutMinutes,
                    @Value("${projectv.gpu.pod-port:8000}") int port,
                    @Value("${projectv.gpu.seconds-per-picture:10}") double secondsPerPicture,
                    @Value("${projectv.gpu.seconds-per-81-frames:76}") double secondsPer81Frames,
                    @Value("${projectv.gpu.pod-start-seconds:900}") double startSeconds,
                    @Value("${projectv.gpu.max-hourly-usd:2.0}") double worstCaseHourlyUsd,
                    RentalEstimate estimate) {
        if (!"modal".equalsIgnoreCase(provider) && !"broker".equalsIgnoreCase(provider)) {
            throw new IllegalArgumentException("projectv.dream.gpu-provider must be modal or broker, not '" + provider + "'");
        }
        this.broker = broker;
        this.jdbc = jdbc;
        this.prefs = prefs;
        this.pricing = pricing;
        this.enabled = "broker".equalsIgnoreCase(provider);
        this.workerFile = workerFile;
        this.image = image;
        this.volumeId = volumeId == null || volumeId.isBlank() ? null : volumeId;
        this.readyTimeoutMinutes = readyTimeoutMinutes;
        this.port = port;
        this.secondsPerPicture = secondsPerPicture;
        this.secondsPer81Frames = secondsPer81Frames;
        this.startSeconds = startSeconds;
        this.worstCaseHourlyUsd = worstCaseHourlyUsd;
        this.estimate = estimate;
    }

    /** Expected hours of a rental for this Short's own work (the daily-cap check and the dashboard both use this). */
    public double rentalHours(UUID dreamId) {
        var row = jdbc.sql("SELECT shot_count, picture_model, svi_scenes FROM dream_short WHERE id = :d").param("d", dreamId)
                .query().listOfRows().stream().findFirst().orElse(null);
        return estimate.hours(row);
    }

    /** Dashboard: this Short's expected rental (hours, USD at the cheapest live price now). */
    public java.util.Map<String, Object> rentalEstimate(UUID dreamId) {
        double h = rentalHours(dreamId);
        double hourly = broker.cheapestQuote().map(GpuProvider.Quote::hourlyUsd).orElse(worstCaseHourlyUsd);
        return java.util.Map.of("hours", Math.round(h * 100) / 100.0, "usd", Math.round(h * hourly * 100) / 100.0,
                "hourlyUsd", hourly);
    }

    public boolean enabled() {
        return enabled;
    }

    /** This Short's pod boot timeline (steps in seconds since the rent, weight downloads); empty when no pod is open. */
    public java.util.Optional<java.util.Map<String, Object>> bootTiming(UUID dreamId) {
        return broker.bootTiming(dreamId);
    }

    /** The worker's boot timeline, forwarded by the media worker when the pod first answers ready. */
    public void reportBoot(UUID dreamId, java.util.Map<String, Object> timing) {
        broker.reportBoot(dreamId, timing);
    }

    /** Today's studio GPU spend (UTC) vs the daily cap: {spentUsd, bookedUsd, runningUsd, capUsd}. */
    public java.util.Map<String, Object> today() {
        return broker.today();
    }

    /** Whether this Short is using the shared pod. */
    public boolean isOpen(UUID dreamId) {
        return broker.open(dreamId).isPresent();
    }

    /** How many Shorts are using the one shared pod right now (0 = it is off). */
    public int usersOnPod() {
        return broker.openShortIds().size();
    }

    /** Best effort at the writing step: start the pod so the download overlaps the writing. A failure is retried by the first GPU step. */
    public void warm(UUID dreamId) {
        if (!enabled) {
            return;
        }
        try {
            session(dreamId);
        } catch (RuntimeException e) {
            log.warn("GPU warm-up for Short {} failed ({}); the pictures step will try again", dreamId, e.getMessage());
        }
    }

    /** Opens (or returns) the Short's pod. Throws when no provider can serve (the step then waits for the owner). */
    public Session session(UUID dreamId) {
        GpuBroker.Rental r = broker.acquire(dreamId, "video.i2v.dream", image, PodBoot.command(),
                PodBoot.env(worker(), port, "/workspace/data", volumeId == null ? null : VOLUME_MOUNT), volumeId, pick(dreamId), rentalHours(dreamId));
        return new Session(r.instance().endpoint(), readyTimeoutMinutes * 60);
    }

    /** The Short's GPU choice: Auto (default) or Manual with the owner's pick. */
    public GpuBroker.Pick pick(UUID dreamId) {
        return jdbc.sql("SELECT gpu_mode, gpu_choice FROM dream_short WHERE id = :d").param("d", dreamId)
                .query((rs, i) -> new GpuBroker.Pick(rs.getString(1), rs.getString(2))).optional().orElse(GpuBroker.Pick.AUTO);
    }

    /** Everything rentable now (and why the rest is not), cheapest first. Read-only. */
    public java.util.List<GpuBroker.Option> options() {
        return broker.options();
    }

    /**
     * The owner sets Auto or Manual for a Short. A manual pick must be one of the options offered now, and is remembered
     * as a preference (what he chose, and what was cheapest) so Auto can learn from it.
     */
    public void setPick(UUID dreamId, String mode, String choice) {
        boolean manual = "manual".equalsIgnoreCase(mode);
        if (!manual && !"auto".equalsIgnoreCase(mode)) {
            throw new IllegalArgumentException("mode must be auto or manual");
        }
        if (manual && (choice == null || choice.isBlank())) {
            throw new IllegalArgumentException("manual mode needs a choice (an option key from the list)");
        }
        if (isOpen(dreamId) && (manual != pick(dreamId).manual() || (manual && !choice.equals(pick(dreamId).choice())))) {
            throw new IllegalStateException("This Short already has a rented GPU; the choice applies to the next rental (after Retry)");
        }
        if (manual) {
            var opts = broker.options();
            var chosen = opts.stream().filter(o -> o.key() != null && o.key().equals(choice)).findFirst()
                    .orElseThrow(() -> new IllegalArgumentException("not an offered option: " + choice));
            if (!chosen.available()) {
                throw new IllegalArgumentException(choice + " cannot be rented now: " + chosen.why());
            }
            String cheapest = opts.stream().filter(GpuBroker.Option::available).findFirst().map(GpuBroker.Option::key).orElse(choice);
            prefs.recordChoice(dreamId, choice, cheapest, java.util.Map.of("shortId", dreamId.toString(),
                    "options", opts.stream().filter(GpuBroker.Option::available).map(o -> o.key() + " $" + o.hourlyUsd()).toList()));
        }
        jdbc.sql("UPDATE dream_short SET gpu_mode = :m, gpu_choice = :c, updated_at = now() WHERE id = :d")
                .param("m", manual ? "manual" : "auto").param("c", manual ? choice : null).param("d", dreamId).update();
    }

    /** Keeps the lease warm and says whether the pod still exists (the worker re-rents on GONE, stops on NONE). */
    public GpuBroker.PodState heartbeat(UUID dreamId) {
        if (!enabled) {
            return GpuBroker.PodState.OPEN;
        }
        broker.heartbeat(dreamId);
        return broker.podState(dreamId);
    }

    /**
     * A new pod for a running GPU step whose pod vanished. Only when the Short still holds a rental the provider reports
     * gone: a released rental (the Short ended) is never rented again from here, and a live one is returned as it is.
     */
    public Session replace(UUID dreamId) {
        return replace(dreamId, null, null);
    }

    /**
     * As {@link #replace(UUID)}; with {@code unreadyUrl} the worker says the pod at that address never became ready
     * ({@code why}): the broker retires that pod (terminated, booked) and rents another one.
     */
    public Session replace(UUID dreamId, String unreadyUrl, String why) {
        if (!enabled) {
            throw new IllegalStateException("The GPU broker is off (projectv.dream.gpu-provider=modal)");
        }
        if (broker.podState(dreamId) == GpuBroker.PodState.NONE) {
            throw new IllegalStateException("Short " + dreamId + " has no open GPU rental (released); not renting again");
        }
        if (unreadyUrl != null && !unreadyUrl.isBlank()) {
            broker.retireUnready(unreadyUrl, why == null ? "not ready in time" : why);
        }
        log.warn("GPU pod of Short {} vanished or never got ready during a GPU step; renting a replacement", dreamId);
        return session(dreamId); // acquire() releases and books the gone rental, then rents again
    }

    public void release(UUID dreamId, String why) {
        if (enabled) {
            broker.release(dreamId, why);
        }
    }

    /** Ends the shared pod for everyone, unless a Short using it has GPU power Manual (the owner keeps it on). */
    public void releaseAllUnlessHeld(String why) {
        if (!enabled || broker.openShortIds().stream().anyMatch(this::holdManual)) {
            return;
        }
        log.info("GPU: ending the shared pod ({})", why);
        broker.releaseAll(why);
    }

    // ------------------------------------------------------------------------------------------- GPU power (owner, 20 Nov 2026)

    /** GPU power Manual: the pod stays on after the GPU steps until the owner turns it off or it sits idle. */
    public boolean holdManual(UUID dreamId) {
        return "manual".equals(jdbc.sql("SELECT gpu_hold FROM dream_short WHERE id = :d").param("d", dreamId)
                .query(String.class).optional().orElse("auto"));
    }

    public int idleMinutes(UUID dreamId) {
        return jdbc.sql("SELECT gpu_idle_minutes FROM dream_short WHERE id = :d").param("d", dreamId)
                .query(Integer.class).optional().orElse(10);
    }

    /** Auto (the studio turns it off when done) or Manual (the owner's button / idle time), with 5-30 idle minutes. Works while a pod is on. */
    public void setHold(UUID dreamId, String hold, Integer idleMinutes) {
        if (!"auto".equalsIgnoreCase(hold) && !"manual".equalsIgnoreCase(hold)) {
            throw new IllegalArgumentException("hold must be auto or manual");
        }
        jdbc.sql("""
                        UPDATE dream_short SET gpu_hold = :h, gpu_idle_minutes = COALESCE(:i, gpu_idle_minutes), updated_at = now()
                        WHERE id = :d
                        """).param("h", hold.toLowerCase()).param("i", idleMinutes == null ? null : Math.max(5, Math.min(30, idleMinutes)))
                .param("d", dreamId).update();
    }

    /**
     * The owner's "Turn off GPU": ends the ONE shared pod now, for every Short that uses it (a running GPU step then fails
     * fast and waits for Retry).
     */
    public void turnOff(UUID dreamId) {
        if (!enabled || !isOpen(dreamId)) {
            throw new IllegalStateException("This Short is not using a rented GPU, so there is nothing to turn off");
        }
        broker.releaseAll("turned off by the owner");
    }

    /** Seconds until a Manual-hold pod turns itself off for being idle; null when this Short holds no pod or is on Auto. */
    public Long offInSeconds(UUID dreamId, Instant now) {
        if (!enabled || !holdManual(dreamId)) {
            return null;
        }
        return broker.lastActivity(dreamId)
                .map(last -> Math.max(0, idleMinutes(dreamId) * 60L - java.time.Duration.between(last, now).toSeconds())).orElse(null);
    }

    /** Every 30 s: a Manual-hold pod that nothing used for its idle minutes is turned off (rule 8: a forgotten pod is a critical bug). */
    @org.springframework.scheduling.annotation.Scheduled(initialDelay = 60_000, fixedDelay = 30_000)
    void releaseIdleManualScheduled() {
        releaseIdleManual(Instant.now());
    }

    void releaseIdleManual(Instant now) {
        if (!enabled) {
            return;
        }
        for (UUID id : broker.openShortIds()) {
            try {
                Long left = offInSeconds(id, now); // null when on Auto: never touched here
                if (left != null && left == 0L) {
                    int min = idleMinutes(id);
                    log.info("GPU power Manual: pod of Short {} idle for {} min, turning it off", id, min);
                    broker.release(id, "manual hold: idle for " + min + " min");
                }
            } catch (RuntimeException e) {
                log.warn("GPU idle check for Short {} failed: {}", id, e.getMessage());
            }
        }
    }

    /** Estimated GPU seconds for the jobs (config time estimates), including the start-up unless the pod is already open. */
    public double pictureSeconds(UUID dreamId, int pictures) {
        return pictures * secondsPerPicture + (broker.hasOpenPod() ? 0 : startSeconds);
    }

    public double clipSeconds(UUID dreamId, int totalFrames) {
        return secondsPer81Frames * totalFrames / 81.0 + (broker.hasOpenPod() ? 0 : startSeconds);
    }

    /** Estimate in rupees at the cheapest live price right now (worst-case watchdog rate if nothing is quotable). */
    public BigDecimal inr(double seconds) {
        double hourly = broker.cheapestQuote().map(GpuProvider.Quote::hourlyUsd).orElse(worstCaseHourlyUsd);
        return BigDecimal.valueOf(hourly * seconds / 3600.0 * pricing.fx()).setScale(2, RoundingMode.HALF_UP);
    }

    /** Each provider's state (configured / preflight / proven) for the GPU API. */
    public java.util.List<java.util.Map<String, Object>> providerStatuses() {
        return broker.providerStatuses();
    }

    /** Free pre-check with the real worker payload; never rents. */
    public GpuProvider.Preflight preflight(String provider) {
        return broker.preflight(provider, image, PodBoot.command(),
                PodBoot.env(worker(), port, "/workspace/data", volumeId == null ? null : VOLUME_MOUNT), volumeId);
    }

    /** The owner-approved canary: one tiny rental of the provider's cheapest GPU that must pass the worker's /health, then ends. */
    public java.util.Map<String, Object> canary(String provider, double maxUsd) {
        return broker.canary(provider, image, PodBoot.command(),
                PodBoot.env(worker(), port, "/workspace/data", volumeId == null ? null : VOLUME_MOUNT), volumeId,
                DreamGpu::workerHealth, 20 * 60, 10_000, maxUsd);
    }

    /** null = the worker answered /health ready; else why not. The address carries the token as http://worker:TOKEN@host:port. */
    static String workerHealth(String endpoint) {
        try {
            java.net.URI u = java.net.URI.create(endpoint);
            String token = u.getUserInfo() == null ? null : u.getUserInfo().replaceFirst("^[^:]*:", "");
            java.net.URI clean = new java.net.URI(u.getScheme(), null, u.getHost(), u.getPort(), "/health", null, null);
            java.net.http.HttpRequest.Builder rb = java.net.http.HttpRequest.newBuilder(clean).timeout(java.time.Duration.ofSeconds(10));
            if (token != null && !token.isBlank()) {
                rb.header("Authorization", "Bearer " + token);
            }
            java.net.http.HttpResponse<String> r = java.net.http.HttpClient.newHttpClient()
                    .send(rb.build(), java.net.http.HttpResponse.BodyHandlers.ofString());
            return r.statusCode() == 200 && r.body().replace(" ", "").contains("\"ready\":true")
                    ? null : "HTTP " + r.statusCode() + " " + r.body().substring(0, Math.min(120, r.body().length()));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "interrupted";
        } catch (java.io.IOException | java.net.URISyntaxException | RuntimeException e) {
            return e.getClass().getSimpleName();
        }
    }

    private byte[] worker() {
        // The backend runs from the repo root (scripts\start.ps1) but tests run from control-plane/: try the configured
        // path, then the same file relative to either folder, so the default works in both.
        java.util.List<Path> tries = java.util.List.of(Path.of(workerFile), Path.of("workers/pod/server.py"),
                Path.of("../workers/pod/server.py"));
        for (Path p : tries) {
            if (Files.isRegularFile(p)) {
                try {
                    return Files.readAllBytes(p);
                } catch (IOException e) {
                    throw new IllegalStateException("GPU pod worker file unreadable: " + p, e);
                }
            }
        }
        throw new IllegalStateException("GPU pod worker file not found: " + workerFile + " (projectv.gpu.pod-worker-file)");
    }
}
