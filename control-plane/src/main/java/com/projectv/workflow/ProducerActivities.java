package com.projectv.workflow;

import com.projectv.media.MediaTypes.ProducerAdvice;
import com.projectv.media.MediaTypes.ProducerReviewRequest;
import com.projectv.media.MediaTypes.ProducerReviewResult;
import io.temporal.activity.ActivityInterface;

/** AI Producer (P1) control activities: what to review, and saving the review as advice (control task queue). */
@ActivityInterface(namePrefix = "Producer_")
public interface ProducerActivities {

    /** null when the stage has nothing the Producer reviews. */
    ProducerReviewRequest reviewRequest(String videoId, String stage);

    ProducerAdvice saveReview(String videoId, String stage, int attempt, ProducerReviewResult result);

    /** Advice on a money step (no AI call): would the Producer approve it within the owner's limits? */
    String spendAdvice(String videoId, java.math.BigDecimal estimateInr);

    // ---- P2: acting
    boolean deciding();

    void markActed(String decisionId, String acted);

    boolean mayRedo(String videoId, int round);

    java.util.List<String> shotIdsFor(String videoId, java.util.List<String> itemIds);

    com.projectv.media.MediaTypes.NarrationRequest narrationRedoRequest(String videoId, String notes);

    com.projectv.media.MediaTypes.SpendDecision spendDecision(String videoId, String stage, java.math.BigDecimal estimateInr);
}
