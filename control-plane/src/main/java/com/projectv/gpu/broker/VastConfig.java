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
 * Vast.ai offer filter and timing, all from {@code pricing.gpuBroker.providers.vast} in config/providers.yaml (limits and
 * estimates only: the hourly price always comes from the live offer, rule 7). Every field has a default so a missing
 * key never disables a safety limit.
 *
 * @param gpuNames          Vast {@code gpu_name} values that are allowed (all &gt;= 24 GB, fp8 Wan)
 * @param minReliability    host reliability score ({@code reliability}), 0..1
 * @param minDownMbps       measured download speed of the host, the weights are ~42 GB
 * @param diskGb            container disk to rent (and the minimum free disk an offer must have)
 * @param maxBandwidthUsd   an offer whose estimated internet bill (download + upload GB x its per-GB price) is above this is skipped:
 *                          some hosts charge tens of dollars per TB, which would cost more than the GPU hour
 * @param downloadGb        expected download per rental (weights, ComfyUI, pip)
 * @param uploadGb          expected upload per rental (finished pictures, clips and scene zips back to the studio)
 * @param amortizeHours     the bandwidth bill is spread over this many hours to give the effective hourly price
 * @param startTimeoutSeconds an instance that is not running with its port mapped after this long is destroyed (and the next offer tried)
 * @param pollSeconds       how often the instance is polled while it starts
 * @param offerAttempts     offers tried per create (an offer may be taken by someone else between search and create)
 * @param priceSlack        a replacement offer may cost at most this many times the quoted price
 * @param cacheSeconds      search results are reused this long (Vast rate-limits; the dashboard asks often)
 */
public record VastConfig(List<String> gpuNames, double minReliability, double minDownMbps, int diskGb, double maxBandwidthUsd,
                         double downloadGb, double uploadGb, double amortizeHours, int startTimeoutSeconds, int pollSeconds,
                         int offerAttempts, double priceSlack, int cacheSeconds) {

    public static final List<String> DEFAULT_GPUS = List.of("RTX 4090", "RTX 5090", "L40S", "RTX A6000");

    public static VastConfig defaults() {
        return fromYaml(Map.of());
    }

    @SuppressWarnings("unchecked")
    public static VastConfig fromYaml(Map<String, Object> root) {
        Map<String, Object> pricing = root.get("pricing") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> b = pricing.get("gpuBroker") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> ps = b.get("providers") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        Map<String, Object> v = ps.get("vast") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
        return new VastConfig(
                v.get("gpuNames") instanceof List<?> l && !l.isEmpty() ? l.stream().map(String::valueOf).toList() : DEFAULT_GPUS,
                num(v.get("minReliability"), 0.97), num(v.get("minDownMbps"), 500), (int) num(v.get("diskGb"), 90),
                num(v.get("maxBandwidthUsd"), 0.60), num(v.get("downloadGb"), 50), num(v.get("uploadGb"), 3),
                Math.max(0.1, num(v.get("amortizeHours"), 0.75)), (int) num(v.get("startTimeoutSeconds"), 480),
                (int) num(v.get("pollSeconds"), 5), Math.max(1, (int) num(v.get("offerAttempts"), 2)),
                Math.max(1.0, num(v.get("priceSlack"), 1.25)), (int) num(v.get("cacheSeconds"), 15));
    }

    private static double num(Object o, double dflt) {
        return o instanceof Number n ? n.doubleValue() : dflt;
    }

    @Configuration
    static class Loader {
        @Bean
        VastConfig vastConfig(@Value("${projectv.config-dir}") String configDir) throws IOException {
            try (InputStream in = Files.newInputStream(Path.of(configDir, "providers.yaml"))) {
                return fromYaml(new Yaml().load(in));
            }
        }
    }
}
