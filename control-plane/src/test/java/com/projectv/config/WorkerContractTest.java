package com.projectv.config;

import com.projectv.workflow.MediaActivities;
import io.temporal.common.converter.DataConverter;
import io.temporal.api.common.v1.Payload;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Method;
import java.lang.reflect.ParameterizedType;
import java.lang.reflect.Type;
import java.util.LinkedHashSet;
import java.util.Optional;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Worker -> Java contract: every result type a Python worker activity returns (and every record nested in it) must be readable
 * when the worker adds a field the record does not have yet. 22 Nov 2026: stock_fetch returned sourceWidth/sourceHeight, the Java
 * record rejected it and the television video's workflow wedged. The Python side of the contract (which keys exist) is
 * workers/tests/test_java_contract.py.
 */
class WorkerContractTest {

    private static void collect(Type t, Set<Class<?>> out) {
        if (t instanceof ParameterizedType p) {
            for (Type a : p.getActualTypeArguments()) {
                collect(a, out);
            }
        } else if (t instanceof Class<?> c) {
            if (c.isArray()) {
                collect(c.getComponentType(), out);
            } else if (c.isRecord() && c.getPackageName().startsWith("com.projectv") && out.add(c)) {
                for (var rc : c.getRecordComponents()) {
                    collect(rc.getGenericType(), out);
                }
            }
        }
    }

    @Test
    void everyWorkerResultRecordToleratesAFieldItDoesNotKnowYet() {
        Set<Class<?>> records = new LinkedHashSet<>();
        for (Method m : MediaActivities.class.getMethods()) {
            collect(m.getGenericReturnType(), records);
        }
        assertTrue(records.size() > 10, "found the worker result records: " + records.size());
        DataConverter dc = LenientConverters.dataConverter();
        for (Class<?> r : records) {
            Payload p = Payload.newBuilder().putMetadata("encoding", com.google.protobuf.ByteString.copyFromUtf8("json/plain"))
                    .setData(com.google.protobuf.ByteString.copyFromUtf8("{\"zzFieldAddedByTheWorkerLater\":1}")).build();
            try {
                dc.fromPayload(p, r, r);
            } catch (RuntimeException e) {
                throw new AssertionError(r.getSimpleName() + " wedges on an unknown worker field: " + e.getMessage(), e);
            }
        }
    }

    @Test
    void theDefaultTemporalConverterWouldHaveWedged_soTheLenientOneIsWired() {
        // Documents why LenientConverters exists: the stock default rejects the unknown field.
        Payload p = Payload.newBuilder().putMetadata("encoding", com.google.protobuf.ByteString.copyFromUtf8("json/plain"))
                .setData(com.google.protobuf.ByteString.copyFromUtf8("{\"found\":true,\"zzNew\":1}")).build();
        Class<?> stock = com.projectv.media.MediaTypes.StockResult.class;
        // StockResult now carries @JsonIgnoreProperties; use a record without it to show the default's behaviour.
        record Strict(boolean found) {
        }
        boolean defaultRejects;
        try {
            io.temporal.common.converter.DefaultDataConverter.newDefaultInstance().fromPayload(p, Strict.class, Strict.class);
            defaultRejects = false;
        } catch (RuntimeException e) {
            defaultRejects = true;
        }
        assertTrue(defaultRejects, "the stock Temporal converter rejects unknown fields");
        assertFalse(Optional.of(LenientConverters.dataConverter().fromPayload(p, Strict.class, Strict.class)).isEmpty());
        assertTrue(stock.isRecord());
    }
}
