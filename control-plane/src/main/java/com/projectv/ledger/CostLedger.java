package com.projectv.ledger;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Every money- or quota-spending job writes a CostEntry (HANDOVER §8 rules, §21). Free calls are logged at ₹0. */
@Service
public class CostLedger {

    private final JdbcClient jdbc;

    public CostLedger(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void record(UUID videoId, String providerId, BigDecimal amountInr, BigDecimal units, String unitType,
                       boolean estimated, String note) {
        jdbc.sql("""
                        INSERT INTO cost_entry (id, video_id, provider_id, amount_inr, units, unit_type, estimated, note)
                        VALUES (:id, :video, :provider, :amount, :units, :unitType, :estimated, :note)
                        """)
                .param("id", UUID.randomUUID())
                .param("video", videoId)
                .param("provider", providerId)
                .param("amount", amountInr)
                .param("units", units)
                .param("unitType", unitType)
                .param("estimated", estimated)
                .param("note", note)
                .update();
        jdbc.sql("UPDATE video SET spent_inr = spent_inr + :amount, updated_at = now() WHERE id = :video")
                .param("amount", amountInr)
                .param("video", videoId)
                .update();
    }

    public List<Map<String, Object>> totalsByProvider() {
        return jdbc.sql("""
                        SELECT provider_id, count(*) AS entries, sum(amount_inr) AS amount_inr, sum(units) AS units,
                               min(unit_type) AS unit_type
                        FROM cost_entry GROUP BY provider_id ORDER BY provider_id
                        """)
                .query().listOfRows();
    }

    public List<Map<String, Object>> forVideo(UUID videoId) {
        return jdbc.sql("""
                        SELECT provider_id, amount_inr, units, unit_type, estimated, note, ts
                        FROM cost_entry WHERE video_id = :video ORDER BY ts
                        """)
                .param("video", videoId)
                .query().listOfRows();
    }
}
