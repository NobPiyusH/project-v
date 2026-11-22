package com.projectv.config;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.temporal.common.converter.DataConverter;
import io.temporal.common.converter.DefaultDataConverter;
import io.temporal.common.converter.JacksonJsonPayloadConverter;

/**
 * Temporal payloads are read back into Java records. A worker that returns one field more than its record has (22 Nov 2026:
 * stock_fetch sourceWidth/sourceHeight) must never wedge a workflow on deserialization, so unknown fields are ignored for every
 * activity result. Only reading is relaxed; what is written is unchanged, so running workflows replay as before.
 */
public final class LenientConverters {

    private LenientConverters() {
    }

    public static ObjectMapper mapper() {
        ObjectMapper m = JacksonJsonPayloadConverter.newDefaultObjectMapper();
        m.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);
        return m;
    }

    public static DataConverter dataConverter() {
        return DefaultDataConverter.newDefaultInstance().withPayloadConverterOverrides(new JacksonJsonPayloadConverter(mapper()));
    }
}
