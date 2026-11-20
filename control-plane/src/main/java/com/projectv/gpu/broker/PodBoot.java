package com.projectv.gpu.broker;

import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.Deflater;

/**
 * Ships workers/pod/server.py to a rented pod with no image build: zlib + base64, split over env vars
 * {@code SV_BOOT_00..} (the same 900-char chunks the Salad route proved; some providers cap an env value) and a short
 * command that joins, decodes and execs it. The broker wraps this command with {@code PodSelfStop}.
 */
public final class PodBoot {

    static final int CHUNK = 900;

    private PodBoot() {
    }

    /** SV_BOOT_00.. (the payload), SV_BOOT_N (how many), SV_PORT, SV_ROOT (where weights and ComfyUI live). */
    public static Map<String, String> env(byte[] workerSource, int port, String root) {
        return env(workerSource, port, root, null);
    }

    /** As above; {@code volumeMount} (where the provider mounts the persistent volume) becomes SV_VOLUME when set. */
    public static Map<String, String> env(byte[] workerSource, int port, String root, String volumeMount) {
        Deflater d = new Deflater(Deflater.BEST_COMPRESSION);
        d.setInput(workerSource);
        d.finish();
        ByteArrayOutputStream zipped = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        while (!d.finished()) {
            zipped.write(buf, 0, d.deflate(buf));
        }
        d.end();
        String b64 = Base64.getEncoder().encodeToString(zipped.toByteArray());
        Map<String, String> env = new LinkedHashMap<>();
        int n = 0;
        for (int i = 0; i < b64.length(); i += CHUNK, n++) {
            env.put("SV_BOOT_%02d".formatted(n), b64.substring(i, Math.min(b64.length(), i + CHUNK)));
        }
        env.put("SV_BOOT_N", Integer.toString(n));
        env.put("SV_PORT", Integer.toString(port));
        env.put("SV_ROOT", root);
        if (volumeMount != null && !volumeMount.isBlank()) {
            env.put("SV_VOLUME", volumeMount);
        }
        return env;
    }

    public static List<String> command() {
        String py = "import os,zlib,base64;exec(zlib.decompress(base64.b64decode(''.join(os.environ['SV_BOOT_%02d'%i] "
                + "for i in range(int(os.environ['SV_BOOT_N']))))))";
        return new ArrayList<>(List.of("python", "-c", py));
    }
}
