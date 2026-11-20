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
                            String musicBed, String listenModel, String briefModel, boolean qualityCheck) {

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
                @Value("${projectv.dream.quality-check:true}") boolean qualityCheck) {
            return new DreamSettings(zimageApp, zimageClass, wanApp, wanClass, publicUrl, autoApproveInr, shotSeconds,
                    musicDir, musicModel, musicBed, listenModel, briefModel, qualityCheck);
        }
    }
}
