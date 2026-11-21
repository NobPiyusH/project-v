package com.projectv.gpu.broker;

import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

class WorkerAuthTest {

    final WorkerAuth auth = new WorkerAuth("s".getBytes(), Set.of("vast", "runpod"));

    @Test
    void tokenIsStableForAPodNameAndDifferentPerRental() {
        String t = auth.token("projectv-vast-abcd1234-ef01");
        assertEquals(48, t.length());
        assertTrue(t.matches("[0-9a-f]{48}"), "URL-safe and env-safe");
        assertEquals(t, new WorkerAuth("s".getBytes(), Set.of("vast")).token("projectv-vast-abcd1234-ef01"), "same after a restart");
        assertNotEquals(t, auth.token("projectv-vast-abcd1234-ef02"));
        assertNotEquals(t, new WorkerAuth("other".getBytes(), Set.of("vast")).token("projectv-vast-abcd1234-ef01"));
    }

    @Test
    void secretDerivedFromTheProviderKeysWhenNoneIsSetAndRandomWhenThereAreNoKeys() {
        WorkerAuth a = new WorkerAuth("", "rk", "vk", true);
        WorkerAuth b = new WorkerAuth("", "rk", "vk", true);
        assertEquals(a.token("n"), b.token("n"), "stable across control-plane restarts without any new setting");
        assertFalse(a.token("n").contains("rk") || a.token("n").contains("vk"), "the keys cannot be read back from a token");
        assertNotEquals(new WorkerAuth("", "", "", true).token("n"), new WorkerAuth("", "", "", true).token("n"), "random per run when nothing is set");
        assertNotEquals(a.token("n"), new WorkerAuth("explicit", "rk", "vk", true).token("n"));
    }

    @Test
    void envAndAddressOnlyForEnabledProviders() {
        Map<String, String> in = Map.of("SV_PORT", "8000");
        Map<String, String> out = auth.env("vast", "projectv-vast-x", in);
        assertEquals("8000", out.get("SV_PORT"));
        assertEquals(auth.token("projectv-vast-x"), out.get("SV_TOKEN"));
        assertFalse(in.containsKey("SV_TOKEN"), "the caller's map is not changed");
        assertEquals("http://worker:" + auth.token("projectv-vast-x") + "@1.2.3.4:41000", auth.decorate("vast", "projectv-vast-x", "http://1.2.3.4:41000"));
        assertEquals("https://worker:" + auth.token("p") + "@id-8000.proxy.runpod.net", auth.decorate("runpod", "p", "https://id-8000.proxy.runpod.net"));
        assertSame(in, auth.env("jarvislabs", "n", in));
        assertEquals("http://x", auth.decorate("jarvislabs", "n", "http://x"));
        assertEquals(null, auth.decorate("vast", "n", null));
        String once = auth.decorate("vast", "n", "http://1.2.3.4:1");
        assertEquals(once, auth.decorate("vast", "n", once), "never decorated twice");
    }

    @Test
    void runpodTokenCanBeSwitchedOffButVastAlwaysHasIt() {
        WorkerAuth a = new WorkerAuth("s", "", "", false);
        assertTrue(a.enabledFor("vast"));
        assertFalse(a.enabledFor("runpod"));
        assertFalse(WorkerAuth.OFF.enabledFor("vast"));
    }
}
