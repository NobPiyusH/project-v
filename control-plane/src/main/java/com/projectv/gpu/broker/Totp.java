package com.projectv.gpu.broker;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.ByteBuffer;
import java.security.GeneralSecurityException;

/** RFC 6238 time-based one-time passwords (HMAC-SHA1, 30 s step) in plain Java; the seed is base32 as authenticator apps show it. */
final class Totp {

    static final int STEP_SECONDS = 30;

    private Totp() {
    }

    /** The code for the 30 s window containing {@code epochSeconds}, left-padded with zeros to {@code digits}. */
    static String code(String base32Secret, long epochSeconds, int digits) {
        byte[] key = base32(base32Secret);
        if (key.length == 0) {
            throw new IllegalArgumentException("TOTP secret is empty or not base32");
        }
        try {
            Mac mac = Mac.getInstance("HmacSHA1");
            mac.init(new SecretKeySpec(key, "HmacSHA1"));
            byte[] h = mac.doFinal(ByteBuffer.allocate(8).putLong(epochSeconds / STEP_SECONDS).array());
            int o = h[h.length - 1] & 0x0f;
            int bin = (h[o] & 0x7f) << 24 | (h[o + 1] & 0xff) << 16 | (h[o + 2] & 0xff) << 8 | (h[o + 3] & 0xff);
            long mod = 1;
            for (int i = 0; i < digits; i++) {
                mod *= 10;
            }
            return String.format("%0" + digits + "d", bin % mod);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HmacSHA1 unavailable", e);
        }
    }

    /** Milliseconds until the next 30 s window starts. */
    static long millisToNextWindow(long nowMillis) {
        return STEP_SECONDS * 1000L - nowMillis % (STEP_SECONDS * 1000L);
    }

    /** RFC 4648 base32; case, spaces, dashes and '=' padding are ignored. Unknown characters are an error (a typo must not give a wrong code silently). */
    static byte[] base32(String s) {
        String in = s == null ? "" : s.replaceAll("[\\s=-]", "").toUpperCase();
        ByteBuffer out = ByteBuffer.allocate(in.length() * 5 / 8 + 1);
        int buffer = 0;
        int bits = 0;
        for (char c : in.toCharArray()) {
            int v = c >= 'A' && c <= 'Z' ? c - 'A' : c >= '2' && c <= '7' ? c - '2' + 26 : -1;
            if (v < 0) {
                throw new IllegalArgumentException("TOTP secret is not valid base32");
            }
            buffer = buffer << 5 | v;
            bits += 5;
            if (bits >= 8) {
                out.put((byte) (buffer >> (bits - 8) & 0xff));
                bits -= 8;
            }
        }
        byte[] r = new byte[out.position()];
        out.flip().get(r);
        return r;
    }
}
