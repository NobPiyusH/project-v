package com.projectv.assets;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.Map;
import java.util.UUID;

/**
 * Every generated asset gets an asset row AND a provenance row (HANDOVER §8 rules, §22). No orphans.
 */
@Component
public class AssetStore {

    public static final UUID LICENCE_GEMINI = UUID.fromString("00000000-0000-0000-0000-00000000a001");
    public static final UUID LICENCE_ZIMAGE = UUID.fromString("00000000-0000-0000-0000-00000000a002");
    public static final UUID LICENCE_OWN_RENDER = UUID.fromString("00000000-0000-0000-0000-00000000a003");
    public static final UUID LICENCE_WAN = UUID.fromString("00000000-0000-0000-0000-00000000a004");

    public record NewAsset(UUID videoId, String kind, String uri, String mimeType, Integer width, Integer height,
                           Double durationSec, String provider, String model, String prompt, Long seed, UUID licenceId,
                           boolean aiGenerated) {
    }

    private final JdbcClient jdbc;

    public AssetStore(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional
    public UUID create(NewAsset a) {
        UUID provenanceId = UUID.randomUUID();
        UUID assetId = UUID.randomUUID();
        jdbc.sql("""
                        INSERT INTO provenance (id, provider, model, prompt, seed, licence_id, ai_generated, created_by)
                        VALUES (:id, :provider, :model, :prompt, :seed, :licence, :ai, 'projectv')
                        """)
                .param("id", provenanceId).param("provider", a.provider()).param("model", a.model())
                .param("prompt", a.prompt()).param("seed", a.seed()).param("licence", a.licenceId())
                .param("ai", a.aiGenerated()).update();
        jdbc.sql("""
                        INSERT INTO asset (id, video_id, kind, uri_hot, mime_type, width, height, duration_sec, provenance_id)
                        VALUES (:id, :video, :kind, :uri, :mime, :w, :h, :dur, :prov)
                        """)
                .param("id", assetId).param("video", a.videoId()).param("kind", a.kind()).param("uri", a.uri())
                .param("mime", a.mimeType()).param("w", a.width()).param("h", a.height()).param("dur", a.durationSec())
                .param("prov", provenanceId).update();
        jdbc.sql("UPDATE provenance SET asset_id = :a WHERE id = :p").param("a", assetId).param("p", provenanceId).update();
        return assetId;
    }

    public String uriOf(UUID assetId) {
        return jdbc.sql("SELECT uri_hot FROM asset WHERE id = :id").param("id", assetId).query(String.class).single();
    }

    public Map<String, Object> describe(UUID assetId) {
        return jdbc.sql("""
                        SELECT a.id, a.kind, a.uri_hot, a.mime_type, a.width, a.height, a.duration_sec,
                               p.provider, p.model, p.prompt, p.seed, l.name AS licence
                        FROM asset a LEFT JOIN provenance p ON p.id = a.provenance_id LEFT JOIN licence l ON l.id = p.licence_id
                        WHERE a.id = :id
                        """)
                .param("id", assetId).query().singleRow();
    }
}
