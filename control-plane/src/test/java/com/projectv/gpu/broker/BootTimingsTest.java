package com.projectv.gpu.broker;

import com.projectv.gpu.GpuPodLimits;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Boot-phase timing (warm-start work): fakes only, nothing is rented. */
class BootTimingsTest {

    static final Instant RENT = GpuBrokerTest.NOW;

    static Map<String, Object> worker(double startAfterRent) {
        double t0 = RENT.toEpochMilli() / 1000.0 + startAfterRent;
        return Map.of(
                "started_at", t0,
                "marks", List.of(
                        Map.of("phase", "worker_started", "at", t0, "since_start_s", 0.0),
                        Map.of("phase", "deps_installed", "at", t0 + 60, "since_start_s", 60.0),
                        Map.of("phase", "weights_done", "at", t0 + 900, "since_start_s", 900.0),
                        Map.of("phase", "ready", "at", t0 + 960, "since_start_s", 960.0)),
                "downloads", List.of(Map.of("name", "unet.safetensors", "gb", 14.29, "seconds", 126.0, "mb_per_s", 113.4, "source", "huggingface")));
    }

    @Test
    void summaryJoinsRentRunningAndTheWorkerTimeline() {
        var row = new BootTimings.Row("pod1", "vast", "img", RENT, RENT.plusSeconds(90), worker(300));
        Map<String, Object> s = BootTimings.summary(row);
        @SuppressWarnings("unchecked") List<Map<String, Object>> steps = (List<Map<String, Object>>) s.get("steps");
        assertEquals(List.of("rented", "provider_running", "container_started", "deps_installed", "weights_done", "ready"),
                steps.stream().map(m -> m.get("phase")).toList());
        assertEquals(90.0, steps.get(1).get("secondsSinceRent"));
        assertEquals(300.0, steps.get(2).get("secondsSinceRent"), "image pull + container start = the gap before the worker's first mark");
        assertEquals(1260.0, s.get("totalSeconds"));
        assertEquals(1, ((List<?>) s.get("downloads")).size());
    }

    @Test
    void summaryWithoutAWorkerReportHasNoTotal() {
        var s = BootTimings.summary(new BootTimings.Row("pod1", "runpod", "img", RENT, null, null));
        assertNull(s.get("totalSeconds"));
        assertEquals(1, ((List<?>) s.get("steps")).size());
    }

    @Test
    void firstRunningAndFirstWorkerReportWin() {
        var t = new BootTimings.Memory();
        t.rented("p", "runpod", "img", RENT);
        t.running("p", RENT.plusSeconds(40));
        t.running("p", RENT.plusSeconds(400));
        t.workerReport("p", worker(100));
        t.workerReport("p", worker(999));
        var row = t.get("p").orElseThrow();
        assertEquals(RENT.plusSeconds(40), row.runningAt());
        assertEquals(RENT.toEpochMilli() / 1000.0 + 100, ((Number) row.worker().get("started_at")).doubleValue());
    }

    @Test
    void brokerRecordsRentThenRunningThenTheWorkersReport() {
        var timings = new BootTimings.Memory();
        var fake = new GpuBrokerTest.Fake("runpod");
        var cfg = new BrokerConfig(List.of("runpod"), new HashMap<>(Map.of("runpod", 5.0)), 0,
                List.of("NVIDIA GeForce RTX 4090"), 24, 48, 80, 1.0, 8000);
        var clock = Clock.fixed(RENT, ZoneOffset.UTC);
        var b = new GpuBroker(List.of(fake), cfg, new GpuWallet(cfg, p -> 0.0), new GpuBrokerTest.Records(), new GpuBrokerTest.Leases(),
                (p, pod, r, h, u) -> { }, new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, true), clock);
        b.setBootTimings(timings);
        UUID shortId = UUID.randomUUID();
        var r = b.acquire(shortId, "video.i2v.dream", "my/image:1", List.of("python", "w.py"), Map.of(), null);
        assertEquals(RENT, timings.get(r.instance().id()).orElseThrow().rentedAt());
        assertEquals("my/image:1", timings.get(r.instance().id()).orElseThrow().image());
        assertNull(b.bootTiming(shortId).orElseThrow().get("totalSeconds"), "no worker report yet");

        b.podState(shortId); // the heartbeat path reads the provider status: first RUNNING sighting
        assertNotNull(timings.get(r.instance().id()).orElseThrow().runningAt());

        b.reportBoot(shortId, worker(120));
        assertEquals(1080.0, b.bootTiming(shortId).orElseThrow().get("totalSeconds"));
        b.reportBoot(UUID.randomUUID(), worker(1)); // a Short with no rental: ignored, no error
        assertTrue(timings.recent(5).size() == 1);
    }
}
