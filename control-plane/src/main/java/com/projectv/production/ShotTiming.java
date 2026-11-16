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

    /**
     * Like {@link #fit}, but some cuts are fixed: {@code anchors} maps a shot index to the time that shot must start
     * (so each narration line begins on its own shot). Rough durations are distributed proportionally between fixed
     * points; only the free cuts are moved to beats. Anchors that would go backwards in time are ignored.
     */
    public static List<double[]> fitAnchored(List<Double> rough, java.util.SortedMap<Integer, Double> anchors, double total,
                                             List<Double> beats, double minShot) {
        int n = rough.size();
        double[] d = new double[n];
        for (int i = 0; i < n; i++) {
            Double v = rough.get(i);
            d[i] = v == null || v <= 0 ? 5.0 : v;
        }
        // Fixed points: shot 0 at 0, valid anchors in increasing time, end at total.
        java.util.TreeMap<Integer, Double> fixed = new java.util.TreeMap<>();
        fixed.put(0, 0.0);
        double lastTime = 0;
        for (var e : anchors.entrySet()) {
            int idx = e.getKey();
            double t = e.getValue();
            if (idx <= 0 || idx >= n || t <= lastTime + 0.3 || t >= total - 0.3) {
                continue;
            }
            fixed.put(idx, t);
            lastTime = t;
        }
        fixed.put(n, total);

        double[] cuts = new double[n + 1];
        boolean[] isFixed = new boolean[n + 1];
        Integer prevIdx = null;
        for (var e : fixed.entrySet()) {
            int idx = e.getKey();
            cuts[idx] = e.getValue();
            isFixed[idx] = true;
            if (prevIdx != null) {
                double span = cuts[idx] - cuts[prevIdx];
                double sum = 0;
                for (int i = prevIdx; i < idx; i++) {
                    sum += d[i];
                }
                double acc = cuts[prevIdx];
                for (int i = prevIdx; i < idx - 1; i++) {
                    acc += d[i] / sum * span;
                    cuts[i + 1] = acc;
                }
            }
            prevIdx = idx;
        }

        if (beats != null && beats.size() > 1) {
            double beatLen = (beats.getLast() - beats.getFirst()) / (beats.size() - 1);
            for (int i = 1; i < n; i++) {
                if (isFixed[i]) {
                    continue;
                }
                double near = nearest(beats, cuts[i]);
                if (Math.abs(near - cuts[i]) <= beatLen * 0.6 && near - cuts[i - 1] >= minShot && cuts[i + 1] - near >= minShot) {
                    cuts[i] = near;
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
