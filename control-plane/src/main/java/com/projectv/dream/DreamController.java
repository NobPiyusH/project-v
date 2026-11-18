package com.projectv.dream;

import com.projectv.dream.DreamStudio.NewDream;
import com.projectv.dream.DreamTypes.DreamStep;
import com.projectv.workflow.StageMode;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/** REST API of the Dream Shorts screen. */
@RestController
@RequestMapping("/api/dream")
public class DreamController {

    public record RedoBody(List<String> shotIds, String notes) {
    }

    public record ModeBody(String mode) {
    }

    private final DreamStudio studio;

    public DreamController(DreamStudio studio) {
        this.studio = studio;
    }

    @GetMapping("/looks")
    public List<String> looks() {
        return DreamService.LOOK_ORDER;
    }

    /** Measured GPU timings that replaced the config's guesses (learning loop, phase 1). */
    @GetMapping("/calibration")
    public List<Map<String, Object>> calibration() {
        return studio.calibration();
    }

    @PostMapping
    public Map<String, Object> create(@RequestBody NewDream body) {
        return Map.of("id", studio.create(body));
    }

    @GetMapping
    public List<Map<String, Object>> list() {
        return studio.list();
    }

    @GetMapping("/{id}")
    public Map<String, Object> detail(@PathVariable UUID id) {
        return studio.detail(id);
    }

    @PostMapping("/{id}/steps/{step}/approve")
    public void approve(@PathVariable UUID id, @PathVariable String step) {
        studio.approve(id, DreamStep.valueOf(step.toUpperCase()));
    }

    @PostMapping("/{id}/steps/{step}/redo")
    public void redo(@PathVariable UUID id, @PathVariable String step, @RequestBody(required = false) RedoBody body) {
        studio.redo(id, DreamStep.valueOf(step.toUpperCase()), body == null ? null : body.shotIds(), body == null ? null : body.notes());
    }

    @PostMapping("/{id}/steps/{step}/mode")
    public void mode(@PathVariable UUID id, @PathVariable String step, @RequestBody ModeBody body) {
        studio.setMode(id, DreamStep.valueOf(step.toUpperCase()), StageMode.parse(body.mode()));
    }

    /** Redo a finished Short from a step (e.g. MUSIC or FINISH); earlier steps keep their results. */
    @PostMapping("/{id}/rerun/{step}")
    public void rerun(@PathVariable UUID id, @PathVariable String step) {
        studio.rerun(id, DreamStep.valueOf(step.toUpperCase()));
    }

    /** Which of the Scout's angles the writer should follow (1-based). */
    @PostMapping("/{id}/scout/angle/{n}")
    public void angle(@PathVariable UUID id, @PathVariable int n) {
        studio.pickAngle(id, n);
    }

    @PostMapping("/{id}/retry")
    public void retry(@PathVariable UUID id) {
        studio.retry(id);
    }

    @PostMapping("/{id}/stop")
    public void stop(@PathVariable UUID id) {
        studio.stopClips(id);
    }

    @PostMapping("/{id}/cancel")
    public void cancel(@PathVariable UUID id) {
        studio.cancel(id);
    }
}
