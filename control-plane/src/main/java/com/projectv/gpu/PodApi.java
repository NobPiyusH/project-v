package com.projectv.gpu;

import java.time.Instant;
import java.util.List;

/** The two calls the watchdog needs from a GPU pod provider. Provider-agnostic; RunPod is {@link RunPodApi}. */
public interface PodApi {

    /**
     * @param costPerHrUsd the provider's live hourly rate for this pod (never a config price)
     * @param createdAt    when the pod was created: lifetime is measured from here, so a restart can't reset it
     */
    record Pod(String id, String name, String status, double costPerHrUsd, Instant createdAt, Instant startedAt) {
    }

    String provider();

    /** True when an API key is configured; without one the watchdog has nothing to do. */
    boolean configured();

    List<Pod> listPods();

    /** Permanently removes the pod (terminate, not stop: a stopped pod still bills its disk). */
    void terminate(String podId);
}
