package com.projectv.planner;

import java.util.List;

/**
 * What the Planner decides from a free-text prompt (R1). Field names and allowed values are also the
 * contract the LLM is asked to fill, so keep them simple.
 *
 * @param format          "long_form" or "short"
 * @param aspectRatio     "16:9" or "9:16"
 * @param outputResolution "2160p" (4K) or "1080p"
 * @param narration       "full", "sparse" or "none"
 * @param music           "lead" (music-first edit), "support" or "none"
 * @param visualTypes     subset of: ai_video, ai_image_motion, motion_graphics, stock_footage, text_cards, data_counter
 */
public record VideoPlan(
        String title,
        String summary,
        String format,
        int durationSec,
        String aspectRatio,
        String outputResolution,
        String stylePreset,
        String tone,
        String language,
        boolean needsResearch,
        String narration,
        String voice,
        String music,
        boolean soundDesign,
        List<String> visualTypes,
        int shortsCount,
        List<String> mustInclude,
        String rationale
) {
}
