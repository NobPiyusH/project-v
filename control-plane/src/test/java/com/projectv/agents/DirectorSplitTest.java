package com.projectv.agents;

import com.projectv.agents.AgentTypes.Concept;
import com.projectv.agents.AgentTypes.PlannedShot;
import com.projectv.agents.AgentTypes.ShotPlan;
import com.projectv.agents.DirectorAgent.TimedLine;
import com.projectv.planner.StylePresets;
import com.projectv.planner.VideoPlan;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mockito;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;

/** Shot plan output-size guard (flag projectv.cost.shot-plan-split-tokens): one call when small, two when the output would near the ceiling. */
class DirectorSplitTest {

    private static VideoPlan plan(int seconds) {
        return new VideoPlan("TV", "s", "long_form", seconds, "16:9", "2160p", "shorts_punchy", "calm", "en", false, "full", "", "support",
                true, List.of(), 0, List.of(), "");
    }

    private static final Concept CONCEPT = new Concept("log", "idea", null, List.of(), null);

    private static List<TimedLine> lines(int n) {
        List<TimedLine> out = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            out.add(new TimedLine(i, "line " + i, true, 10.0));
        }
        return out;
    }

    private static PlannedShot shot(int line, String desc) {
        return new PlannedShot("S", line, 5, "wide", "static", desc, "k", "m", "standard", null, null, null, null, null);
    }

    private DirectorAgent director(Llm llm, int splitTokens) throws Exception {
        DirectorAgent d = new DirectorAgent(llm, new StylePresets("src/test/resources/config"), "claude:claude-sonnet-5-5");
        d.setSplitTokens(splitTokens);
        return d;
    }

    @Test
    void offByDefaultIsOneCallEvenForALongPlan() throws Exception {
        Llm llm = Mockito.mock(Llm.class);
        Mockito.when(llm.call(any(), eq("SHOT_PLAN"), anyString(), anyString(), eq(ShotPlan.class), anyString()))
                .thenReturn(new ShotPlan(List.of(shot(0, "a"))));
        director(llm, 0).plan(null, plan(900), CONCEPT, lines(20), 100, 900, null);
        Mockito.verify(llm, Mockito.times(1)).call(any(), eq("SHOT_PLAN"), anyString(), anyString(), eq(ShotPlan.class), anyString());
    }

    @Test
    void aSmallPlanStaysOneCallEvenWithTheFlagOn() throws Exception {
        Llm llm = Mockito.mock(Llm.class);
        Mockito.when(llm.call(any(), eq("SHOT_PLAN"), anyString(), anyString(), eq(ShotPlan.class), anyString()))
                .thenReturn(new ShotPlan(List.of(shot(0, "a"))));
        director(llm, 12000).plan(null, plan(180), CONCEPT, lines(8), 100, 180, null);
        Mockito.verify(llm, Mockito.times(1)).call(any(), eq("SHOT_PLAN"), anyString(), anyString(), eq(ShotPlan.class), anyString());
    }

    @Test
    void aLargePlanIsSplitInTwoAndMergedInOrder() throws Exception {
        Llm llm = Mockito.mock(Llm.class);
        List<String> users = new ArrayList<>();
        Mockito.when(llm.call(any(), eq("SHOT_PLAN"), anyString(), anyString(), eq(ShotPlan.class), anyString())).thenAnswer(i -> {
            users.add(i.getArgument(3));
            return users.size() == 1 ? new ShotPlan(List.of(shot(0, "first a"), shot(5, "first b")))
                    : new ShotPlan(List.of(shot(6, "second a"), shot(11, "second b")));
        });
        ShotPlan merged = director(llm, 12000).plan(null, plan(900), CONCEPT, lines(12), 100, 900, null);
        assertEquals(2, users.size());
        assertEquals(List.of("first a", "first b", "second a", "second b"), merged.shots().stream().map(PlannedShot::description).toList());
        assertTrue(users.get(0).contains("PART 1 of 2") && users.get(0).contains("0 | voice") && !users.get(0).contains("\n11 | voice"));
        assertTrue(users.get(1).contains("PART 2 of 2") && users.get(1).contains("11 | voice") && !users.get(1).contains("\n0 | voice"));
        assertTrue(users.get(1).contains("first b"), "part 2 is told where part 1 ended");
    }

    @Test
    void theTwoHalvesAskForTheirOwnShareOfTheSeconds() throws Exception {
        Llm llm = Mockito.mock(Llm.class);
        ArgumentCaptor<String> system = ArgumentCaptor.forClass(String.class);
        Mockito.when(llm.call(any(), eq("SHOT_PLAN"), system.capture(), anyString(), eq(ShotPlan.class), anyString()))
                .thenReturn(new ShotPlan(List.of(shot(0, "a"))));
        director(llm, 12000).plan(null, plan(900), CONCEPT, lines(12), 100, 900, null);
        assertTrue(system.getAllValues().get(0).contains("exactly 450 seconds") && system.getAllValues().get(1).contains("exactly 450 seconds"));
    }

    @Test
    void expectedSizeGrowsWithLength() {
        assertTrue(DirectorAgent.expectedOutputTokens(900, false) > DirectorAgent.expectedOutputTokens(180, false));
        assertTrue(DirectorAgent.expectedOutputTokens(60, true) > 1500);
    }
}
