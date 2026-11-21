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
 * E2E Networks (Indian GPU cloud, Delhi) settings from {@code pricing.gpuBroker.providers.e2e} in config/providers.yaml. Limits and
 * estimates only: the hourly price always comes from E2E's live plan list (rule 7); the INR/USD rate and GST are the two inputs
 * needed to show it in dollars (docs/e2e-provider-notes.md). Every field has a default so a missing key never disables a limit.
 *
 * @param plans               allowed 1-GPU plans: E2E plan {@code sku_name}, a short GPU label and its VRAM (the plan list has no VRAM field)
 * @param location            E2E region; MyAccount GPU nodes exist only in Delhi
 * @param image               OS image; Ubuntu 22.04 GPU images ship Docker and the NVIDIA Container Toolkit (24.04 does not)
 * @param inrPerUsd           rupees per dollar used to convert the plan price
 * @param gstPercent          GST added on top of the plan price
 * @param publicPort          host port the worker is published on; the default security group already opens 80 and 443
 * @param startTimeoutSeconds a node that is not Running with a public IP by then is deleted
 * @param pollSeconds         how often the node is polled while it starts
 * @param cacheSeconds        the plan list is reused this long
 */
public record E2eConfig(List<Plan> plans, String location, String image, double inrPerUsd, double gstPercent, int publicPort,
                        int startTimeoutSeconds, int pollSeconds, int cacheSeconds) {

    /** One allowed plan. */
    public record Plan(String sku, String gpu, int vramGb) {
    }

    public static final List<Plan> DEFAULT_PLANS = List.of(
            new Plan("GDC3.L40S-60.220GB_v1", "L40S", 48),
            new Plan("GDC3.A40-16.100GB", "A40", 48),
            new Plan("GDC3.A10080-16.115GB", "A100-80GB", 80));

    public static E2eConfig defaults() {
        return fromYaml(Map.of());
    }

    @SuppressWarnings("unchecked")
    public static E2eConfig fromYaml(Map<String, Object> root) {
        Map<String, Object> pricing = root.get("pricing") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> b = pricing.get("gpuBroker") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> ps = b.get("providers") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> v = ps.get("e2e") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        List<Plan> plans = DEFAULT_PLANS;
        if (v.get("plans") instanceof List<?> l && !l.isEmpty()) {
            plans = l.stream().filter(o -> o instanceof Map<?, ?>).map(o -> (Map<String, Object>) o)
                    .map(m -> new Plan(String.valueOf(m.get("sku")), String.valueOf(m.get("gpu")), (int) num(m.get("vramGb"), 0))).toList();
        }
        return new E2eConfig(plans, v.get("location") != null ? v.get("location").toString() : "Delhi",
                v.get("image") != null ? v.get("image").toString() : "Ubuntu-22.04-GPU",
                Math.max(1, num(v.get("inrPerUsd"), 94)), Math.max(0, num(v.get("gstPercent"), 18)),
                (int) num(v.get("publicPort"), 443), (int) num(v.get("startTimeoutSeconds"), 540),
                (int) num(v.get("pollSeconds"), 10), (int) num(v.get("cacheSeconds"), 30));
    }

    private static double num(Object o, double dflt) {
        return o instanceof Number n ? n.doubleValue() : dflt;
    }

    @Configuration
    static class Loader {
        @Bean
        E2eConfig e2eConfig(@Value("${projectv.config-dir}") String configDir) throws IOException {
            try (InputStream in = Files.newInputStream(Path.of(configDir, "providers.yaml"))) {
                return fromYaml(new Yaml().load(in));
            }
        }
    }
}
