"""Control activities of a Dream Short (Python control worker): thin adapters over DreamService and DreamProducer. Port of
DreamActivitiesImpl and DreamProducer's activity methods. JSON in, JSON out: requests built here travel unchanged to the media worker
and its results come straight back into the save activity (camelCase dicts, see studio.media.wire).

Activities are named `dream.<method>` and `dreamProducer.<method>` so they never clash with the video pipeline's `control.*` names."""

from __future__ import annotations

import functools
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from studio.dream.producer import DreamProducer
from studio.dream.service import DreamService
from studio.dream.wire_types import DreamBedResult, DreamCheckResult, DreamFinishResult, DreamScoutRequest, DreamScoutResult
from studio.media.types import ClipRequest, ClipResult, KeyframeRequest, KeyframeResult, MusicResult, ProducerReviewResult
from studio.media.wire import from_json, to_json


def _id(s: str) -> uuid.UUID:
    return uuid.UUID(s)


class DreamActivities:
    def __init__(self, dreams: DreamService):
        self.dreams = dreams

    def write_shots(self, dream_id: str, notes: str | None) -> str:
        return self.dreams.write_shots(_id(dream_id), notes)

    def picture_request(self, dream_id: str, shot_ids: list[str] | None, notes: str | None) -> dict[str, Any]:
        return to_json(self.dreams.picture_request(_id(dream_id), shot_ids, notes))

    def save_pictures(self, dream_id: str, request: dict[str, Any], result: dict[str, Any]) -> str:
        return self.dreams.save_pictures(_id(dream_id), from_json(KeyframeRequest, request), from_json(KeyframeResult, result))

    def clip_estimate(self, dream_id: str, shot_ids: list[str] | None) -> dict[str, Any]:
        return to_json(self.dreams.clip_estimate(_id(dream_id), shot_ids))

    def clip_request(self, dream_id: str, shot_ids: list[str] | None) -> dict[str, Any]:
        return to_json(self.dreams.clip_request(_id(dream_id), shot_ids))

    def save_clips(self, dream_id: str, request: dict[str, Any], result: dict[str, Any]) -> str:
        return self.dreams.save_clips(_id(dream_id), from_json(ClipRequest, request), from_json(ClipResult, result))

    def music_request(self, dream_id: str, notes: str | None) -> dict[str, Any]:
        return to_json(self.dreams.music_request(_id(dream_id), notes))

    def save_music(self, dream_id: str, result: dict[str, Any]) -> str:
        return self.dreams.save_music(_id(dream_id), from_json(MusicResult, result))

    def check_request(self, dream_id: str) -> dict[str, Any] | None:
        r = self.dreams.check_request(_id(dream_id))
        return None if r is None else to_json(r)

    def save_check(self, dream_id: str, result: dict[str, Any]) -> str:
        return self.dreams.save_check(_id(dream_id), from_json(DreamCheckResult, result), "FINISH")

    def picture_check_request(self, dream_id: str) -> dict[str, Any] | None:
        r = self.dreams.picture_check_request(_id(dream_id))
        return None if r is None else to_json(r)

    def save_picture_check(self, dream_id: str, result: dict[str, Any]) -> str:
        return self.dreams.save_check(_id(dream_id), from_json(DreamCheckResult, result), "PICTURES")

    def scout_wanted(self, dream_id: str) -> bool:
        return self.dreams.scout_wanted(_id(dream_id))

    def scout_request(self, dream_id: str, notes: str | None) -> dict[str, Any]:
        return to_json(self.dreams.scout_request(_id(dream_id), notes))

    def save_scout(self, dream_id: str, request: dict[str, Any], result: dict[str, Any]) -> str:
        return self.dreams.save_scout(_id(dream_id), from_json(DreamScoutRequest, request), from_json(DreamScoutResult, result))

    def music_source(self, dream_id: str) -> str:
        return self.dreams.music_source(_id(dream_id))

    def music_bed_request(self, dream_id: str) -> dict[str, Any]:
        return to_json(self.dreams.music_bed_request(_id(dream_id)))

    def save_music_bed(self, dream_id: str, result: dict[str, Any]) -> str:
        return self.dreams.save_music_bed(_id(dream_id), from_json(DreamBedResult, result))

    def finish_request(self, dream_id: str) -> dict[str, Any]:
        return to_json(self.dreams.finish_request(_id(dream_id)))

    def save_finish(self, dream_id: str, result: dict[str, Any]) -> str:
        return self.dreams.save_finish(_id(dream_id), from_json(DreamFinishResult, result))

    def status(self, dream_id: str, step: str, status: str, message: str | None) -> None:
        self.dreams.status(_id(dream_id), step, status, message)

    def start_clip_batch(self, dream_id: str) -> None:
        self.dreams.start_clip_batch(_id(dream_id))

    def gpu_batch_ahead(self, dream_id: str, step: str) -> int:
        """Batch order on the shared pod: how many other Shorts should use the pod before this one starts `step` (0 = go now)."""
        return self.dreams.gpu_batch_ahead(_id(dream_id), step)


class DreamProducerActivities:
    """AI Producer for Dream Shorts: what to review, saving its verdict, and whether it may act alone within the owner's limits."""

    def __init__(self, producer: DreamProducer):
        self.producer = producer

    def review_request(self, dream_id: str, step: str) -> dict[str, Any] | None:
        r = self.producer.review_request(dream_id, step)
        return None if r is None else to_json(r)

    def save_review(self, dream_id: str, step: str, attempt: int, result: dict[str, Any]) -> dict[str, Any]:
        return to_json(self.producer.save_review(dream_id, step, attempt, from_json(ProducerReviewResult, result)))

    def decides(self, dream_id: str) -> bool:
        return self.producer.decides(dream_id)

    def may_redo(self, dream_id: str, round_: int) -> bool:
        return self.producer.may_redo(dream_id, round_)

    def shot_ids(self, dream_id: str, item_ids: list[str]) -> list[str]:
        return self.producer.shot_ids(dream_id, list(item_ids))

    def spend_decision(self, dream_id: str, estimate_inr: float) -> dict[str, Any]:
        return to_json(self.producer.spend_decision(dream_id, Decimal(str(estimate_inr))))

    def mark_acted(self, decision_id: str | None, acted: str) -> None:
        self.producer.mark_acted(decision_id, acted)


def activity_functions(dream: DreamActivities, producer: DreamProducerActivities) -> list[Callable[..., Any]]:
    """Every public method as a Temporal activity: `dream.<method>` and `dreamProducer.<method>`. Imported lazily (temporalio is a worker
    dependency)."""
    from temporalio import activity

    out: list[Callable[..., Any]] = []
    for prefix, obj in (("dream", dream), ("dreamProducer", producer)):
        for name in dir(type(obj)):
            if name.startswith("_"):
                continue
            fn = getattr(obj, name)
            if not callable(fn):
                continue

            @functools.wraps(fn)
            def wrapper(*args: Any, __fn: Callable[..., Any] = fn) -> Any:
                return __fn(*args)

            out.append(activity.defn(name=f"{prefix}.{name}")(wrapper))
    return out


def activity_names(dream: type = DreamActivities, producer: type = DreamProducerActivities) -> list[str]:
    return [f"{p}.{n}" for p, c in (("dream", dream), ("dreamProducer", producer)) for n in dir(c) if not n.startswith("_") and callable(getattr(c, n))]
