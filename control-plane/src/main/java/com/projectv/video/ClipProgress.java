package com.projectv.video;

import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Live state of clip batches for the dashboard: per-clip progress reported by the media worker, and the owner's Stop.
 * In memory on purpose: it is a view of work in flight (the clips themselves are saved as they finish). A control
 * plane restart forgets a pending Stop; the worker then simply finishes the batch.
 */
@Component
public class ClipProgress {

    public record Snapshot(List<Map<String, Object>> clips, boolean finished, Instant at) {
    }

    private final Map<UUID, Snapshot> progress = new ConcurrentHashMap<>();
    private final Set<UUID> stops = ConcurrentHashMap.newKeySet();

    public void report(UUID videoId, List<Map<String, Object>> clips, boolean finished) {
        progress.put(videoId, new Snapshot(clips == null ? List.of() : List.copyOf(clips), finished, Instant.now()));
        if (finished) {
            stops.remove(videoId);
        }
    }

    public Snapshot get(UUID videoId) {
        return progress.get(videoId);
    }

    public void requestStop(UUID videoId) {
        stops.add(videoId);
    }

    public boolean stopRequested(UUID videoId) {
        return stops.contains(videoId);
    }

    /** A new batch starts clean (an old Stop must not cancel it). */
    public void startBatch(UUID videoId) {
        stops.remove(videoId);
        progress.remove(videoId);
    }
}
