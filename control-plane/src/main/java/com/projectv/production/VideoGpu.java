package com.projectv.production;

import com.projectv.dream.DreamGpu;
import com.projectv.media.MediaTypes.ClipJob;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * The Video path's door to the GPU broker, sharing {@link DreamGpu} (same pod worker, warm image, daily cap, per-rental
 * estimate, Auto pick) instead of copying it. Off unless {@code projectv.video.provider-draft / provider-final /
 * provider-keyframes} is {@code broker}; the defaults (wan / omni / Gemini + Z-Image) are unchanged. The broker itself must
 * also be on ({@code projectv.dream.gpu-provider=broker}), else a Video batch is refused with that message.
 * <p>
 * A Video id plays the role of the Short id: it is the lease key; the rental is booked on the Video's CostEntry ledger
 * when it ends. The pod is let go right after each batch's save; a batch that failed leaves it to {@link #releaseIdle}.
 */
@Component
public class VideoGpu {

    private static final Logger log = LoggerFactory.getLogger(VideoGpu.class);
    /** The routing/provider id of the rented-pod route (config/providers.yaml). */
    public static final String POD = "gpu_pod";

    private final DreamGpu gpu;
    private final String draft;
    private final String fin;
    private final String keyframes;
    private final int idleMinutes;
    private final Map<UUID, Instant> holds = new ConcurrentHashMap<>();

    public VideoGpu(DreamGpu gpu, @Value("${projectv.video.provider-draft:wan}") String draft,
                    @Value("${projectv.video.provider-final:omni}") String fin,
                    @Value("${projectv.video.provider-keyframes:default}") String keyframes,
                    @Value("${projectv.video.gpu-idle-minutes:5}") int idleMinutes) {
        this.gpu = gpu;
        this.draft = norm(draft);
        this.fin = norm(fin);
        this.keyframes = norm(keyframes);
        this.idleMinutes = Math.max(2, idleMinutes);
    }

    private static String norm(String s) {
        return s == null ? "" : s.strip().toLowerCase();
    }

    /** Clips of this kind (draft | final) go to the broker's pod. */
    public boolean clips(String kind) {
        return "broker".equals(VideoStage.DRAFT.equals(kind) ? draft : fin);
    }

    /** Keyframes (FLUX.2 klein) go to the broker's pod. */
    public boolean keyframes() {
        return "broker".equals(keyframes);
    }

    private void requireBroker() {
        if (!gpu.enabled()) {
            throw new IllegalStateException(
                    "projectv.video.provider-*=broker needs the GPU broker on: set projectv.dream.gpu-provider=broker");
        }
    }

    /** Pod seconds for these clips (config time per 81 frames; the pod start-up only when no pod is open). */
    public double clipSeconds(UUID videoId, List<ClipJob> jobs) {
        // The configured seconds per 81 frames were measured at 480x832: scale up for larger clips so 720p is not under-estimated.
        double equivalentFrames = jobs.stream().mapToDouble(j -> j.numFrames() * Math.max(1.0, Math.pow((double) j.width() * j.height() / REFERENCE_PIXELS, PIXEL_EXPONENT))).sum();
        return gpu.clipSeconds(videoId, (int) Math.ceil(equivalentFrames));
    }

    /** 480x832, the size the pod's seconds-per-81-frames figure was measured at (Dream Shorts). Linear scaling is an estimate. */
    static final double REFERENCE_PIXELS = 480.0 * 832.0;
    /** Modal H100 measured 30 s (480p) vs 133 s (720p) per 81 frames (config wanSecondsPer81Frames): 2.3x the pixels took 4.4x the time. Not measured on a pod. */
    static final double PIXEL_EXPONENT = 1.75;

    public double pictureSeconds(UUID videoId, int pictures) {
        return gpu.pictureSeconds(videoId, pictures);
    }

    /** Rupees at the cheapest live price for this many pod seconds. */
    public BigDecimal inr(double seconds) {
        return gpu.inr(seconds);
    }

    /** A pod start in progress per Video: it outlives one activity attempt (a start takes 8-18 min, an attempt is 5), so a retry joins it. */
    private final Map<UUID, java.util.concurrent.CompletableFuture<DreamGpu.Session>> starting = new ConcurrentHashMap<>();
    private final java.util.concurrent.ExecutorService startPool = java.util.concurrent.Executors.newCachedThreadPool(r -> {
        Thread t = new Thread(r, "video-gpu-start");
        t.setDaemon(true);
        return t;
    });
    /** How long one call waits for the pod; kept under the 5-minute activity limit. Package-private so a test can shorten it. */
    long startWaitSeconds = 240;

    /**
     * Opens (or joins) the shared pod for this Video. Throws when no provider can serve or the daily cap would be exceeded.
     * The start runs in the background and survives an activity timeout: a call that waits {@link #startWaitSeconds} without
     * the pod being ready throws, and the Temporal retry joins the same start instead of renting a second pod.
     */
    public DreamGpu.Session session(UUID videoId, double podSeconds) {
        requireBroker();
        gpu.hold(videoId);
        holds.put(videoId, Instant.now());
        var start = starting.computeIfAbsent(videoId, id -> java.util.concurrent.CompletableFuture.supplyAsync(
                () -> gpu.session(id, podSeconds / 3600.0), startPool));
        try {
            var session = start.get(startWaitSeconds, java.util.concurrent.TimeUnit.SECONDS);
            starting.remove(videoId, start);
            holds.put(videoId, Instant.now());
            return session;
        } catch (java.util.concurrent.TimeoutException e) {
            throw new IllegalStateException("The GPU pod is still starting (a start takes 8-18 min); this step retries and joins it");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Waiting for the GPU pod was interrupted; this step retries and joins the start");
        } catch (java.util.concurrent.ExecutionException e) {
            starting.remove(videoId, start);
            holds.remove(videoId);
            gpu.unhold(videoId);
            Throwable cause = e.getCause() == null ? e : e.getCause();
            throw cause instanceof RuntimeException re ? re : new IllegalStateException(cause.getMessage(), cause);
        }
    }

    /** Lets go of the pod (it ends when nobody else uses it) and books this Video's share on its ledger. Safe to repeat. */
    public void release(UUID videoId, String why) {
        if (holds.remove(videoId) == null) {
            return;
        }
        gpu.unhold(videoId);
        gpu.release(videoId, why);
    }

    /** Every minute: a Video that holds the pod but has not used it for a while (a failed batch waiting for Retry) lets go. */
    @Scheduled(initialDelay = 60_000, fixedDelay = 60_000)
    void releaseIdleScheduled() {
        releaseIdle(Instant.now());
    }

    void releaseIdle(Instant now) {
        for (UUID id : List.copyOf(holds.keySet())) {
            var inProgress = starting.get(id);
            if (inProgress != null && !inProgress.isDone()) {
                continue; // a pod start in progress is not idle (it would end the rental before it is ready)
            }
            try {
                Instant last = gpu.lastActivity(id).orElse(holds.get(id));
                if (last == null || Duration.between(last, now).toMinutes() >= idleMinutes) {
                    log.info("Video {} holds the GPU pod but used it for {} min: ending its share", id, idleMinutes);
                    release(id, "video GPU batch idle for " + idleMinutes + " min");
                }
            } catch (RuntimeException e) {
                log.warn("GPU idle check for Video {} failed: {}", id, e.getMessage());
            }
        }
    }
}
