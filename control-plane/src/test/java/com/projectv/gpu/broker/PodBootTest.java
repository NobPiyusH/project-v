package com.projectv.gpu.broker;

import org.junit.jupiter.api.Test;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Map;
import java.util.zip.Inflater;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PodBootTest {

    @Test
    void workerRoundTripsThroughChunkedEnv() throws Exception {
        byte[] src = ("print('hello')\n".repeat(2000)).getBytes(StandardCharsets.UTF_8);
        Map<String, String> env = PodBoot.env(src, 8000, "/workspace/data");
        int n = Integer.parseInt(env.get("SV_BOOT_N"));
        StringBuilder b64 = new StringBuilder();
        for (int i = 0; i < n; i++) {
            String chunk = env.get("SV_BOOT_%02d".formatted(i));
            assertTrue(chunk.length() <= PodBoot.CHUNK);
            b64.append(chunk);
        }
        Inflater inf = new Inflater();
        inf.setInput(Base64.getDecoder().decode(b64.toString()));
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        while (!inf.finished()) {
            out.write(buf, 0, inf.inflate(buf));
        }
        assertEquals(new String(src, StandardCharsets.UTF_8), out.toString(StandardCharsets.UTF_8));
        assertEquals("8000", env.get("SV_PORT"));
        assertEquals("/workspace/data", env.get("SV_ROOT"));
        assertEquals("python", PodBoot.command().get(0));
        assertFalse(env.containsKey("SV_VOLUME"));
        assertEquals("/workspace", PodBoot.env(src, 8000, "/workspace/data", "/workspace").get("SV_VOLUME"));
        assertFalse(env.containsKey("SV_BAKED"), "warm start is off unless asked for");
        assertEquals("/opt/projectv", PodBoot.env(src, 8000, "/workspace/data", null, "/opt/projectv").get("SV_BAKED"));
    }
}
