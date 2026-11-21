package com.projectv.gpu.broker;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** RFC 6238 Appendix B test vectors (SHA-1; secret "12345678901234567890"). */
class TotpTest {

    static final String SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"; // base32 of the RFC's ASCII secret

    @Test
    void rfc6238VectorsEightDigits() {
        assertEquals("94287082", Totp.code(SEED, 59L, 8));
        assertEquals("07081804", Totp.code(SEED, 1111111109L, 8));
        assertEquals("14050471", Totp.code(SEED, 1111111111L, 8));
        assertEquals("89005924", Totp.code(SEED, 1234567890L, 8));
        assertEquals("69279037", Totp.code(SEED, 2000000000L, 8));
        assertEquals("65353130", Totp.code(SEED, 20000000000L, 8));
    }

    @Test
    void sixDigitCodesAreTheLastSixOfTheSameValueAndKeepLeadingZeros() {
        assertEquals("287082", Totp.code(SEED, 59L, 6));
        assertEquals("081804", Totp.code(SEED, 1111111109L, 6));
        assertEquals("005924", Totp.code(SEED, 1234567890L, 6));
    }

    @Test
    void base32IgnoresCaseSpacesAndPaddingAndRejectsTypos() {
        assertEquals(Totp.code(SEED, 59L, 6), Totp.code("gezd gnbv-gy3t qojq gezd gnbv gy3t qojq====", 59L, 6));
        assertThrows(IllegalArgumentException.class, () -> Totp.code("GEZD1NBV", 59L, 6)); // '1' is not base32
        assertThrows(IllegalArgumentException.class, () -> Totp.code("", 59L, 6));
    }

    @Test
    void windowCountdownIsWithinOneStep() {
        assertEquals(30_000L, Totp.millisToNextWindow(0));
        assertEquals(1L, Totp.millisToNextWindow(29_999));
    }
}
