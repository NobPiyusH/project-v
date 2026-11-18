package com.projectv.workflow;

import com.projectv.media.MediaTypes.ProducerReviewRequest;
import com.projectv.media.MediaTypes.ProducerReviewResult;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/** AI Producer review on the Python media worker, where the files are: Gemini reads, looks, watches and listens. */
@ActivityInterface
public interface ProducerMediaActivities {

    @ActivityMethod(name = "producer_review")
    ProducerReviewResult review(ProducerReviewRequest request);
}
