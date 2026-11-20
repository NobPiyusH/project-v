package com.projectv.gpu.broker;

import java.util.List;

/** A provider that is planned but not built: never configured, so the broker skips it with a clear reason. */
public class StubProvider implements GpuProvider {

    private final String id;
    private final String todo;

    public StubProvider(String id, String todo) {
        this.id = id;
        this.todo = todo;
    }

    @Override
    public String provider() {
        return id;
    }

    @Override
    public boolean configured() {
        return false;
    }

    @Override
    public boolean enabledByDefault() {
        return false;
    }

    @Override
    public List<Pod> listPods() {
        return List.of();
    }

    @Override
    public void terminate(String podId) {
        throw new UnsupportedOperationException(id + " adapter not built yet: " + todo);
    }

    @Override
    public Quote quote(GpuSpec spec) {
        return Quote.unavailable(id, "adapter not built yet (TODO: " + todo + ")");
    }

    @Override
    public Instance create(CreateRequest req) {
        throw new UnsupportedOperationException(id + " adapter not built yet: " + todo);
    }

    @Override
    public InstanceStatus status(String instanceId) {
        throw new UnsupportedOperationException(id + " adapter not built yet: " + todo);
    }
}
