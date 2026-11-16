package com.projectv.planner;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

/** Loads config/style-presets.yaml. */
@Component
public class StylePresets {

    /** @param visualStyle appended to every image/video prompt so all shots share one look */
    public record Preset(String id, String name, String description, String visualStyle, Map<String, Object> defaults) {
    }

    private final List<Preset> presets;

    public StylePresets(@Value("${projectv.config-dir}") String configDir) throws IOException {
        Path file = Path.of(configDir, "style-presets.yaml");
        try (InputStream in = Files.newInputStream(file)) {
            Map<String, Object> root = new Yaml().load(in);
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> raw = (List<Map<String, Object>>) root.get("presets");
            this.presets = raw.stream().map(StylePresets::toPreset).toList();
        }
        if (presets.isEmpty()) {
            throw new IllegalStateException("No style presets in " + file.toAbsolutePath());
        }
    }

    @SuppressWarnings("unchecked")
    private static Preset toPreset(Map<String, Object> m) {
        Object defaults = m.getOrDefault("defaults", Map.of());
        Object visual = m.get("visualStyle");
        return new Preset((String) m.get("id"), (String) m.get("name"),
                String.valueOf(m.get("description")).trim(), visual == null ? "" : String.valueOf(visual).trim(),
                (Map<String, Object>) defaults);
    }

    public List<Preset> all() {
        return presets;
    }

    public Preset byIdOrDefault(String id) {
        return presets.stream().filter(p -> p.id().equals(id)).findFirst().orElse(presets.getFirst());
    }

    public boolean exists(String id) {
        return presets.stream().anyMatch(p -> p.id().equals(id));
    }
}
