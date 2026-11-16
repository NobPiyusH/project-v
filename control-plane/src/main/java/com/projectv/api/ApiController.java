package com.projectv.api;

import com.projectv.assets.AssetStore;
import com.projectv.assets.MediaUrls;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.production.ProductionService;
import java.net.URI;
import java.time.Duration;
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
    private final ProductionService production;
    private final AssetStore assets;
    private final MediaUrls mediaUrls;
    private final BudgetGuard budget;
    private final Pricing pricing;

    public ApiController(VideoService videos, ProviderRegistry providers, StylePresets presets, CostLedger ledger,
                         Planner planner, ProductionService production, AssetStore assets, MediaUrls mediaUrls,
                         BudgetGuard budget, Pricing pricing) {
        this.videos = videos;
        this.providers = providers;
        this.presets = presets;
        this.ledger = ledger;
        this.planner = planner;
        this.production = production;
        this.assets = assets;
        this.mediaUrls = mediaUrls;
        this.budget = budget;
        this.pricing = pricing;
    }

    public record CreateVideoRequest(@NotBlank @Size(max = 4000) String prompt, StageMode defaultMode,
                                     Map<Stage, StageMode> modes, Integer reviewMinutes) {
    }

    public record ReviewRequest(int minutes) {
    }

    public record ShotRedoBody(List<UUID> shotIds, String notes, String policy) {
    }

    public record PromptBody(@NotBlank @Size(max = 4000) String keyframePrompt) {
    }

    public record SelectBody(UUID assetId) {
    }

    public record LineEditBody(@NotBlank @Size(max = 2000) String text, Boolean spoken) {
    }

    public record LineRedoBody(List<Integer> lines, String style) {
    }

    public record NotesRequest(String notes) {
    }

    public record ModeRequest(StageMode mode) {
    }

    @PostMapping("/videos")
    public ResponseEntity<Map<String, Object>> create(@Valid @RequestBody CreateVideoRequest req) {
        UUID id = videos.create(new VideoService.CreateVideo(req.prompt(), req.defaultMode(), req.modes(), req.reviewMinutes()));
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

    @PostMapping("/videos/{id}/stages/{stage}/rerun")
    public Map<String, Object> rerun(@PathVariable UUID id, @PathVariable Stage stage) {
        return Map.of("stages", videos.rerun(id, stage));
    }

    @ExceptionHandler(IllegalStateException.class)
    public ResponseEntity<Map<String, String>> conflict(IllegalStateException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of("error", e.getMessage()));
    }

    @PutMapping("/videos/{id}/modes/{stage}")
    public void setMode(@PathVariable UUID id, @PathVariable Stage stage, @RequestBody ModeRequest body) {
        videos.setMode(id, stage, body.mode());
    }

    @PutMapping("/videos/{id}/modes")
    public void setAllModes(@PathVariable UUID id, @RequestBody ModeRequest body) {
        videos.setAllModes(id, body.mode());
    }

    @PostMapping("/videos/{id}/shots/redo")
    public Map<String, Object> redoShots(@PathVariable UUID id, @RequestBody ShotRedoBody body) {
        return Map.of("workflowId", videos.redoShots(id, body.shotIds(), body.notes(), body.policy()));
    }

    @PutMapping("/shots/{shotId}/prompt")
    public void editShotPrompt(@PathVariable UUID shotId, @Valid @RequestBody PromptBody body) {
        production.editShotPrompt(shotId, body.keyframePrompt());
    }

    @GetMapping("/shots/{shotId}/versions")
    public List<Map<String, Object>> shotVersions(@PathVariable UUID shotId) {
        return production.shotVersions(shotId);
    }

    @PutMapping("/shots/{shotId}/keyframe")
    public void selectKeyframe(@PathVariable UUID shotId, @RequestBody SelectBody body) {
        production.selectKeyframe(shotId, body.assetId());
    }

    @PutMapping("/videos/{id}/script/lines/{index}")
    public Object editLine(@PathVariable UUID id, @PathVariable int index, @Valid @RequestBody LineEditBody body) {
        return production.editLine(id, index, body.text(), body.spoken());
    }

    @PostMapping("/videos/{id}/script/lines/redo")
    public Map<String, Object> redoLines(@PathVariable UUID id, @RequestBody LineRedoBody body) {
        return Map.of("workflowId", videos.redoLines(id, body.lines(), body.style()));
    }

    @PutMapping("/videos/{id}/script/lines/{index}/voice")
    public void selectLineVoice(@PathVariable UUID id, @PathVariable int index, @RequestBody SelectBody body) {
        production.selectLineVoice(id, index, body.assetId());
    }

    @PutMapping("/videos/{id}/review-minutes")
    public void setReviewMinutes(@PathVariable UUID id, @RequestBody ReviewRequest body) {
        videos.setReviewMinutes(id, body.minutes());
    }

    @PostMapping("/videos/{id}/pause")
    public void pause(@PathVariable UUID id) {
        videos.pause(id);
    }

    @PostMapping("/videos/{id}/resume")
    public void resume(@PathVariable UUID id) {
        videos.resume(id);
    }

    @GetMapping("/videos/{id}/production")
    public Map<String, Object> production(@PathVariable UUID id) {
        return production.production(id);
    }

    /** Redirects to a short-lived URL on the local S3 store (supports range requests for video seeking). */
    @GetMapping("/assets/{id}/content")
    public ResponseEntity<Void> assetContent(@PathVariable UUID id) {
        String url = mediaUrls.presign(assets.uriOf(id), Duration.ofMinutes(30));
        return ResponseEntity.status(HttpStatus.FOUND).location(URI.create(url)).build();
    }

    @GetMapping("/assets/{id}")
    public Map<String, Object> asset(@PathVariable UUID id) {
        return assets.describe(id);
    }

    @GetMapping("/budget")
    public Map<String, Object> budget() {
        return Map.of("geminiCapInr", pricing.geminiMonthlyCapInr(), "geminiSpentInr", budget.geminiSpentThisMonth(),
                "geminiRemainingInr", budget.geminiRemaining());
    }

    @GetMapping("/stages")
    public List<Map<String, Object>> stages() {
        return Arrays.stream(Stage.values())
                .map(s -> Map.<String, Object>of("id", s.name(), "label", s.label(), "description", s.description(),
                        "redoWith", VideoService.REDO_WITH.getOrDefault(s, List.of())))
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
