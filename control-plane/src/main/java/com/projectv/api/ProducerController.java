package com.projectv.api;

import com.projectv.production.ProducerService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;
import java.util.UUID;

/** AI Producer report: its reviews of a video (with the owner's decisions) and its overall agreement with the owner. */
@RestController
@RequestMapping("/api")
public class ProducerController {

    private final ProducerService producer;

    public ProducerController(ProducerService producer) {
        this.producer = producer;
    }

    @GetMapping("/videos/{id}/producer")
    public Map<String, Object> report(@PathVariable UUID id) {
        return Map.of("decisions", producer.decisions(id), "agreement", producer.agreement());
    }
}
