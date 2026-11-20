package com.projectv.gpu.broker;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Broker settings: provider order and per-provider monthly caps (USD, 0 = blocked) from
 * {@code pricing.gpuBroker} in config/providers.yaml. Caps are limits, never prices (rule 7).
 */
public record BrokerConfig(List<String> order, Map<String, Double> monthlyCapUsd, double totalCapUsd,
                           List<String> gpuPreference, int minVramGb, int minRamGb, int diskGb,
                           double expectedHours, int port) {

    public static final List<String> DEFAULT_ORDER = List.of("runpod", "jarvislabs", "cloudrift", "vast", "shadeform", "modal");

    public double capUsd(String provider) {
        return monthlyCapUsd.getOrDefault(provider, 0.0);
    }

    @SuppressWarnings("unchecked")
    public static BrokerConfig fromYaml(Map<String, Object> root) {
        Map<String, Object> pricing = root.get("pricing") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> b = pricing.get("gpuBroker") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Double> caps = new LinkedHashMap<>();
        if (b.get("providers") instanceof Map<?, ?> ps) {
            ps.forEach((k, v) -> {
                if (v instanceof Map<?, ?> pm && pm.get("monthlyCapUsd") instanceof Number n) {
                    caps.put(String.valueOf(k), n.doubleValue());
                }
            });
        }
        return new BrokerConfig(
                b.get("order") instanceof List<?> l ? l.stream().map(String::valueOf).toList() : DEFAULT_ORDER,
                Map.copyOf(caps), num(b.get("totalCapUsd"), 0),
                b.get("gpuPreference") instanceof List<?> l ? l.stream().map(String::valueOf).toList()
                        : List.of("NVIDIA GeForce RTX 4090", "NVIDIA GeForce RTX 5090", "NVIDIA RTX A6000", "NVIDIA L40S"),
                (int) num(b.get("minVramGb"), 24), (int) num(b.get("minRamGb"), 48), (int) num(b.get("diskGb"), 80),
                num(b.get("expectedHours"), 0.75), (int) num(b.get("port"), 8000));
    }

    private static double num(Object o, double dflt) {
        return o instanceof Number n ? n.doubleValue() : dflt;
    }

    @Configuration
    static class Loader {
        @Bean
        BrokerConfig brokerConfig(@Value("${projectv.config-dir}") String configDir) throws IOException {
            try (InputStream in = Files.newInputStream(Path.of(configDir, "providers.yaml"))) {
                return fromYaml(new Yaml().load(in));
            }
        }
    }
}
