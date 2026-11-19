package com.projectv.salad;

import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.Deflater;

/**
 * Ships workers/salad/server.py to the container with no image build: zlib + base64, split over env vars
 * {@code SV_BOOT_00..} (Salad caps an env value at 1000 chars) and a short {@code container.command} that joins,
 * decodes and execs it (Salad caps a command item at 1000 chars too; this one is about 200).
 */
final class SaladBoot {

    static final int CHUNK = 900;

    private SaladBoot() {
    }

    /** The env vars: SV_BOOT_00.. (the payload), SV_BOOT_N (how many), SV_PORT, SV_ROOT. */
    static Map<String, String> env(byte[] workerSource, int port) {
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
        env.put("SV_ROOT", "/data");
        return env;
    }

    static List<String> command() {
        String py = "import os,zlib,base64;exec(zlib.decompress(base64.b64decode(''.join(os.environ['SV_BOOT_%02d'%i] "
                + "for i in range(int(os.environ['SV_BOOT_N']))))))";
        return new ArrayList<>(List.of("python", "-c", py));
    }
}
