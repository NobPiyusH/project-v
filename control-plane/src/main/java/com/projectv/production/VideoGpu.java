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
        return gpu.clipSeconds(videoId, jobs.stream().mapToInt(ClipJob::numFrames).sum());
    }

    public double pictureSeconds(UUID videoId, int pictures) {
        return gpu.pictureSeconds(videoId, pictures);
    }

    /** Rupees at the cheapest live price for this many pod seconds. */
    public BigDecimal inr(double seconds) {
        return gpu.inr(seconds);
    }

    /** Opens (or joins) the shared pod for this Video. Throws when no provider can serve or the daily cap would be exceeded. */
    public DreamGpu.Session session(UUID videoId, double podSeconds) {
        requireBroker();
        gpu.hold(videoId);
        holds.put(videoId, Instant.now());
        try {
            return gpu.session(videoId, podSeconds / 3600.0);
        } catch (RuntimeException e) {
            holds.remove(videoId);
            gpu.unhold(videoId);
            throw e;
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
