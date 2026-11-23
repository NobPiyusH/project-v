"""Task queues. The Python control plane uses its own control queue and workflow type names so it can run next to the Java worker
(rollback rule: new workflow types, never edits to running ones). The media queue is shared: the Python media worker serves both."""

CONTROL = "projectv-control-py"  # Python control plane: workflows and control activities (Java keeps "projectv-control")
MEDIA = "projectv-media"  # Python media worker (workers/): FFmpeg, TTS, captions, critic, generation clients

VIDEO_WORKFLOW = "VideoProductionWorkflowPy"
SHOT_REDO_WORKFLOW = "ShotRedoWorkflowPy"
CLIP_REDO_WORKFLOW = "ClipRedoWorkflowPy"
VOICE_REDO_WORKFLOW = "VoiceRedoWorkflowPy"
FOOTAGE_WORKFLOW = "FootageWorkflowPy"
CREATIVE_REVIEW_WORKFLOW = "CreativeReviewWorkflowPy"
