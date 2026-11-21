package com.projectv.dream;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.Map;

/**
 * How long one rented pod will run for a Dream Short, from that Short's own work (21 Nov 2026: the fixed 0.75 h made a
 * 4-clip SVI Short look like $0.52 on a $0.69/h GPU and the daily cap refused it; the real need is about 0.35 h).
 * Pod start + pictures x seconds per picture (per picture model) + clips x seconds per 81 frames + the SVI setup on its
 * first use. Every number is config ({@code projectv.gpu.*}), never a price (rule 7). The daily-cap check before renting
 * and the dashboard GPU card both use {@link #hours}.
 *
 * @param fallbackHours used whenever the Short's work is unknown (pricing.gpuBroker.expectedHours, 0.75)
 */
public record RentalEstimate(double startSeconds, double fluxSecondsPerPicture, double zimageSecondsPerPicture,
                             double secondsPer81Frames, double sviSetupSeconds, double fallbackHours) {

    public static final RentalEstimate DEFAULT = new RentalEstimate(900, 3, 5, 76, 180, 0.75);

    /**
     * Hours for a Short row ({@code dream_short}: shot_count, picture_model, svi_scenes); {@link #fallbackHours} when the
     * row or its shot count is missing.
     */
    public double hours(Map<String, Object> d) {
        if (d == null || !(d.get("shot_count") instanceof Number n) || n.intValue() <= 0) {
            return fallbackHours;
        }
        int clips = n.intValue();
        int sviScenes = d.get("svi_scenes") instanceof Number s ? s.intValue() : 0;
        int pictures = sviScenes > 0 ? sviScenes : clips; // SVI: one picture per scene; otherwise up to one per shot
        String model = d.get("picture_model") == null ? null : d.get("picture_model").toString();
        return hours(pictures, model, clips, sviScenes > 0);
    }

    /** Hours for this much work; {@link #fallbackHours} when the picture model is unknown or the counts make no sense. */
    public double hours(int pictures, String pictureModel, int clips, boolean svi) {
        double perPicture = "flux2-klein".equals(pictureModel) ? fluxSecondsPerPicture
                : "zimage".equals(pictureModel) ? zimageSecondsPerPicture : -1;
        if (perPicture < 0 || pictures < 0 || clips <= 0) {
            return fallbackHours;
        }
        double s = startSeconds + pictures * perPicture + clips * secondsPer81Frames + (svi ? sviSetupSeconds : 0);
        return s / 3600.0;
    }

    @Configuration
    static class Config {
        @Bean
        RentalEstimate rentalEstimate(@Value("${projectv.gpu.pod-start-seconds:900}") double start,
                                      @Value("${projectv.gpu.seconds-per-picture-flux:3}") double flux,
                                      @Value("${projectv.gpu.seconds-per-picture-zimage:5}") double zimage,
                                      @Value("${projectv.gpu.seconds-per-81-frames:76}") double per81,
                                      @Value("${projectv.gpu.svi-setup-seconds:180}") double svi,
                                      com.projectv.gpu.broker.BrokerConfig broker) {
            return new RentalEstimate(start, flux, zimage, per81, svi, broker.expectedHours());
        }
    }
}
