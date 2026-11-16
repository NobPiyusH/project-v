package com.projectv.production;

import com.projectv.agents.AgentTypes.ActCritique;
import com.projectv.agents.AgentTypes.CreativeReview;
import com.projectv.agents.AgentTypes.ShotCritique;
import com.projectv.agents.AgentTypes.VideoCritique;
import com.projectv.media.MediaTypes.Finding;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CreativeDirectorTest {

    private static final Map<Integer, Map<String, Object>> SHOTS = Map.of(
            1, Map.of("id", "s1", "ord", 0), 2, Map.of("id", "s2", "ord", 1), 3, Map.of("id", "s3", "ord", 2));

    private static final VideoCritique GOOD_VIDEO = new VideoCritique(4, 5, 4, 4, "strong", "");

    private static ShotCritique shot(int n, int meaning, boolean literal, String fix) {
        return new ShotCritique(n, meaning, 5, 5, literal, "problem " + n, fix);
    }

    private static List<String> sev(List<Finding> f) {
        return f.stream().map(x -> x.severity() + ":" + x.code() + ":" + x.ref()).toList();
    }

    @Test
    void aLiteralKeywordImageFailsAndTargetsItsShotAtTheImageStage() {
        var review = new CreativeReview(GOOD_VIDEO, List.of(), List.of(shot(2, 3, true, "light leaving a galaxy..."),
                shot(1, 5, false, "")));
        List<Finding> f = CreativeDirector.findings(review, SHOTS, true, Set.of());
        assertEquals(List.of("FAIL:CD_SHOT:shot 2"), sev(f));
        assertEquals("s2", f.getFirst().targetId());
        assertTrue(f.getFirst().message().contains("shows one word instead of the idea"));
    }

    @Test
    void weakShotPromptsAreRewrittenAtTheShotPlanAndOnlyReportedAsInfo() {
        var review = new CreativeReview(GOOD_VIDEO, List.of(), List.of(shot(3, 2, false, "better prompt")));
        assertEquals(List.of("INFO:CD_PROMPT_IMPROVED:shot 3"),
                sev(CreativeDirector.findings(review, SHOTS, false, Set.of(3))));
    }

    @Test
    void aWeakParagraphFailsTheShotPlanUnlessItsShotsWereAllRewritten() {
        var act = new ActCritique("The Speed Limit of Time", 2, 3, 4, 4, "", "the reveal has no strong image", List.of(2, 3));
        var review = new CreativeReview(GOOD_VIDEO, List.of(act), List.of());
        assertEquals(List.of("FAIL:CD_PARAGRAPH:paragraph “The Speed Limit of Time”"),
                sev(CreativeDirector.findings(review, SHOTS, false, Set.of(2))));
        assertEquals(List.of("INFO:CD_PARAGRAPH_FIXED:paragraph “The Speed Limit of Time”"),
                sev(CreativeDirector.findings(review, SHOTS, false, Set.of(2, 3))));
    }

    @Test
    void aWeakParagraphOfImagesTargetsTheShotsThatFixIt() {
        var act = new ActCritique("Act", 4, 2, 4, 4, "", "key moment is a filler shot", List.of(3));
        List<Finding> f = CreativeDirector.findings(new CreativeReview(GOOD_VIDEO, List.of(act), List.of()), SHOTS, true, Set.of());
        assertEquals(List.of("FAIL:CD_PARAGRAPH:shot 3"), sev(f));
        assertEquals("s3", f.getFirst().targetId());
    }

    @Test
    void aWeakArcFailsThePlanButOnlyWarnsAtTheImages() {
        var review = new CreativeReview(new VideoCritique(2, 4, 3, 4, "", "the ending doesn't answer the hook"),
                List.of(), List.of());
        assertEquals(List.of("FAIL:CD_VIDEO:whole video"), sev(CreativeDirector.findings(review, SHOTS, false, Set.of())));
        List<Finding> images = CreativeDirector.findings(review, SHOTS, true, Set.of());
        assertEquals(List.of("WARN:CD_VIDEO:whole video"), sev(images));
        assertNull(images.getFirst().targetId());
    }

    @Test
    void goodScoresProduceNoFindings() {
        var review = new CreativeReview(GOOD_VIDEO, List.of(new ActCritique("A", 4, 5, 4, 4, "ok", "", List.of())),
                List.of(shot(1, 4, false, ""), shot(2, 5, false, "")));
        assertEquals(List.of(), CreativeDirector.findings(review, SHOTS, true, Set.of()));
    }
}
