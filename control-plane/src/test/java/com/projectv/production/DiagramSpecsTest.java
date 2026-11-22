package com.projectv.production;

import com.projectv.agents.Llm;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import tools.jackson.databind.json.JsonMapper;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;

class DiagramSpecsTest {

    private static final UUID VID = UUID.randomUUID();

    private static DiagramSpecs.SceneSpec good() {
        return new DiagramSpecs.SceneSpec("t", 8.0,
                List.of(Map.of("id", "gun", "type", "box", "x", -4, "y", 0, "w", 2, "h", 1, "text", "Gun"),
                        Map.of("id", "b", "type", "beam", "x1", -3, "y1", 0, "x2", 3, "y2", 0)),
                List.of(Map.of("at", 0.5, "dur", 1.0, "do", "show", "target", "gun"),
                        Map.of("at", 2.0, "dur", 3.0, "do", "flow", "target", "b")));
    }

    private static DiagramSpecs.SceneSpec bad() {
        return new DiagramSpecs.SceneSpec("t", 8.0, List.of(Map.of("id", "gun", "type", "box", "x", 40, "y", 0, "w", 2, "h", 1)),
                List.of(Map.of("at", 0.5, "dur", 1.0, "do", "teleport", "target", "gun")));
    }

    private static DiagramSpecs specs(Llm llm) {
        return new DiagramSpecs(llm, JsonMapper.builder().build(), true);
    }

    @Test
    void validSpecIsUsedAsIs() {
        Llm llm = Mockito.mock(Llm.class);
        Mockito.when(llm.call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class))).thenReturn(good());
        Map<String, Object> m = specs(llm).specFor(VID, "TV", "Electrons are fired", "a gun fires", 7.2);
        assertEquals(8.0, m.get("duration"), "duration is set from the shot, rounded up");
        assertFalse(m.containsKey("fallback"));
        Mockito.verify(llm, Mockito.times(1)).call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class));
    }

    @Test
    void invalidSpecGetsExactlyOneRepairWithTheErrorsShown() {
        Llm llm = Mockito.mock(Llm.class);
        List<String> prompts = new ArrayList<>();
        Mockito.when(llm.call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class))).thenAnswer(i -> {
            prompts.add(i.getArgument(3));
            return prompts.size() == 1 ? bad() : good();
        });
        Map<String, Object> m = specs(llm).specFor(VID, "TV", "line", "desc", 8);
        assertFalse(m.containsKey("fallback"));
        assertEquals(2, prompts.size());
        assertTrue(prompts.get(1).contains("outside the frame") && prompts.get(1).contains("teleport"));
    }

    @Test
    void stillInvalidAfterRepairFallsBackToATextCard() {
        Llm llm = Mockito.mock(Llm.class);
        Mockito.when(llm.call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class))).thenReturn(bad());
        Map<String, Object> m = specs(llm).specFor(VID, "TV", "Magnets steer the beam", "Beam steering", 6);
        assertEquals(true, m.get("fallback"));
        assertEquals(List.of("Magnets steer the beam"), m.get("lines"));
        assertEquals("Beam steering", m.get("title"));
        Mockito.verify(llm, Mockito.times(2)).call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class));
    }

    @Test
    void aFailingModelFallsBackWithoutRetrying() {
        Llm llm = Mockito.mock(Llm.class);
        Mockito.when(llm.call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class)))
                .thenThrow(new IllegalStateException("model down"));
        Map<String, Object> m = specs(llm).specFor(VID, "TV", "line", "desc", 6);
        assertEquals(true, m.get("fallback"));
        assertTrue(m.get("fallbackReason").toString().contains("model down"));
        Mockito.verify(llm, Mockito.times(1)).call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class));
    }

    @Test
    void shotsAreRecognisedByLaneAndKeepNoKeyframeFinding() {
        Map<String, Object> shot = Map.of("id", "s1", "ord", 0, "prompt_json", "{\"lane\": \"diagram\", \"diagramSpec\": {}}");
        assertTrue(StageSupport.diagramShot(shot));
        assertFalse(StageSupport.diagramShot(Map.of("id", "s2", "prompt_json", "{\"lane\": \"ai_video\"}")));
        assertFalse(StageSupport.diagramShot(Map.of("id", "s3")));
        assertTrue(Inspector.keyframes(List.of(shot)).isEmpty());
        assertEquals(1, Inspector.keyframes(List.of(Map.of("id", "s2", "ord", 1))).size());
    }
}
