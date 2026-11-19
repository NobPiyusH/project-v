package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamEstimate;
import com.projectv.dream.DreamTypes.DreamFinishRequest;
import com.projectv.dream.DreamTypes.DreamFinishResult;
import com.projectv.media.MediaTypes.ClipRequest;
import com.projectv.media.MediaTypes.ClipResult;
import com.projectv.media.MediaTypes.KeyframeRequest;
import com.projectv.media.MediaTypes.KeyframeResult;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.UUID;

/** Temporal activity implementation: a thin adapter (ids as text) over {@link DreamService}. */
@Component
public class DreamActivitiesImpl implements DreamActivities {

    private final DreamService dreams;

    public DreamActivitiesImpl(DreamService dreams) {
        this.dreams = dreams;
    }

    @Override
    public String writeShots(String dreamId, String notes) {
        return dreams.writeShots(UUID.fromString(dreamId), notes);
    }

    @Override
    public KeyframeRequest pictureRequest(String dreamId, List<String> shotIds, String notes) {
        return dreams.pictureRequest(UUID.fromString(dreamId), shotIds, notes);
    }

    @Override
    public String savePictures(String dreamId, KeyframeRequest request, KeyframeResult result) {
        return dreams.savePictures(UUID.fromString(dreamId), request, result);
    }

    @Override
    public DreamEstimate clipEstimate(String dreamId, List<String> shotIds) {
        return dreams.clipEstimate(UUID.fromString(dreamId), shotIds);
    }

    @Override
    public ClipRequest clipRequest(String dreamId, List<String> shotIds) {
        return dreams.clipRequest(UUID.fromString(dreamId), shotIds);
    }

    @Override
    public String saveClips(String dreamId, ClipRequest request, ClipResult result) {
        return dreams.saveClips(UUID.fromString(dreamId), request, result);
    }

    @Override
    public com.projectv.media.MediaTypes.MusicRequest musicRequest(String dreamId, String notes) {
        return dreams.musicRequest(UUID.fromString(dreamId), notes);
    }

    @Override
    public String saveMusic(String dreamId, com.projectv.media.MediaTypes.MusicResult result) {
        return dreams.saveMusic(UUID.fromString(dreamId), result);
    }

    @Override
    public boolean scoutWanted(String dreamId) {
        return dreams.scoutWanted(UUID.fromString(dreamId));
    }

    @Override
    public DreamTypes.DreamScoutRequest scoutRequest(String dreamId, String notes) {
        return dreams.scoutRequest(UUID.fromString(dreamId), notes);
    }

    @Override
    public String saveScout(String dreamId, DreamTypes.DreamScoutRequest request, DreamTypes.DreamScoutResult result) {
        return dreams.saveScout(UUID.fromString(dreamId), request, result);
    }

    @Override
    public String musicSource(String dreamId) {
        return dreams.musicSource(UUID.fromString(dreamId));
    }

    @Override
    public DreamTypes.DreamBedRequest musicBedRequest(String dreamId) {
        return dreams.musicBedRequest(UUID.fromString(dreamId));
    }

    @Override
    public String saveMusicBed(String dreamId, DreamTypes.DreamBedResult result) {
        return dreams.saveMusicBed(UUID.fromString(dreamId), result);
    }

    @Override
    public DreamFinishRequest finishRequest(String dreamId) {
        return dreams.finishRequest(UUID.fromString(dreamId));
    }

    @Override
    public String saveFinish(String dreamId, DreamFinishResult result) {
        return dreams.saveFinish(UUID.fromString(dreamId), result);
    }

    @Override
    public void status(String dreamId, String step, String status, String message) {
        dreams.status(UUID.fromString(dreamId), step, status, message);
    }

    @Override
    public void startClipBatch(String dreamId) {
        dreams.startClipBatch(UUID.fromString(dreamId));
    }
}
