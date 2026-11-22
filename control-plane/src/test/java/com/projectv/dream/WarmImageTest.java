package com.projectv.dream;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The warm-start image is OFF unless configured; when on, every rental uses it and tells the worker where ComfyUI is baked. */
class WarmImageTest {

    DreamGpu gpu(String warm) throws Exception {
        java.nio.file.Path worker = java.nio.file.Files.createTempFile("worker", ".py");
        java.nio.file.Files.writeString(worker, "print('x')");
        return new DreamGpu(null, null, null, null, "broker", worker.toString(), "plain:1", "", warm, 30, 8000, 10, 76, 900, 2.0,
                RentalEstimate.DEFAULT);
    }

    @Test
    void offByDefault() throws Exception {
        for (String blank : new String[]{null, "", "  "}) {
            DreamGpu g = gpu(blank);
            assertEquals("plain:1", g.podImage());
            assertFalse(g.podEnv().containsKey("SV_BAKED"));
        }
    }

    @Test
    void onUsesTheWarmImageAndSetsBakedRoot() throws Exception {
        DreamGpu g = gpu(" ghcr.io/o/projectv-pod:comfy-v0.45.0 ");
        assertEquals("ghcr.io/o/projectv-pod:comfy-v0.45.0", g.podImage());
        assertEquals("/opt/projectv", g.podEnv().get("SV_BAKED"));
        assertTrue(g.podEnv().containsKey("SV_BOOT_N"), "the worker is still shipped by env: no rebuild for worker edits");
    }
}
