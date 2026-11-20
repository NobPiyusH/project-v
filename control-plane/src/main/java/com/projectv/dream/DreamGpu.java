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
                    @Value("${projectv.gpu.max-hourly-usd:2.0}") double worstCaseHourlyUsd) {
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
    }

    public boolean enabled() {
        return enabled;
    }

    public boolean isOpen(UUID dreamId) {
        return broker.open(dreamId).isPresent();
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
                PodBoot.env(worker(), port, "/workspace/data", volumeId == null ? null : VOLUME_MOUNT), volumeId, pick(dreamId));
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

    public void heartbeat(UUID dreamId) {
        if (enabled) {
            broker.heartbeat(dreamId);
        }
    }

    public void release(UUID dreamId, String why) {
        if (enabled) {
            broker.release(dreamId, why);
        }
    }

    /** Estimated GPU seconds for the jobs (config time estimates), including the start-up unless the pod is already open. */
    public double pictureSeconds(UUID dreamId, int pictures) {
        return pictures * secondsPerPicture + (isOpen(dreamId) ? 0 : startSeconds);
    }

    public double clipSeconds(UUID dreamId, int totalFrames) {
        return secondsPer81Frames * totalFrames / 81.0 + (isOpen(dreamId) ? 0 : startSeconds);
    }

    /** Estimate in rupees at the cheapest live price right now (worst-case watchdog rate if nothing is quotable). */
    public BigDecimal inr(double seconds) {
        double hourly = broker.cheapestQuote().map(GpuProvider.Quote::hourlyUsd).orElse(worstCaseHourlyUsd);
        return BigDecimal.valueOf(hourly * seconds / 3600.0 * pricing.fx()).setScale(2, RoundingMode.HALF_UP);
    }

    private byte[] worker() {
        try {
            return Files.readAllBytes(Path.of(workerFile));
        } catch (IOException e) {
            throw new IllegalStateException("GPU pod worker file not found: " + workerFile + " (projectv.gpu.pod-worker-file)", e);
        }
    }
}
