package com.projectv.production;

import java.util.ArrayList;
import java.util.List;

/**
 * Fits the director's rough shot durations to the exact total, then moves each cut to the nearest music beat when
 * that keeps every shot at least {@code minShot} long. Pure function: unit-tested.
 */
public final class ShotTiming {

    private ShotTiming() {
    }

    /** @return per shot {startSec, durationSec}, summing exactly to {@code total} */
    public static List<double[]> fit(List<Double> rough, double total, List<Double> beats, double minShot) {
        int n = rough.size();
        double[] d = new double[n];
        double sum = 0;
        for (int i = 0; i < n; i++) {
            Double v = rough.get(i);
            d[i] = v == null || v <= 0 ? 5.0 : v;
            sum += d[i];
        }
        double scale = total / sum;
        double[] cuts = new double[n + 1];
        for (int i = 0; i < n; i++) {
            cuts[i + 1] = cuts[i] + d[i] * scale;
        }
        cuts[n] = total;

        if (beats != null && beats.size() > 1) {
            double beatLen = (beats.getLast() - beats.getFirst()) / (beats.size() - 1);
            for (int i = 1; i < n; i++) {
                double nearest = nearest(beats, cuts[i]);
                boolean close = Math.abs(nearest - cuts[i]) <= beatLen * 0.6;
                boolean keepsMin = nearest - cuts[i - 1] >= minShot && cuts[i + 1] - nearest >= minShot;
                if (close && keepsMin) {
                    cuts[i] = nearest;
                }
            }
        }

        List<double[]> out = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            out.add(new double[]{cuts[i], cuts[i + 1] - cuts[i]});
        }
        return out;
    }

    private static double nearest(List<Double> beats, double t) {
        double best = beats.getFirst();
        for (double b : beats) {
            if (Math.abs(b - t) < Math.abs(best - t)) {
                best = b;
            }
        }
        return best;
    }
}
