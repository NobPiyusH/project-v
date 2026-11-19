package com.projectv.production;

import org.springframework.stereotype.Service;
import tools.jackson.core.type.TypeReference;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import com.projectv.production.ProductionService.*;

/** What the dashboard shows for a video's production. */
@Service
public class ProductionReadModel extends StageSupport {

    public ProductionReadModel(ProductionDeps d) {
        super(d);
    }

    /** Everything the dashboard shows for a video's production. */
    public Map<String, Object> production(UUID videoId) {
        Map<String, Object> out = new LinkedHashMap<>();
        // Postgres text[] must be converted to JSON in SQL: the JDBC array object itself isn't serialisable.
        out.put("research", jdbc.sql("""
                        SELECT facts_json::text AS facts, to_json(COALESCE(queries, '{}'))::text AS queries, version
                        FROM research WHERE video_id = :v ORDER BY version DESC LIMIT 1
                        """)
                .param("v", videoId).query().listOfRows().stream().findFirst().map(r -> {
                    Map<String, Object> m = new LinkedHashMap<>(r);
                    m.put("queries", json.readValue((String) r.get("queries"), new TypeReference<List<String>>() {
                    }));
                    return m;
                }).orElse(null));
        out.put("concept", latestConcept(videoId).orElse(null));
        out.put("script", latestScript(videoId).orElse(null));
        out.put("music", jdbc.sql("SELECT asset_id, bpm FROM music_cue WHERE video_id = :v").param("v", videoId)
                .query().listOfRows().stream().findFirst().orElse(null));
        List<Map<String, Object>> shots = new ArrayList<>();
        for (Map<String, Object> s : shots(videoId)) {
            Map<String, Object> row = new LinkedHashMap<>(s);
            row.remove("keyframe_uri");
            row.put("prompt_json", json.readValue((String) s.get("prompt_json"), new TypeReference<Map<String, Object>>() {
            }));
            shots.add(row);
        }
        out.put("shots", shots);
        out.put("animaticAssetId", jdbc.sql("SELECT animatic_asset_id FROM video WHERE id = :v").param("v", videoId)
                .query(UUID.class).optional().orElse(null));
        // True when any active keyframe is newer than the animatic: the owner redid shots and should rebuild it.
        out.put("animaticOutdated", jdbc.sql("""
                        SELECT EXISTS (
                            SELECT 1 FROM video v JOIN asset an ON an.id = v.animatic_asset_id
                            JOIN scene sc ON sc.video_id = v.id JOIN shot s ON s.scene_id = sc.id
                            JOIN asset k ON k.id = s.keyframe_asset_id
                            WHERE v.id = :v AND k.created_at > an.created_at)
                        """).param("v", videoId).query(Boolean.class).single());
        // Voice takes newer than the animatic also make it outdated; edited-but-not-revoiced lines are counted.
        List<StoredLine> scriptLines = latestScript(videoId).map(StoredScript::lines).orElse(List.of());
        List<UUID> activeVoice = scriptLines.stream().map(StoredLine::audioAssetId).filter(java.util.Objects::nonNull).toList();
        if (!activeVoice.isEmpty() && !Boolean.TRUE.equals(out.get("animaticOutdated"))) {
            out.put("animaticOutdated", jdbc.sql("""
                            SELECT EXISTS (SELECT 1 FROM video v JOIN asset an ON an.id = v.animatic_asset_id, asset a
                                           WHERE v.id = :v AND a.id IN (:ids) AND a.created_at > an.created_at)
                            """).param("v", videoId).param("ids", activeVoice).query(Boolean.class).single());
        }
        out.put("linesNeedingVoice", scriptLines.stream().filter(l -> Boolean.TRUE.equals(l.needsVoice())).count());
        out.put("redoingLines", scriptLines.stream().filter(l -> "REDOING".equals(l.voiceStatus())).count());
        out.put("redoingShots", jdbc.sql("""
                        SELECT count(*) FROM shot s JOIN scene sc ON sc.id = s.scene_id WHERE sc.video_id = :v AND s.status = 'REDOING'
                        """).param("v", videoId).query(Integer.class).single());
        return out;
    }
}
