package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.ledger.BudgetGuard;
import com.projectv.media.MediaTypes.MusicRequest;
import com.projectv.media.MediaTypes.MusicResult;
import com.projectv.planner.VideoPlan;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.Map;
import java.util.UUID;
import com.projectv.production.ProductionService.*;

/** Score request (Lyria) and storing the beat-mapped music cue. */
@Service
public class MusicStage extends StageSupport {

    public MusicStage(ProductionDeps d) {
        super(d);
    }

    public MusicRequest musicRequest(UUID videoId, String ownerNotes) {
        VideoPlan plan = plan(videoId);
        StoredScript script = latestScript(videoId).orElse(null);
        double target = plan.durationSec();
        int maxSegments = (int) Math.min(6, Math.ceil(target / 55.0) + 1);
        budget.require(pricing.musicInr(maxSegments), "MUSIC (" + maxSegments + " Lyria generations at most)");
        String brief = script != null && script.musicBrief() != null ? script.musicBrief() : "Cinematic score matching the tone: " + plan.tone();
        String prompt = """
                Instrumental film score, no vocals, no lyrics. About %d seconds long.
                Mood and style: %s
                Video: %s (%s). It must leave room for a narrator's voice: avoid busy melodies in the mid range.
                %s""".formatted(Math.round(target), brief, plan.title(), plan.tone(),
                ownerNotes == null || ownerNotes.isBlank() ? "" : "Owner's notes: " + ownerNotes);
        return new MusicRequest(videoId.toString(), musicModel, prompt, target, maxSegments);
    }

    @Transactional
    public StageResult saveMusic(UUID videoId, MusicResult r) {
        int songs = r.usage() == null ? r.segments() : r.usage().size();
        ledger.record(videoId, BudgetGuard.GEMINI, pricing.musicInr(songs), BigDecimal.valueOf(songs), "song", true,
                "MUSIC (" + r.model() + ")");
        UUID assetId = assets.create(new NewAsset(videoId, "audio", r.uri(), "audio/wav", null, null, r.durationSec(),
                "gemini_api", r.model(), null, null, AssetStore.LICENCE_GEMINI, true));
        jdbc.sql("DELETE FROM music_cue WHERE video_id = :v").param("v", videoId).update();
        jdbc.sql("""
                        INSERT INTO music_cue (id, video_id, asset_id, bpm, beat_times_json, sections_json)
                        VALUES (:id, :v, :a, :bpm, CAST(:beats AS jsonb), CAST(:sections AS jsonb))
                        """)
                .param("id", UUID.randomUUID()).param("v", videoId).param("a", assetId).param("bpm", r.bpm())
                .param("beats", json.writeValueAsString(r.beatTimes()))
                .param("sections", json.writeValueAsString(Map.of("downbeats", r.downbeatTimes())))
                .update();
        return StageResult.of("Score %.0fs at %.0f BPM, %d beats (%d Lyria generation%s).".formatted(r.durationSec(),
                        r.bpm(), r.beatTimes().size(), songs, songs == 1 ? "" : "s"),
                json.writeValueAsString(Map.of("assetId", assetId, "bpm", r.bpm(), "durationSec", r.durationSec())));
    }
}
