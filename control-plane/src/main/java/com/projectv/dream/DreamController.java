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

    public record ReviewBody(String shotId, int rating, String note) {
    }

    public record LessonBody(String rule, String target, String status) {
    }

    private final DreamStudio studio;
    private final DreamLessons lessons;

    public DreamController(DreamStudio studio, DreamLessons lessons) {
        this.studio = studio;
        this.lessons = lessons;
    }

    /** The owner rates a clip (shotId set) or the whole Short; a note becomes a proposed lesson. */
    @PostMapping("/{id}/reviews")
    public Map<String, Object> review(@PathVariable UUID id, @RequestBody ReviewBody body) {
        UUID shot = body.shotId() == null || body.shotId().isBlank() ? null : UUID.fromString(body.shotId());
        return Map.of("id", lessons.review(id, shot, body.rating(), body.note()));
    }

    @GetMapping("/{id}/reviews")
    public List<Map<String, Object>> reviews(@PathVariable UUID id) {
        return lessons.reviews(id);
    }

    @GetMapping("/lessons")
    public List<Map<String, Object>> lessons() {
        return lessons.lessons();
    }

    /** Edit, approve (ACTIVE) or retire a lesson. */
    @PostMapping("/lessons/{lessonId}")
    public void lesson(@PathVariable UUID lessonId, @RequestBody LessonBody body) {
        lessons.update(lessonId, body.rule(), body.target(), body.status());
    }

    @GetMapping("/looks")
    public List<String> looks() {
        return DreamService.SELECTABLE_LOOKS;
    }

    /** The idea packs (curated concept seeds with a hook title and a camera note) for the new-Short idea picker. */
    @GetMapping("/idea-packs")
    public List<DreamIdeaPacks.Pack> ideaPacks() {
        return DreamIdeaPacks.packs();
    }

    public record HookBody(String text, String color) {
    }

    /** Edit the hook title (review or manual step); it applies at the next finish (Redo from Finish, free). */
    @PostMapping("/{id}/hook")
    public void hook(@PathVariable UUID id, @RequestBody HookBody body) {
        studio.setHook(id, body.text(), body.color());
    }

    public record PathsBody(String a, String b) {
    }

    /** Edit the two path options of the end card (review or manual step); they apply at the next finish (Redo from Finish, free). */
    @PostMapping("/{id}/paths")
    public void paths(@PathVariable UUID id, @RequestBody PathsBody body) {
        studio.setPaths(id, body.a(), body.b());
    }

    public record ContinueBody(String path) {
    }

    /** "Continue this dream": the owner picks path A or B; a new Short (the next episode) starts down that path. */
    @PostMapping("/{id}/continue")
    public Map<String, Object> continueDream(@PathVariable UUID id, @RequestBody ContinueBody body) {
        return Map.of("id", studio.continueDream(id, DreamBranches.PathPick.owner(body.path())));
    }

    /** The episode chain this Short belongs to (episode 1, 2, 3...). */
    @GetMapping("/{id}/chain")
    public List<Map<String, Object>> chain(@PathVariable UUID id) {
        return studio.chain(id);
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

    public record EditingBody(Boolean musicEdit, Boolean ambience, Boolean pacing) {
    }

    /** Switch the editing rules for this Short; they apply at the next finish (use "Redo from Finish", free). */
    @PostMapping("/{id}/editing")
    public void editing(@PathVariable UUID id, @RequestBody EditingBody body) {
        studio.editing(id, body.musicEdit(), body.ambience(), body.pacing());
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
