package com.projectv.ledger;

import com.projectv.media.MediaTypes.Usage;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** Gemini prices: the calibration everywhere (router included) and the 1 Jan 2027 price rise. */
class PricingTest {

    static Pricing at(String day) throws Exception {
        Pricing p = new Pricing("../config");
        p.clock = Clock.fixed(Instant.parse(day + "T00:00:00Z"), ZoneOffset.UTC);
        return p;
    }

    static double inr(BigDecimal v) {
        return v.doubleValue();
    }

    @Test
    void flashTextAndTtsDoubleOnTheFirstOfJanuary2027() throws Exception {
        Usage million = new Usage(1_000_000, 1_000_000, 0, 2_000_000);
        Pricing before = at("2026-12-31");
        Pricing after = at("2027-01-01");
        double fx = before.fx();
        assertEquals((0.75 + 3.75) * fx * 1.5, inr(before.textInr(million)), 0.01);
        assertEquals((1.50 + 7.50) * fx * 1.5, inr(after.textInr(million)), 0.01);
        assertEquals((0.50 + 9.00) * fx * 1.5, inr(before.ttsInr(million)), 0.01);
        assertEquals((1.00 + 18.00) * fx * 1.5, inr(after.ttsInr(million)), 0.01);
        assertEquals(inr(before.imageInr("1K")), inr(after.imageInr("1K")), 1e-9, "only the announced prices change");
    }

    @Test
    void routerCandidateCostsCarryTheGeminiCalibration() throws Exception {
        // The router scores the rupees Pricing returns: image, music and Omni estimates are already scaled ×1.5.
        Pricing p = at("2026-11-19");
        assertEquals(0.0336 * p.fx() * 1.5, inr(p.imageInr("1K")), 0.001);
        assertEquals(0.08 * p.fx() * 1.5, inr(p.musicInr(1)), 0.001);
    }
}
