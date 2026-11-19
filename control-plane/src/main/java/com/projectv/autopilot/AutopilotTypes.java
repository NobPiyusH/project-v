package com.projectv.autopilot;

import java.util.List;

/** Small types passed through Temporal for posting (ids and text only). */
public final class AutopilotTypes {

    private AutopilotTypes() {
    }

    /** One upload for the media worker, which has the file store and the owner's YouTube sign-in. */
    public record YoutubeUploadRequest(String runId, String videoUri, String title, String description, List<String> tags,
                                       String privacy) {
    }

    /** {@code videoId} null when it failed ({@code error} says why). */
    public record YoutubeUploadResult(String videoId, String url, String privacy, String error) {
    }

    /** The publishing kit the writer makes for a finished Short. */
    public record PostKit(String title, String description, List<String> hashtags) {
    }
}
