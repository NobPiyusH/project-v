package com.projectv.api;

import com.projectv.ledger.CostLedger;
import com.projectv.planner.Planner;
import com.projectv.planner.StylePresets;
import com.projectv.provider.ProviderRegistry;
import com.projectv.video.VideoService;
import com.projectv.workflow.Stage;
import com.projectv.workflow.StageMode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.UUID;

/** REST API used by the dashboard. Local only (bound to localhost via the dev setup); no auth in M1. */
@RestController
@RequestMapping("/api")
public class ApiController {

    private final VideoService videos;
    private final ProviderRegistry providers;
    private final StylePresets presets;
    private final CostLedger ledger;
    private final Planner planner;

    public ApiController(VideoService videos, ProviderRegistry providers, StylePresets presets, CostLedger ledger,
                         Planner planner) {
        this.videos = videos;
        this.providers = providers;
        this.presets = presets;
        this.ledger = ledger;
        this.planner = planner;
    }

    public record CreateVideoRequest(@NotBlank @Size(max = 4000) String prompt, StageMode defaultMode,
                                     Map<Stage, StageMode> modes) {
    }

    public record NotesRequest(String notes) {
    }

    public record ModeRequest(StageMode mode) {
    }

    @PostMapping("/videos")
    public ResponseEntity<Map<String, Object>> create(@Valid @RequestBody CreateVideoRequest req) {
        UUID id = videos.create(new VideoService.CreateVideo(req.prompt(), req.defaultMode(), req.modes()));
        return ResponseEntity.status(HttpStatus.CREATED).body(Map.of("id", id));
    }

    @GetMapping("/videos")
    public List<Map<String, Object>> list() {
        return videos.list();
    }

    @GetMapping("/videos/{id}")
    public Map<String, Object> detail(@PathVariable UUID id) {
        Map<String, Object> d = new LinkedHashMap<>(videos.detail(id));
        d.put("costs", ledger.forVideo(id));
        return d;
    }

    @PostMapping("/videos/{id}/stages/{stage}/approve")
    public void approve(@PathVariable UUID id, @PathVariable Stage stage, @RequestBody(required = false) NotesRequest body) {
        videos.approve(id, stage, body == null ? null : body.notes());
    }

    @PostMapping("/videos/{id}/stages/{stage}/reject")
    public void reject(@PathVariable UUID id, @PathVariable Stage stage, @RequestBody(required = false) NotesRequest body) {
        videos.reject(id, stage, body == null ? null : body.notes());
    }

    @PutMapping("/videos/{id}/modes/{stage}")
    public void setMode(@PathVariable UUID id, @PathVariable Stage stage, @RequestBody ModeRequest body) {
        videos.setMode(id, stage, body.mode());
    }

    @PutMapping("/videos/{id}/modes")
    public void setAllModes(@PathVariable UUID id, @RequestBody ModeRequest body) {
        videos.setAllModes(id, body.mode());
    }

    @PostMapping("/videos/{id}/pause")
    public void pause(@PathVariable UUID id) {
        videos.pause(id);
    }

    @PostMapping("/videos/{id}/resume")
    public void resume(@PathVariable UUID id) {
        videos.resume(id);
    }

    @GetMapping("/stages")
    public List<Map<String, String>> stages() {
        return Arrays.stream(Stage.values())
                .map(s -> Map.of("id", s.name(), "label", s.label(), "description", s.description()))
                .toList();
    }

    @GetMapping("/styles")
    public List<StylePresets.Preset> styles() {
        return presets.all();
    }

    @GetMapping("/providers")
    public List<Map<String, Object>> providers() {
        return providers.all();
    }

    @GetMapping("/ledger")
    public List<Map<String, Object>> ledger() {
        return ledger.totalsByProvider();
    }

    @GetMapping("/system")
    public Map<String, Object> system() {
        return Map.of("planner", planner.llmConfigured() ? "gemini:" + planner.modelName() : "rules (no GEMINI_API_KEY)");
    }

    @ExceptionHandler(NoSuchElementException.class)
    public ResponseEntity<Map<String, String>> notFound(NoSuchElementException e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", e.getMessage()));
    }
}
