package com.projectv.agents;

import com.projectv.agents.AgentTypes.CreativeReview;
import org.springframework.stereotype.Component;

import java.util.UUID;

/**
 * Creative Director (owner, 17 Nov 2026: "who is the main brain ... checking the sound and images match: not just one
 * word from a sentence"). Reviews the story told by the pictures at three levels (shot, paragraph, whole video) and
 * writes better image prompts for weak shots. The image review on the media worker ({@code workers/activities/review.py})
 * receives the same {@link #RUBRIC}, so the two reviews score alike.
 */
@Component
public class CreativeDirectorAgent {

    /** The scoring rules, shared with the image review (sent to the media worker in each request). */
    public static final String RUBRIC = """
            Judge MEANING, not words. A shot must show the idea of its narration line as it is meant in its paragraph
            and in the whole story. A shot that illustrates one word of the line is a failure, e.g. "light travels for
            billions of years" shown as a flashlight, "the universe expands" shown as a balloon at a party, "the Sun's
            surface" shown as a sunny beach. Abstract ideas need a concrete, truthful visual metaphor that a viewer
            understands within one second.

            Score every item 1-5: 5 excellent, 4 good, 3 weak (should improve), 2 wrong or confusing, 1 contradicts it.

            Shot level (every shot):
            - meaning: does it show the idea of its line, in the context of the paragraph?
            - accuracy: is what we see scientifically/factually right (scale, physics, which side is lit, colours of
              stars, no impossible objects)?
            - continuity: does it belong with its neighbours (style, palette, scale, direction of motion, same subject
              looking the same)?
            - literal: true if it depicts a single word instead of the line's meaning.
            - For any score of 3 or less, write fixPrompt: a complete, self-contained replacement image prompt (subject,
              composition, lens, light, colour, atmosphere) that fixes the problem. Never ask for text, captions or
              real people in the image. Otherwise leave fixPrompt empty.

            Paragraph (act) level, for every act in order:
            - flow: do its shots, in order, explain the paragraph's point (setup -> explanation -> reveal)?
            - keyMoment: does the paragraph's main idea get the strongest, most memorable image (ideally a hero shot)?
            - continuity: do its shots feel like one scene?
            - mood: do the pictures (and the music brief) match the paragraph's emotion?
            - shotsToFix: the shot numbers whose change would fix the paragraph's problem; each of them must also have
              a fixPrompt in the shot list.

            Whole video:
            - arc: hook -> build -> payoff -> ending; does the ending pay off the opening question?
            - hook: do the first 2 seconds stop the scroll?
            - ending: is the last image memorable (for a Short, ideally looping back to the start)?
            - variety: no repeated images or ideas.

            Be strict but fair: 4 is a good professional result. Name concrete problems in plain words.
            """;

    private final Llm llm;

    public CreativeDirectorAgent(Llm llm) {
        this.llm = llm;
    }

    /** Text-only review of a shot plan (before any image money is spent). */
    public CreativeReview reviewShotPlan(UUID videoId, String context) {
        String system = """
                You are the Creative Director of a premium documentary studio. You did not write this shot plan; your
                job is to review it before any images are made, and to fix weak shots.
                """ + RUBRIC + """

                Here you see the shot descriptions and image prompts, not images: judge what the prompts would show.
                Return only the JSON object.""";
        return llm.call(videoId, "CREATIVE_REVIEW", system, context, CreativeReview.class);
    }
}
