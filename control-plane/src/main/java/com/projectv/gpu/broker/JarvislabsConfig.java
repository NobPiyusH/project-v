package com.projectv.gpu.broker;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/**
 * JarvisLabs settings from {@code pricing.gpuBroker.providers.jarvislabs} in config/providers.yaml. Limits only: the hourly price
 * always comes from the live GPU list (rule 7). See docs/jarvislabs-provider-notes.md; the GPU type names below follow the CLI docs
 * and are NOT verified against the live list.
 *
 * @param gpus                allowed GPU types and their VRAM (the live list's VRAM field is a free-form string)
 * @param template            instance template ("pytorch" is container-based)
 * @param servicePort         the port JarvisLabs publishes through the instance endpoint (docs: 6006); the worker is told to listen on it
 * @param startTimeoutSeconds create blocks until Running; the helper call is cut off after this
 * @param inrPerUsd           rupees per dollar: the owner's account is INR, so the live list's prices are rupees per hour
 * @param minBalance          the preflight fails below this account balance (account currency)
 * @param scriptMaxBytes      the largest startup script the preflight accepts (JarvisLabs publishes no limit: unverified)
 */
public record JarvislabsConfig(List<Gpu> gpus, String template, int servicePort, int startTimeoutSeconds, int scriptMaxBytes,
                               int cacheSeconds, double inrPerUsd, double minBalance) {

    public record Gpu(String type, int vramGb) {
    }

    public static final List<Gpu> DEFAULT_GPUS = List.of(new Gpu("A100", 40), new Gpu("A100-80GB", 80), new Gpu("RTX-PRO6000", 96));

    public static JarvislabsConfig defaults() {
        return fromYaml(Map.of());
    }

    @SuppressWarnings("unchecked")
    public static JarvislabsConfig fromYaml(Map<String, Object> root) {
        Map<String, Object> pricing = root.get("pricing") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> b = pricing.get("gpuBroker") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> ps = b.get("providers") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> v = ps.get("jarvislabs") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        List<Gpu> gpus = DEFAULT_GPUS;
        if (v.get("gpus") instanceof List<?> l && !l.isEmpty()) {
            gpus = l.stream().filter(o -> o instanceof Map<?, ?>).map(o -> (Map<String, Object>) o)
                    .map(m -> new Gpu(String.valueOf(m.get("type")), (int) num(m.get("vramGb"), 0))).toList();
        }
        return new JarvislabsConfig(gpus, v.get("template") != null ? v.get("template").toString() : "pytorch",
                (int) num(v.get("servicePort"), 6006), (int) num(v.get("startTimeoutSeconds"), 900),
                (int) num(v.get("scriptMaxBytes"), 60_000), (int) num(v.get("cacheSeconds"), 30), Math.max(1, num(v.get("inrPerUsd"), 94)), num(v.get("minBalance"), 100));
    }

    private static double num(Object o, double dflt) {
        return o instanceof Number n ? n.doubleValue() : dflt;
    }

    @Configuration
    static class Loader {
        @Bean
        JarvislabsConfig jarvislabsConfig(@Value("${projectv.config-dir}") String configDir) throws IOException {
            try (InputStream in = Files.newInputStream(Path.of(configDir, "providers.yaml"))) {
                return fromYaml(new Yaml().load(in));
            }
        }
    }
}
