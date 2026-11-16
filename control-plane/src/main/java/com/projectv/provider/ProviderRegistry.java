package com.projectv.provider;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.yaml.snakeyaml.Yaml;
import tools.jackson.databind.ObjectMapper;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Loads config/providers.yaml into the provider table at startup (M1: registry only, no routing). */
@Component
public class ProviderRegistry implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(ProviderRegistry.class);

    private final JdbcClient jdbc;
    private final ObjectMapper json;
    private final Path file;

    public ProviderRegistry(JdbcClient jdbc, ObjectMapper json, @Value("${projectv.config-dir}") String configDir) {
        this.jdbc = jdbc;
        this.json = json;
        this.file = Path.of(configDir, "providers.yaml");
    }

    @Override
    public void run(ApplicationArguments args) throws Exception {
        reload();
    }

    @SuppressWarnings("unchecked")
    public int reload() throws Exception {
        List<Map<String, Object>> providers;
        try (InputStream in = Files.newInputStream(file)) {
            Map<String, Object> root = new Yaml().load(in);
            providers = (List<Map<String, Object>>) root.get("providers");
        }
        for (Map<String, Object> p : providers) {
            Map<String, Object> config = new LinkedHashMap<>(p);
            List.of("id", "name", "kind", "capabilities", "enabled").forEach(config::remove);
            jdbc.sql("""
                            INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
                            VALUES (:id, :name, :kind, CAST(:caps AS jsonb), CAST(:config AS jsonb), :enabled)
                            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind,
                                capabilities_json = EXCLUDED.capabilities_json, config_json = EXCLUDED.config_json,
                                enabled = EXCLUDED.enabled, updated_at = now()
                            """)
                    .param("id", p.get("id"))
                    .param("name", p.get("name"))
                    .param("kind", p.get("kind"))
                    .param("caps", json.writeValueAsString(p.getOrDefault("capabilities", List.of())))
                    .param("config", json.writeValueAsString(config))
                    .param("enabled", Boolean.TRUE.equals(p.getOrDefault("enabled", true)))
                    .update();
        }
        log.info("Provider registry loaded: {} providers from {}", providers.size(), file);
        return providers.size();
    }

    public List<Map<String, Object>> all() {
        return jdbc.sql("SELECT id, name, kind, capabilities_json::text AS capabilities, config_json::text AS config, enabled FROM provider ORDER BY kind, id")
                .query().listOfRows();
    }
}
