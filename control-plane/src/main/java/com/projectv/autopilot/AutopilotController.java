package com.projectv.autopilot;

import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/** REST API of the Autopilot screen. */
@RestController
@RequestMapping("/api/autopilot")
public class AutopilotController {

    public record IdeaBody(String idea, String look) {
    }

    public record PostBody(String title, String description, String hashtags, String privacy) {
    }

    private final AutopilotService autopilot;

    public AutopilotController(AutopilotService autopilot) {
        this.autopilot = autopilot;
    }

    @GetMapping
    public Map<String, Object> status() {
        return autopilot.status();
    }

    @GetMapping("/settings")
    public AutopilotService.Settings settings() {
        return autopilot.settings();
    }

    @PutMapping("/settings")
    public AutopilotService.Settings saveSettings(@RequestBody AutopilotService.Settings body) {
        return autopilot.saveSettings(body);
    }

    @GetMapping("/ideas")
    public List<Map<String, Object>> ideas() {
        return autopilot.ideas();
    }

    @PostMapping("/ideas")
    public Map<String, Object> addIdea(@RequestBody IdeaBody body) {
        return Map.of("id", autopilot.addIdea(body.idea(), body.look()));
    }

    @DeleteMapping("/ideas/{id}")
    public void deleteIdea(@PathVariable UUID id) {
        autopilot.deleteIdea(id);
    }

    @GetMapping("/runs")
    public List<Map<String, Object>> runs() {
        return autopilot.runs();
    }

    /** Manual start mode: the owner's Start for today's run. */
    @PostMapping("/runs/{id}/start")
    public void start(@PathVariable UUID id) {
        autopilot.startNow(id);
    }

    /** The owner's tap: uploads this Short to YouTube with the (edited) title, description and hashtags. */
    @PostMapping("/runs/{id}/post")
    public void post(@PathVariable UUID id, @RequestBody PostBody body) {
        autopilot.post(id, body.title(), body.description(), body.hashtags(), body.privacy());
    }

    @PostMapping("/runs/{id}/dismiss")
    public void dismiss(@PathVariable UUID id) {
        autopilot.dismiss(id);
    }

    @PostMapping("/runs/{id}/retry-kit")
    public void retryKit(@PathVariable UUID id) {
        autopilot.retryKit(id);
    }

    /** Runs the daily check now (it also runs every minute by itself). */
    @PostMapping("/tick")
    public void tick() {
        autopilot.tick();
    }
}
