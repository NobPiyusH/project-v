package com.projectv.dream;

import com.projectv.assets.AssetStore;
import com.projectv.assets.AssetStore.NewAsset;
import com.projectv.dream.DreamLooks.Look;
import com.projectv.dream.DreamTypes.DreamWorld;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import com.projectv.routing.Router;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * Dream Shorts music (Lyria generation or a piece of the owner's music bed), split out of DreamService unchanged.
 * DreamService keeps the public methods and delegates here; shared helpers (route, cost, tag, dream, shots) stay there.
 */
class DreamMusic {

    private final DreamService svc;
    private final JdbcClient jdbc;
    private final ObjectMapper json;
    private final AssetStore assets;
    private final Pricing pricing;
    private final BudgetGuard budget;
    private final Router router;
    private final DreamEvidence evidence;
    private final String musicModel;
    private final String musicBed;
    private final String listenModel;

    DreamMusic(DreamService svc, JdbcClient jdbc, ObjectMapper json, AssetStore assets, Pricing pricing, BudgetGuard budget,
               Router router, DreamEvidence evidence, DreamSettings settings) {
        this.svc = svc;
        this.jdbc = jdbc;
        this.json = json;
        this.assets = assets;
        this.pricing = pricing;
        this.budget = budget;
        this.router = router;
        this.evidence = evidence;
        this.musicModel = settings.musicModel();
        this.musicBed = settings.musicBed();
        this.listenModel = settings.listenModel();
    }

    @Transactional
    com.projectv.media.MediaTypes.MusicRequest musicRequest(UUID id, String notes) {
        Map<String, Object> d = svc.dream(id);
        Look look = DreamLooks.look((String) d.get("look"));
        DreamWorld w = d.get("world_json") == null ? null : json.readValue(d.get("world_json").toString(), DreamWorld.class);
        double target = svc.shortSeconds(svc.shots(id).size(), look);
        BigDecimal inr = pricing.musicInr(1);
        svc.route(id, "DREAM_MUSIC", "music.dream", DreamService.LYRIA, inr);
        budget.require(inr, "Dream Short music (1 Lyria generation)");
        String world = w == null ? String.valueOf(d.get("idea"))
                : String.join(" ", Objects.toString(w.world(), ""), "Palette:", Objects.toString(w.palette(), ""),
                "Light:", Objects.toString(w.light(), ""));
        String prompt = String.join("\n",
                "Instrumental music only, no vocals, no lyrics. About " + Math.round(target) + " seconds, for a wordless vertical Short.",
                "Feeling: a calm, slightly eerie dream; soft, warm, floating, nostalgic. Gentle start, no hard drums.",
                "Look of the video: " + look.brief(),
                "World: " + world,
                notes == null || notes.isBlank() ? "" : "Owner's notes: " + notes.strip()).strip();
        return new com.projectv.media.MediaTypes.MusicRequest(DreamService.storageId(id), musicModel, prompt, target, 1);
    }

    @Transactional
    String saveMusic(UUID id, com.projectv.media.MediaTypes.MusicResult r) {
        int songs = r.usage() == null || r.usage().isEmpty() ? Math.max(1, r.segments()) : r.usage().size();
        // Recorded as gemini_api: the provider the monthly Gemini cap counts (BudgetGuard.GEMINI).
        svc.cost(id, BudgetGuard.GEMINI, pricing.musicInr(songs), songs, "song", true, "Dream Short music (" + r.model() + ")");
        UUID asset = assets.create(new NewAsset(null, "audio", r.uri(), "audio/wav", null, null, r.durationSec(),
                DreamService.LYRIA, r.model(), null, null, AssetStore.LICENCE_GEMINI, true));
        jdbc.sql("UPDATE asset SET dream_short_id = :d WHERE id = :a").param("d", id).param("a", asset).update();
        jdbc.sql("UPDATE dream_short SET music_asset_id = :a, final_asset_id = NULL, updated_at = now() WHERE id = :d")
                .param("a", asset).param("d", id).update();
        return "Music: %.0f s at about %.0f BPM (%s).".formatted(r.durationSec(), r.bpm(), r.model());
    }

    String musicSource(UUID id) {
        return "lyria".equals(svc.dream(id).get("music_source")) ? "lyria" : "bed";
    }

    /** A new piece of the bed each time (seeded by the Short and how many pieces it has had), away from used starts. */
    DreamTypes.DreamBedRequest musicBedRequest(UUID id) {
        if (musicBed == null || musicBed.isBlank()) {
            throw new IllegalStateException("No music bed is set: put the track's storage address in DREAM_MUSIC_BED "
                    + "(or choose Lyria for this Short)");
        }
        List<Double> used = jdbc.sql("""
                        SELECT p.seed FROM provenance p JOIN asset a ON a.provenance_id = p.id
                        WHERE p.provider = :p AND p.model = :m AND p.seed IS NOT NULL
                        """).param("p", DreamService.BED).param("m", bedName()).query(Long.class).list()
                .stream().map(ms -> ms / 1000.0).toList();
        int takes = jdbc.sql("""
                        SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id
                        WHERE a.dream_short_id = :d AND p.provider = :p
                        """).param("d", id).param("p", DreamService.BED).query(Integer.class).single();
        Map<String, Object> d = svc.dream(id);
        List<Map<String, Object>> shots = svc.shots(id);
        DreamWorld w = d.get("world_json") == null ? null : json.readValue(d.get("world_json").toString(), DreamWorld.class);
        List<String> scenes = shots.stream().map(s -> Objects.toString(s.get("scene"), "")).filter(s -> !s.isBlank()).toList();
        String brief = String.join("\n",
                "Look: " + DreamLooks.look((String) d.get("look")).brief(),
                "Idea: " + d.get("idea"),
                w == null ? "" : "World: " + Objects.toString(w.world(), "") + " Palette: " + Objects.toString(w.palette(), "")
                        + " Light: " + Objects.toString(w.light(), ""),
                scenes.isEmpty() ? "" : "Shots, in order: " + String.join(" | ", scenes)).strip();
        List<String> pictures = shots.stream().map(s -> (String) s.get("picture_uri")).filter(Objects::nonNull).limit(3).toList();
        String model = listenModel;
        try {
            budget.require(DreamService.LISTEN_ESTIMATE_INR, "Dream Short music pick (Gemini listens to the pieces)");
        } catch (BudgetGuard.BudgetExceededException e) {
            model = null; // over the Gemini cap: pick by loudness only, never block the Short over a few paise
        }
        return new DreamTypes.DreamBedRequest(DreamService.storageId(id), musicBed, svc.shortSeconds(shots.size(), DreamLooks.look((String) d.get("look"))),
                id + "#" + takes, used,
                model, brief, pictures, DreamService.BED_CANDIDATES);
    }

    @Transactional
    String saveMusicBed(UUID id, DreamTypes.DreamBedResult r) {
        if (r.usage() != null) {
            evidence.costCheck(id, "MUSIC", DreamService.LISTEN_ESTIMATE_INR.doubleValue(), pricing.textInr(r.usage()).doubleValue());
            svc.cost(id, BudgetGuard.GEMINI, pricing.textInr(r.usage()), r.usage().totalTokens(), "token", false,
                    "Dream Short music pick: Gemini listened to " + r.heard() + " pieces (" + r.model() + ")");
            UUID pick = router.recordExternal(null, "DREAM_MUSIC_PICK", "audio.listen", BudgetGuard.GEMINI,
                    "best fit of " + r.heard() + " pieces: " + Objects.toString(r.why(), ""), List.of(Map.of("provider", BudgetGuard.GEMINI)));
            svc.tag("routing_decision", pick, id);
        }
        UUID decision = router.recordExternal(null, "DREAM_MUSIC", "music.dream", "local_laptop",
                "owner's music bed: free and his own track (Lyria only when chosen for this Short)",
                List.of(Map.of("provider", "local_laptop")));
        svc.tag("routing_decision", decision, id);
        svc.cost(id, "local_laptop", BigDecimal.ZERO, r.durationSec(), "second", false,
                "Dream Short music: the owner's music bed (" + r.bedName() + "), free");
        UUID asset = assets.create(new NewAsset(null, "audio", r.uri(), "audio/wav", null, null, r.durationSec(),
                DreamService.BED, bedName(), "piece from %s to %s of %s".formatted(clock(r.startSec()), clock(r.startSec() + r.durationSec()), musicBed),
                Math.round(r.startSec() * 1000), DreamService.LICENCE_OWNER_MUSIC, false));
        jdbc.sql("UPDATE asset SET dream_short_id = :d WHERE id = :a").param("d", id).param("a", asset).update();
        jdbc.sql("UPDATE dream_short SET music_asset_id = :a, final_asset_id = NULL, updated_at = now() WHERE id = :d")
                .param("a", asset).param("d", id).update();
        String how = r.usage() != null ? "best of " + r.heard() + " pieces by ear: " + r.why()
                : r.why() == null || r.why().isBlank() ? "picked by loudness" : r.why();
        return "Music: %.0f s of your track \"%s\" from %s (%s).".formatted(r.durationSec(), r.bedName(), clock(r.startSec()), how);
    }

    /** The bed's file name, e.g. "7 weeks and 3 days 1 hour.m4a" (used as the provenance model). */
    private String bedName() {
        return musicBed.substring(musicBed.lastIndexOf('/') + 1);
    }

    private static String clock(double sec) {
        long s = Math.round(sec);
        return "%d:%02d".formatted(s / 60, s % 60);
    }
}
