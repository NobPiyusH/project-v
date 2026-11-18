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

    public ProducerActivitiesImpl(ProducerService producer) {
        this.producer = producer;
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
}
