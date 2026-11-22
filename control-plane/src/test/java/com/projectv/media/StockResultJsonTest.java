package com.projectv.media;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

/** The worker returns extra fields (sourceWidth/sourceHeight); an unknown field must never wedge a workflow. */
class StockResultJsonTest {
    @Test
    void ignoresFieldsTheWorkerAddsLater() throws Exception {
        String json = "{\"found\":true,\"reason\":null,\"queries\":[\"q\"],\"skipped\":[],\"uri\":\"s3://b/x.mp4\","
                + "\"width\":1920,\"height\":1080,\"durationSec\":4.0,\"sourceWidth\":3840,\"sourceHeight\":2160,"
                + "\"provenance\":{\"source\":\"pixabay\",\"sourceId\":\"1\",\"pageUrl\":\"u\",\"author\":\"a\","
                + "\"licence\":\"l\",\"licenceUrl\":\"\",\"attribution\":\"\",\"creditRequired\":false,"
                + "\"fetchedAt\":\"t\",\"sha256\":\"h\",\"futureField\":1}}";
        var r = new ObjectMapper().readValue(json, MediaTypes.StockResult.class);
        assertTrue(r.found());
        assertEquals(1080, r.height());
    }
}
