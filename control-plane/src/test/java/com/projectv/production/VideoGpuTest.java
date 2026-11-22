package com.projectv.production;

import com.projectv.dream.DreamGpu;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

import java.time.Instant;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class VideoGpuTest {

    static final UUID V = UUID.randomUUID();

    @Test
    void defaultsKeepEveryVideoOnItsCurrentProviders() {
        VideoGpu g = new VideoGpu(Mockito.mock(DreamGpu.class), "wan", "omni", "default", 5);
        assertFalse(g.clips("draft") || g.clips("final") || g.keyframes());
        VideoGpu on = new VideoGpu(Mockito.mock(DreamGpu.class), "broker", "Broker", "broker", 5);
        assertTrue(on.clips("draft") && on.clips("final") && on.keyframes());
    }

    @Test
    void brokerRouteNeedsTheBrokerOn() {
        DreamGpu dream = Mockito.mock(DreamGpu.class);
        when(dream.enabled()).thenReturn(false);
        var e = assertThrows(IllegalStateException.class, () -> new VideoGpu(dream, "broker", "omni", "", 5).session(V, 600));
        assertTrue(e.getMessage().contains("projectv.dream.gpu-provider=broker"));
        verify(dream, never()).session(Mockito.any(), anyDouble());
    }

    @Test
    void sessionHoldsThePodAndReleaseLetsGoOnce() {
        DreamGpu dream = Mockito.mock(DreamGpu.class);
        when(dream.enabled()).thenReturn(true);
        when(dream.session(V, 0.5)).thenReturn(new DreamGpu.Session("http://pod", 60));
        VideoGpu g = new VideoGpu(dream, "broker", "omni", "", 5);
        assertTrue(g.session(V, 1800).url().equals("http://pod"));
        verify(dream).hold(V);
        g.release(V, "done");
        g.release(V, "again");
        verify(dream, times(1)).release(V, "done");
        verify(dream).unhold(V);
        verify(dream, never()).release(Mockito.eq(V), Mockito.eq("again"));
    }

    @Test
    void aFailedRentalLeavesNoHold() {
        DreamGpu dream = Mockito.mock(DreamGpu.class);
        when(dream.enabled()).thenReturn(true);
        when(dream.session(V, 0.25)).thenThrow(new IllegalStateException("daily cap"));
        VideoGpu g = new VideoGpu(dream, "broker", "omni", "", 5);
        assertThrows(IllegalStateException.class, () -> g.session(V, 900));
        verify(dream).unhold(V);
        g.releaseIdle(Instant.now().plusSeconds(3600));
        verify(dream, never()).release(Mockito.any(), anyString());
    }

    @Test
    void anIdleHoldIsReleasedAfterTheIdleMinutes() {
        DreamGpu dream = Mockito.mock(DreamGpu.class);
        when(dream.enabled()).thenReturn(true);
        when(dream.session(V, 0.5)).thenReturn(new DreamGpu.Session("http://pod", 60));
        Instant used = Instant.parse("2026-11-22T10:00:00Z");
        when(dream.lastActivity(V)).thenReturn(Optional.of(used));
        VideoGpu g = new VideoGpu(dream, "broker", "omni", "", 5);
        g.session(V, 1800);
        g.releaseIdle(used.plusSeconds(4 * 60));
        verify(dream, never()).release(Mockito.any(), anyString());
        g.releaseIdle(used.plusSeconds(5 * 60));
        verify(dream).release(Mockito.eq(V), anyString());
    }
}
