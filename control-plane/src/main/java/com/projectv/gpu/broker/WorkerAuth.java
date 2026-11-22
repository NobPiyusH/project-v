package com.projectv.gpu.broker;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/**
 * Per-rental bearer token for the pod worker's HTTP port (workers/pod/server.py, env {@code SV_TOKEN}). A Vast.ai port is
 * public (direct IP:port), so the worker answers 401 to everything without it. The token is
 * {@code HMAC-SHA256(secret, pod name)}: nothing to store, a control-plane restart derives the same token again for a
 * recovered rental, and the host of one pod cannot compute the token of another (HMAC is one-way, the pod name carries
 * a random part). The secret is {@code projectv.gpu.worker-token-secret} (env POD_TOKEN_SECRET); when empty it is
 * derived from the provider keys already in .env, and when there are none, random for this run.
 * The token reaches the Python side inside the pod address ({@code http://worker:<token>@host:port}); see
 * workers/pod/client.py. Vast.ai always uses it; RunPod while {@code projectv.gpu.worker-token-runpod} is true.
 */
@Component
public class WorkerAuth {

    public static final String ENV = "SV_TOKEN";
    /** No token anywhere (tests, and callers that never asked for one). */
    public static final WorkerAuth OFF = new WorkerAuth(new byte[0], Set.of());

    private final byte[] secret;
    private final Set<String> providers;

    @Autowired
    public WorkerAuth(@Value("${projectv.gpu.worker-token-secret:}") String secret,
                      @Value("${projectv.runpod.api-key:}") String runpodKey,
                      @Value("${projectv.vast.api-key:}") String vastKey,
                      @Value("${projectv.gpu.worker-token-runpod:true}") boolean runpod) {
        this(secretBytes(secret, runpodKey, vastKey), runpod ? Set.of("vast", "e2e", "jarvislabs", "runpod") : Set.of("vast", "e2e", "jarvislabs"));
    }

    public WorkerAuth(byte[] secret, Set<String> providers) {
        this.secret = secret;
        this.providers = new HashSet<>(providers);
    }

    private static byte[] secretBytes(String secret, String runpodKey, String vastKey) {
        if (secret != null && !secret.isBlank()) {
            return secret.strip().getBytes(StandardCharsets.UTF_8);
        }
        String keys = (runpodKey == null ? "" : runpodKey.strip()) + "|" + (vastKey == null ? "" : vastKey.strip());
        if (keys.length() > 1) {
            try {
                return MessageDigest.getInstance("SHA-256").digest(("projectv-worker-token|" + keys).getBytes(StandardCharsets.UTF_8));
            } catch (java.security.NoSuchAlgorithmException e) {
                throw new IllegalStateException(e);
            }
        }
        byte[] random = new byte[32];
        new SecureRandom().nextBytes(random);
        return random;
    }

    public boolean enabledFor(String provider) {
        return secret.length > 0 && providers.contains(provider);
    }

    /** The token for one pod name (48 hex characters). */
    public String token(String podName) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            byte[] d = mac.doFinal(podName.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 24; i++) {
                sb.append("%02x".formatted(d[i]));
            }
            return sb.toString();
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    /** The worker env plus {@code SV_TOKEN} when this provider uses tokens (the input map is not changed). */
    public Map<String, String> env(String provider, String podName, Map<String, String> env) {
        if (!enabledFor(provider)) {
            return env;
        }
        Map<String, String> out = new LinkedHashMap<>(env == null ? Map.of() : env);
        out.put(ENV, token(podName));
        return out;
    }

    /** {@code http://host:port} -> {@code http://worker:<token>@host:port}; unchanged when no token applies or no endpoint. */
    public String decorate(String provider, String podName, String endpoint) {
        if (endpoint == null || !enabledFor(provider)) {
            return endpoint;
        }
        int i = endpoint.indexOf("://");
        if (i < 0 || endpoint.contains("@")) {
            return endpoint;
        }
        return endpoint.substring(0, i + 3) + "worker:" + token(podName) + "@" + endpoint.substring(i + 3);
    }
}
