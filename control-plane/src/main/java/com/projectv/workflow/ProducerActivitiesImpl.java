package com.projectv.workflow;

import com.projectv.media.MediaTypes.ProducerAdvice;
import com.projectv.media.MediaTypes.ProducerReviewRequest;
import com.projectv.media.MediaTypes.ProducerReviewResult;
import com.projectv.production.ProducerService;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.UUID;

@Component
public class ProducerActivitiesImpl implements ProducerActivities {

    private final ProducerService producer;
    private final com.projectv.production.ProductionService production;

    public ProducerActivitiesImpl(ProducerService producer, com.projectv.production.ProductionService production) {
        this.producer = producer;
        this.production = production;
    }

    @Override
    public ProducerReviewRequest reviewRequest(String videoId, String stage) {
        return producer.reviewRequest(UUID.fromString(videoId), stage);
    }

    @Override
    public ProducerAdvice saveReview(String videoId, String stage, int attempt, ProducerReviewResult result) {
        return producer.saveReview(UUID.fromString(videoId), stage, attempt, result);
    }

    @Override
    public String spendAdvice(String videoId, BigDecimal estimateInr) {
        return producer.spendAdvice(UUID.fromString(videoId), estimateInr);
    }

    @Override
    public boolean deciding() {
        return producer.deciding();
    }

    @Override
    public void markActed(String decisionId, String acted) {
        if (decisionId != null) {
            producer.markActed(UUID.fromString(decisionId), acted);
        }
    }

    @Override
    public boolean mayRedo(String videoId, int round) {
        return producer.mayRedo(UUID.fromString(videoId), round);
    }

    @Override
    public java.util.List<String> shotIdsFor(String videoId, java.util.List<String> itemIds) {
        return producer.shotIdsFor(UUID.fromString(videoId), itemIds);
    }

    @Override
    public com.projectv.media.MediaTypes.NarrationRequest narrationRedoRequest(String videoId, String notes) {
        return ProducerService.withStyle(production.narrationRequest(UUID.fromString(videoId)), notes);
    }

    @Override
    public com.projectv.media.MediaTypes.SpendDecision spendDecision(String videoId, String stage, BigDecimal estimateInr) {
        return producer.spendDecision(UUID.fromString(videoId), stage, estimateInr);
    }
}
