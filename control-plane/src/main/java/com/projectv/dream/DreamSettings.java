package com.projectv.dream;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.math.BigDecimal;

/**
 * Dream Shorts settings in one place (they used to be thirteen constructor parameters of DreamService). The keys are
 * spread over several property prefixes ({@code projectv.modal}, {@code projectv.video}, {@code projectv.models},
 * {@code projectv.dream}), so they are bound by {@link Config} with {@code @Value} and unchanged defaults.
 */
public record DreamSettings(String zimageApp, String zimageClass, String wanApp, String wanClass, String publicUrl,
                            BigDecimal autoApproveInr, double shotSeconds, String musicDir, String musicModel,
                            String musicBed, String listenModel, String briefModel, boolean qualityCheck,
                            String pictureModel) {

    /** Pod picture models the pod worker knows (workers/pod/server.py PICTURE_MODELS). */
    public static final java.util.List<String> PICTURE_MODELS = java.util.List.of("flux2-klein", "zimage");

    /** Tests and older callers: the studio default picture model (FLUX.2 klein, owner's pick 21 Nov 2026). */
    public DreamSettings(String zimageApp, String zimageClass, String wanApp, String wanClass, String publicUrl,
                         BigDecimal autoApproveInr, double shotSeconds, String musicDir, String musicModel,
                         String musicBed, String listenModel, String briefModel, boolean qualityCheck) {
        this(zimageApp, zimageClass, wanApp, wanClass, publicUrl, autoApproveInr, shotSeconds, musicDir, musicModel,
                musicBed, listenModel, briefModel, qualityCheck, "flux2-klein");
    }

    /** A known pod picture model, else the given default. */
    public static String cleanPictureModel(String m, String fallback) {
        String v = m == null ? "" : m.strip().toLowerCase();
        return PICTURE_MODELS.contains(v) ? v : fallback;
    }

    @Configuration
    static class Config {
        @Bean
        DreamSettings dreamSettings(
                @Value("${projectv.modal.zimage-app:projectv-zimage}") String zimageApp,
                @Value("${projectv.modal.zimage-class:ZImage}") String zimageClass,
                @Value("${projectv.video.modal-app:projectv-wan}") String wanApp,
                @Value("${projectv.video.modal-class:Wan}") String wanClass,
                @Value("${projectv.public-url:http://127.0.0.1:8080}") String publicUrl,
                @Value("${projectv.dream.auto-approve-inr:0}") BigDecimal autoApproveInr,
                @Value("${projectv.dream.shot-seconds:3.5}") double shotSeconds,
                @Value("${projectv.dream.music-dir:}") String musicDir,
                @Value("${projectv.models.music:lyria-3.5}") String musicModel,
                @Value("${projectv.dream.music-bed:}") String musicBed,
                @Value("${projectv.models.vision:gemini-3.6-flash}") String listenModel,
                @Value("${projectv.models.story:claude:claude-sonnet-5-5}") String briefModel,
                @Value("${projectv.dream.quality-check:true}") boolean qualityCheck,
                @Value("${projectv.dream.picture-model:flux2-klein}") String pictureModel) {
            return new DreamSettings(zimageApp, zimageClass, wanApp, wanClass, publicUrl, autoApproveInr, shotSeconds,
                    musicDir, musicModel, musicBed, listenModel, briefModel, qualityCheck,
                    cleanPictureModel(pictureModel, "flux2-klein"));
        }
    }
}
