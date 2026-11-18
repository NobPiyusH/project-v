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
        return Map.of("decisions", producer.decisions(id), "agreement", producer.agreement(), "summary", producer.videoSummary(id));
    }

    public record TrustBody(String trust) {
    }

    public record RuleBody(String stage, String text, Boolean enabled) {
    }

    @GetMapping("/producer/settings")
    public Map<String, Object> settings() {
        return Map.of("trust", producer.trust(), "deciding", producer.deciding(), "agreement", producer.agreement());
    }

    @org.springframework.web.bind.annotation.PutMapping("/producer/settings")
    public void setTrust(@org.springframework.web.bind.annotation.RequestBody TrustBody b) {
        producer.setTrust(b.trust());
    }

    @GetMapping("/producer/rules")
    public java.util.List<Map<String, Object>> rules() {
        return producer.rulesList();
    }

    @org.springframework.web.bind.annotation.PostMapping("/producer/rules")
    public void addRule(@org.springframework.web.bind.annotation.RequestBody RuleBody b) {
        producer.addRule(b.stage(), b.text());
    }

    @org.springframework.web.bind.annotation.PutMapping("/producer/rules/{id}")
    public void updateRule(@PathVariable UUID id, @org.springframework.web.bind.annotation.RequestBody RuleBody b) {
        producer.updateRule(id, b.text(), b.enabled());
    }

    @org.springframework.web.bind.annotation.DeleteMapping("/producer/rules/{id}")
    public void deleteRule(@PathVariable UUID id) {
        producer.deleteRule(id);
    }
}
