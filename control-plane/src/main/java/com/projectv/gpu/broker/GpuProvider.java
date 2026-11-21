package com.projectv.gpu.broker;

import com.projectv.gpu.PodApi;

import java.util.List;
import java.util.Map;

/**
 * One GPU rental provider (docs/plans/m3-gpu-router.md). Extends {@link PodApi} so the same adapter also feeds the
 * {@link com.projectv.gpu.PodWatchdog}. {@link #quote} is read-only; {@link #create} is the only call that spends.
 */
public interface GpuProvider extends PodApi {

    /** What the job needs. {@code gpuPreference} is tried in order, the cheapest one in stock wins. */
    record GpuSpec(List<String> gpuPreference, int minVramGb, int minRamGb, int diskGb) {
    }

    /** Live answer to "can I get a GPU now, and at what hourly price". Prices are never config (rule 7). */
    record Quote(String provider, boolean available, String stock, String gpu, double hourlyUsd, String detail) {
        public static Quote unavailable(String provider, String detail) {
            return new Quote(provider, false, "NONE", null, 0, detail);
        }
    }

    /**
     * @param name     must start with the studio pod prefix; the watchdog only touches such pods
     * @param bootArgv already wrapped with {@link com.projectv.gpu.PodSelfStop#wrap}
     * @param quote    the quote this create is based on (GPU and price to use)
     */
    record CreateRequest(String name, String image, int diskGb, List<String> bootArgv, Map<String, String> env,
                         int port, String volumeId, Quote quote) {
    }

    record Instance(String provider, String id, String name, double hourlyUsd, String gpu, String endpoint) {
    }

    enum State { STARTING, RUNNING, GONE }

    record InstanceStatus(State state, String endpoint, String detail) {
    }

    boolean enabledByDefault();

    /**
     * The pod's command wrapped with this provider's in-pod guard. Default: {@link com.projectv.gpu.PodSelfStop#wrap} (the
     * pod deletes itself with its own RunPod key). Providers whose pods must hold no account key (Vast.ai) override it.
     */
    default List<String> guard(List<String> jobArgv, int maxRuntimeMinutes, int idleMinutes, java.time.Instant createdAt) {
        return com.projectv.gpu.PodSelfStop.wrap(jobArgv, maxRuntimeMinutes, idleMinutes, createdAt);
    }

    /** The cheapest option in stock (or an unavailable quote saying why not). */
    Quote quote(GpuSpec spec);

    /** Every option in stock, one per GPU (and cloud) the provider offers: what the owner can pick from in Manual mode. */
    default List<Quote> quotes(GpuSpec spec) {
        Quote q = quote(spec);
        return q.available() ? List.of(q) : List.of();
    }

    Instance create(CreateRequest req);

    /** Where the worker on this pod listens (null when the provider cannot say); used to rebuild a rental after a restart. */
    default String endpointFor(String instanceId, int port) {
        return null;
    }

    InstanceStatus status(String instanceId);
}
