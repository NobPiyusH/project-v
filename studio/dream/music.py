"""Dream Shorts music (Lyria generation or a piece of the owner's music bed). Port of DreamMusic; DreamService keeps the public
methods and delegates here, shared helpers (route, cost, tag, dream, shots) stay there."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from studio.agents.jfmt import fixed, jround
from studio.assets import asset_store as lic
from studio.assets.asset_store import NewAsset
from studio.dream import looks
from studio.dream.llm_types import DreamWorld
from studio.dream.settings import DreamSettings
from studio.dream.wire_types import DreamBedRequest, DreamBedResult
from studio.ledger.budget_guard import GEMINI, BudgetExceededError
from studio.media.types import MusicRequest, MusicResult

LYRIA = "gemini_lyria"
BED = "owner_music_bed"  # provenance provider for pieces of the owner's music bed (cut free on the laptop)
LICENCE_OWNER_MUSIC = uuid.UUID("00000000-0000-0000-0000-00000000a012")
BED_CANDIDATES = 6  # pieces of the bed Gemini compares for one Short
LISTEN_ESTIMATE_INR = Decimal("0.80")  # measured Rs 0.57-0.79 (19 Nov)


def _clock(sec: float) -> str:
    s = jround(sec)
    return f"{s // 60}:{s % 60:02d}"


class DreamMusic:
    def __init__(self, svc: Any, db: Any, assets: Any, pricing: Any, budget: Any, router: Any, evidence: Any, settings: DreamSettings):
        self.svc, self.db, self.assets, self.pricing, self.budget, self.router, self.evidence = svc, db, assets, pricing, budget, router, evidence
        self.music_model, self.music_bed, self.listen_model = settings.music_model, settings.music_bed, settings.listen_model

    def _world(self, d: dict[str, Any]) -> DreamWorld | None:
        raw = d.get("world_json")
        return None if raw is None else DreamWorld.model_validate(raw if isinstance(raw, dict) else json.loads(raw))

    def music_request(self, dream_id: uuid.UUID, notes: str | None) -> MusicRequest:
        d = self.svc.dream(dream_id)
        lk = looks.look(d.get("look"))
        w = self._world(d)
        target = self.svc.short_seconds(len(self.svc.shots(dream_id)), lk)
        inr = self.pricing.music_inr(1)
        self.svc.route(dream_id, "DREAM_MUSIC", "music.dream", LYRIA, inr)
        self.budget.require(inr, "Dream Short music (1 Lyria generation)")
        world = str(d.get("idea")) if w is None else " ".join([w.world or "", "Palette:", w.palette or "", "Light:", w.light or ""])
        prompt = "\n".join([
            f"Instrumental music only, no vocals, no lyrics. About {jround(target)} seconds, for a wordless vertical Short.",
            "Feeling: a calm, slightly eerie dream; soft, warm, floating, nostalgic. Gentle start, no hard drums.",
            "Look of the video: " + lk.brief,
            "World: " + world,
            "" if not notes or not notes.strip() else "Owner's notes: " + notes.strip()]).strip()
        return MusicRequest(self.svc.storage_id(dream_id), self.music_model, prompt, target, 1)

    def save_music(self, dream_id: uuid.UUID, r: MusicResult) -> str:
        songs = max(1, r.segments) if not r.usage else len(r.usage)
        # Recorded as gemini_api: the provider the monthly Gemini cap counts.
        self.svc.cost(dream_id, GEMINI, self.pricing.music_inr(songs), songs, "song", True, f"Dream Short music ({r.model})")
        asset = self.assets.create(NewAsset(None, "audio", r.uri, "audio/wav", None, None, r.duration_sec, LYRIA, r.model, None, None,
                                            lic.LICENCE_GEMINI, True))
        self.db.execute("UPDATE asset SET dream_short_id = %(d)s WHERE id = %(a)s", {"d": dream_id, "a": asset})
        self.db.execute("UPDATE dream_short SET music_asset_id = %(a)s, final_asset_id = NULL, updated_at = now() WHERE id = %(d)s",
                        {"a": asset, "d": dream_id})
        return f"Music: {fixed(r.duration_sec, 0)} s at about {fixed(r.bpm, 0)} BPM ({r.model})."

    def music_source(self, dream_id: uuid.UUID) -> str:
        return "lyria" if self.svc.dream(dream_id).get("music_source") == "lyria" else "bed"

    def _bed_name(self) -> str:
        return self.music_bed[self.music_bed.rfind("/") + 1:]

    def music_bed_request(self, dream_id: uuid.UUID) -> DreamBedRequest:
        """A new piece of the bed each time (seeded by the Short and how many pieces it has had), away from used starts."""
        if not self.music_bed or not self.music_bed.strip():
            raise RuntimeError("No music bed is set: put the track's storage address in DREAM_MUSIC_BED (or choose Lyria for this Short)")
        used = [r["seed"] / 1000.0 for r in self.db.rows(
            "SELECT p.seed FROM provenance p JOIN asset a ON a.provenance_id = p.id WHERE p.provider = %(p)s AND p.model = %(m)s "
            "AND p.seed IS NOT NULL", {"p": BED, "m": self._bed_name()})]
        takes = self.db.rows("SELECT count(*) AS n FROM asset a JOIN provenance p ON p.id = a.provenance_id "
                             "WHERE a.dream_short_id = %(d)s AND p.provider = %(p)s", {"d": dream_id, "p": BED})[0]["n"]
        d = self.svc.dream(dream_id)
        shots = self.svc.shots(dream_id)
        w = self._world(d)
        scenes = [str(s.get("scene") or "") for s in shots if str(s.get("scene") or "").strip()]
        brief = "\n".join([
            "Look: " + looks.look(d.get("look")).brief,
            f"Idea: {d.get('idea')}",
            "" if w is None else f"World: {w.world or ''} Palette: {w.palette or ''} Light: {w.light or ''}",
            "" if not scenes else "Shots, in order: " + " | ".join(scenes)]).strip()
        pictures = [s["picture_uri"] for s in shots if s.get("picture_uri") is not None][:3]
        model: str | None = self.listen_model
        try:
            self.budget.require(LISTEN_ESTIMATE_INR, "Dream Short music pick (Gemini listens to the pieces)")
        except BudgetExceededError:
            model = None  # over the Gemini cap: pick by loudness only, never block the Short over a few paise
        return DreamBedRequest(self.svc.storage_id(dream_id), self.music_bed,
                               self.svc.short_seconds(len(shots), looks.look(d.get("look"))), f"{dream_id}#{takes}", used, model, brief,
                               pictures, BED_CANDIDATES)

    def save_music_bed(self, dream_id: uuid.UUID, r: DreamBedResult) -> str:
        if r.usage is not None:
            self.evidence.cost_check(dream_id, "MUSIC", float(LISTEN_ESTIMATE_INR), float(self.pricing.text_inr(r.usage)))
            self.svc.cost(dream_id, GEMINI, self.pricing.text_inr(r.usage), r.usage.total_tokens, "token", False,
                          f"Dream Short music pick: Gemini listened to {r.heard} pieces ({r.model})")
            pick = self.router.record_external(None, "DREAM_MUSIC_PICK", "audio.listen", GEMINI,
                                               f"best fit of {r.heard} pieces: {r.why or ''}", [{"provider": GEMINI}])
            self.svc.tag("routing_decision", pick, dream_id)
        decision = self.router.record_external(None, "DREAM_MUSIC", "music.dream", "local_laptop",
                                               "owner's music bed: free and his own track (Lyria only when chosen for this Short)",
                                               [{"provider": "local_laptop"}])
        self.svc.tag("routing_decision", decision, dream_id)
        self.svc.cost(dream_id, "local_laptop", Decimal(0), r.duration_sec, "second", False,
                      f"Dream Short music: the owner's music bed ({r.bed_name}), free")
        asset = self.assets.create(NewAsset(
            None, "audio", r.uri, "audio/wav", None, None, r.duration_sec, BED, self._bed_name(),
            f"piece from {_clock(r.start_sec)} to {_clock(r.start_sec + r.duration_sec)} of {self.music_bed}", jround(r.start_sec * 1000),
            LICENCE_OWNER_MUSIC, False))
        self.db.execute("UPDATE asset SET dream_short_id = %(d)s WHERE id = %(a)s", {"d": dream_id, "a": asset})
        self.db.execute("UPDATE dream_short SET music_asset_id = %(a)s, final_asset_id = NULL, updated_at = now() WHERE id = %(d)s",
                        {"a": asset, "d": dream_id})
        how = (f"best of {r.heard} pieces by ear: {r.why}" if r.usage is not None
               else "picked by loudness" if not r.why or not r.why.strip() else r.why)
        return f"Music: {fixed(r.duration_sec, 0)} s of your track \"{r.bed_name}\" from {_clock(r.start_sec)} ({how})."
