package com.projectv.dream;

import com.projectv.media.MediaTypes.ProducerAdvice;
import com.projectv.media.MediaTypes.ProducerReviewRequest;
import com.projectv.media.MediaTypes.ProducerReviewResult;
import com.projectv.media.MediaTypes.SpendDecision;
import io.temporal.activity.ActivityInterface;

import java.math.BigDecimal;
import java.util.List;

/**
 * AI Producer for Dream Shorts (CONTROL queue). The review itself runs on the media worker
 * ({@code producer_review}, shared with the documentary pipeline); these activities choose what it judges, save its
 * verdict, and answer whether it may act alone within the owner's limits.
 */
@ActivityInterface(namePrefix = "dreamProducer_")
public interface DreamProducerActivities {

    /** null when the step has nothing the Producer judges (Scout). */
    ProducerReviewRequest reviewRequest(String dreamId, String step);

    ProducerAdvice saveReview(String dreamId, String step, int attempt, ProducerReviewResult result);

    /** May the Producer act alone on this Short: an autopilot Short, or the global trust switch says so. */
    boolean decides(String dreamId);

    /** A redo round it may start: rounds left, and the Short inside the ₹ per-Short (and autopilot daily) limits. */
    boolean mayRedo(String dreamId, int round);

    /** Shot ids for weak items ("shot-N"). */
    List<String> shotIds(String dreamId, List<String> itemIds);

    /** Its own call on a money step (clips), recorded; approves only within the limits. */
    SpendDecision spendDecision(String dreamId, BigDecimal estimateInr);

    void markActed(String decisionId, String acted);
}
