package com.projectv.config;

import com.projectv.workflow.TaskQueues;
import com.projectv.workflow.VideoActivitiesImpl;
import com.projectv.workflow.VideoProductionWorkflowImpl;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowClientOptions;
import io.temporal.serviceclient.WorkflowServiceStubs;
import io.temporal.serviceclient.WorkflowServiceStubsOptions;
import io.temporal.worker.Worker;
import io.temporal.worker.WorkerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.SmartLifecycle;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Temporal client + in-process Java worker, wired by hand (the official Spring Boot starter targets older Boot
 * lines; see docs/adr/0001). The worker starts after the Spring context is ready and stops on shutdown.
 */
@Configuration
public class TemporalConfig {

    @Bean(destroyMethod = "shutdown")
    public WorkflowServiceStubs workflowServiceStubs(@Value("${projectv.temporal.target}") String target) {
        return WorkflowServiceStubs.newServiceStubs(WorkflowServiceStubsOptions.newBuilder().setTarget(target).build());
    }

    @Bean
    public WorkflowClient workflowClient(WorkflowServiceStubs stubs,
                                         @Value("${projectv.temporal.namespace}") String namespace) {
        return WorkflowClient.newInstance(stubs, WorkflowClientOptions.newBuilder().setNamespace(namespace).build());
    }

    @Bean
    public WorkerFactory workerFactory(WorkflowClient client, VideoActivitiesImpl activities) {
        WorkerFactory factory = WorkerFactory.newInstance(client);
        Worker worker = factory.newWorker(TaskQueues.CONTROL);
        worker.registerWorkflowImplementationTypes(VideoProductionWorkflowImpl.class, com.projectv.workflow.ShotRedoWorkflowImpl.class,
                com.projectv.workflow.VoiceRedoWorkflowImpl.class, com.projectv.workflow.CreativeReviewWorkflowImpl.class,
                com.projectv.workflow.ClipRedoWorkflowImpl.class);
        worker.registerActivitiesImplementations(activities);
        return factory;
    }

    @Bean
    public SmartLifecycle temporalWorkerLifecycle(WorkerFactory factory) {
        return new SmartLifecycle() {
            private volatile boolean running;

            @Override
            public void start() {
                factory.start();
                running = true;
            }

            @Override
            public void stop() {
                factory.shutdown();
                factory.awaitTermination(10, java.util.concurrent.TimeUnit.SECONDS);
                running = false;
            }

            @Override
            public boolean isRunning() {
                return running;
            }
        };
    }
}
