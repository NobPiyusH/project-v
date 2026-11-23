"""Small broker helpers: provider states, boot timings, pod boot payload, TOTP, worker tokens, learned GPU preference.

Ports of ProviderStates, BootTimings, PodBoot, Totp, WorkerAuth and PreferenceStore.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import struct
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from studio.agents.jfmt import jround
from studio.db import Db

# ============================================================================================ ProviderStates


@dataclass
class ProviderState:
    proven: bool = False
    proven_note: str | None = None
    preflight_ok: bool | None = None
    preflight_at: datetime | None = None
    preflight_note: str | None = None


class MemoryStates:
    """In memory only (tests). Also the base shape of the interface: get / record_preflight / mark_proven / proven."""

    def __init__(self, *proven: str):
        self._map = {p: ProviderState(True, "seeded") for p in proven}

    def get(self, provider: str) -> ProviderState:
        return self._map.get(provider, ProviderState())

    def record_preflight(self, provider: str, ok: bool, note: str) -> None:
        from datetime import UTC

        s = self.get(provider)
        self._map[provider] = ProviderState(s.proven, s.proven_note, ok, datetime.now(UTC), note)

    def mark_proven(self, provider: str, note: str) -> None:
        s = self.get(provider)
        self._map[provider] = ProviderState(True, note, s.preflight_ok, s.preflight_at, s.preflight_note)

    def proven(self, provider: str) -> bool:
        return self.get(provider).proven


class AllProven(MemoryStates):
    """Every provider counts as proven (tests and callers that never asked for the gate)."""

    def get(self, provider: str) -> ProviderState:
        return ProviderState(True, "gate off")

    def record_preflight(self, provider: str, ok: bool, note: str) -> None:
        pass

    def mark_proven(self, provider: str, note: str) -> None:
        pass


class PgStates(MemoryStates):
    """Table gpu_provider_state (V41): proven (a real worker has run on it) and the last free pre-check."""

    def __init__(self, db: Db):
        super().__init__()
        self._db = db

    def get(self, provider: str) -> ProviderState:
        rows = self._db.rows(
            "SELECT proven, proven_note, preflight_ok, preflight_at, preflight_note FROM gpu_provider_state WHERE provider = %(p)s",
            {"p": provider},
        )
        if not rows:
            return ProviderState()
        r = rows[0]
        return ProviderState(r["proven"], r["proven_note"], r["preflight_ok"], r["preflight_at"], r["preflight_note"])

    def record_preflight(self, provider: str, ok: bool, note: str) -> None:
        self._db.execute(
            """
            INSERT INTO gpu_provider_state (provider, preflight_ok, preflight_at, preflight_note) VALUES (%(p)s, %(ok)s, now(), %(n)s)
            ON CONFLICT (provider) DO UPDATE SET preflight_ok = %(ok)s, preflight_at = now(), preflight_note = %(n)s
            """,
            {"p": provider, "ok": ok, "n": note},
        )

    def mark_proven(self, provider: str, note: str) -> None:
        self._db.execute(
            """
            INSERT INTO gpu_provider_state (provider, proven, proven_at, proven_note) VALUES (%(p)s, true, now(), %(n)s)
            ON CONFLICT (provider) DO UPDATE SET proven = true, proven_at = now(), proven_note = %(n)s
            """,
            {"p": provider, "n": note},
        )


# ============================================================================================ BootTimings


@dataclass
class TimingRow:
    pod_id: str
    provider: str
    image: str | None
    rented_at: datetime
    running_at: datetime | None = None
    worker: dict[str, Any] | None = None


def _round1(v: float) -> float:
    return jround(v * 10) / 10.0


def _step(phase: str, seconds_since_rent: float, worker_since_start: float | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {"phase": phase, "secondsSinceRent": seconds_since_rent}
    if worker_since_start is not None:
        m["workerSinceStartSeconds"] = worker_since_start
    return m


def iso_z(t: datetime) -> str:
    """Java Instant.toString(): UTC, trailing zeros of the fraction dropped, Z."""
    from datetime import UTC

    u = t.astimezone(UTC)
    frac = f"{u.microsecond:06d}".rstrip("0")
    return u.strftime("%Y-%m-%dT%H:%M:%S") + (f".{frac}" if frac else "") + "Z"


def summary(row: TimingRow) -> dict[str, Any]:
    """Seconds since the rent for each step we know, in order: rented, provider_running, container_started (the worker's first mark),
    deps_installed, comfyui_fetched, weights_done, comfyui_up, ready, plus the download rows and totalSeconds."""
    out: dict[str, Any] = {"podId": row.pod_id, "provider": row.provider, "image": row.image, "rentedAt": iso_z(row.rented_at)}
    steps = [_step("rented", 0)]
    if row.running_at is not None:
        steps.append(_step("provider_running", _round1(int((row.running_at - row.rented_at).total_seconds() * 1000) / 1000.0)))
    total = None
    if row.worker is not None:
        marks = row.worker.get("marks")
        if isinstance(marks, list):
            for m in marks:
                if isinstance(m, dict) and isinstance(m.get("at"), (int, float)):
                    since = _round1(m["at"] - int(row.rented_at.timestamp() * 1000) / 1000.0)
                    phase = str(m.get("phase"))
                    sss = m.get("since_start_s")
                    steps.append(_step("container_started" if phase == "worker_started" else phase, since,
                                       float(sss) if isinstance(sss, (int, float)) else None))
                    if phase == "ready":
                        total = since
        if isinstance(row.worker.get("downloads"), list):
            out["downloads"] = row.worker["downloads"]
    out["steps"] = steps
    out["totalSeconds"] = total
    return out


class MemoryTimings:
    """Where a rental's boot time goes (rent / running / worker timeline). Never raises into a rental: a lost row costs nothing."""

    def __init__(self) -> None:
        self._rows: dict[str, TimingRow] = {}

    def rented(self, pod_id: str, provider: str, image: str | None, at: datetime) -> None:
        self._rows[pod_id] = TimingRow(pod_id, provider, image, at)

    def running(self, pod_id: str, at: datetime) -> None:
        """First call wins: later 'running' sightings do not move it."""
        r = self._rows.get(pod_id)
        if r is not None and r.running_at is None:
            r.running_at = at

    def worker_report(self, pod_id: str, timing: dict[str, Any]) -> None:
        """The worker's timeline (its /health 'timing' object); the first report per pod wins."""
        r = self._rows.get(pod_id)
        if r is not None and r.worker is None:
            r.worker = timing

    def get(self, pod_id: str) -> TimingRow | None:
        return self._rows.get(pod_id)

    def recent(self, limit: int) -> list[TimingRow]:
        return sorted(self._rows.values(), key=lambda r: r.rented_at, reverse=True)[:limit]


class NoTimings(MemoryTimings):
    """Nothing is kept (default until timings are set)."""

    def rented(self, *a: Any) -> None:
        pass

    def running(self, *a: Any) -> None:
        pass

    def worker_report(self, *a: Any) -> None:
        pass


class PgTimings(MemoryTimings):
    """Table gpu_boot_timing (V42)."""

    _COLS = "pod_id, provider, image, rented_at, running_at, worker::text AS worker"

    def __init__(self, db: Db):
        super().__init__()
        self._db = db

    def rented(self, pod_id, provider, image, at) -> None:
        self._db.execute(
            "INSERT INTO gpu_boot_timing (pod_id, provider, image, rented_at) VALUES (%(id)s, %(p)s, %(i)s, %(t)s) ON CONFLICT (pod_id) DO NOTHING",
            {"id": pod_id, "p": provider, "i": image, "t": at},
        )

    def running(self, pod_id, at) -> None:
        self._db.execute("UPDATE gpu_boot_timing SET running_at = %(t)s WHERE pod_id = %(id)s AND running_at IS NULL", {"t": at, "id": pod_id})

    def worker_report(self, pod_id, timing) -> None:
        self._db.execute(
            "UPDATE gpu_boot_timing SET worker = CAST(%(w)s AS jsonb), reported_at = now() WHERE pod_id = %(id)s AND worker IS NULL",
            {"w": json.dumps(timing), "id": pod_id},
        )

    @staticmethod
    def _row(r: dict[str, Any]) -> TimingRow:
        return TimingRow(r["pod_id"], r["provider"], r["image"], r["rented_at"], r["running_at"],
                         json.loads(r["worker"]) if r["worker"] else None)

    def get(self, pod_id: str) -> TimingRow | None:
        rows = self._db.rows(f"SELECT {self._COLS} FROM gpu_boot_timing WHERE pod_id = %(id)s", {"id": pod_id})
        return self._row(rows[0]) if rows else None

    def recent(self, limit: int) -> list[TimingRow]:
        return [self._row(r) for r in self._db.rows(f"SELECT {self._COLS} FROM gpu_boot_timing ORDER BY rented_at DESC LIMIT %(n)s", {"n": limit})]


# ============================================================================================ PodBoot

_CHUNK = 900


def boot_env(worker_source: bytes, port: int, root: str, volume_mount: str | None = None, baked_root: str | None = None) -> dict[str, str]:
    """Ships workers/pod/server.py to a rented pod with no image build: zlib + base64, split over env vars SV_BOOT_00.. (900-char
    chunks; some providers cap an env value) and a short command that joins, decodes and execs it."""
    b64 = base64.b64encode(zlib.compress(worker_source, 9)).decode("ascii")
    env: dict[str, str] = {}
    n = 0
    for i in range(0, len(b64), _CHUNK):
        env[f"SV_BOOT_{n:02d}"] = b64[i : i + _CHUNK]
        n += 1
    env["SV_BOOT_N"] = str(n)
    env["SV_PORT"] = str(port)
    env["SV_ROOT"] = root
    # Hugging Face xet failed every FLUX download on two pods (21 Nov 2026): plain HTTP from the first try.
    env["HF_HUB_DISABLE_XET"] = "1"
    if volume_mount and volume_mount.strip():
        env["SV_VOLUME"] = volume_mount
    if baked_root and baked_root.strip():
        env["SV_BAKED"] = baked_root
    return env


def boot_command() -> list[str]:
    py = ("import os,zlib,base64;exec(zlib.decompress(base64.b64decode(''.join(os.environ['SV_BOOT_%02d'%i] "
          "for i in range(int(os.environ['SV_BOOT_N']))))))")
    return ["python", "-c", py]


# ============================================================================================ Totp

TOTP_STEP_SECONDS = 30


def totp_base32(s: str | None) -> bytes:
    """RFC 4648 base32; case, spaces, dashes and '=' padding are ignored. Unknown characters are an error (a typo must not give a
    wrong code silently)."""
    text = re.sub(r"[\s=-]", "", s or "").upper()
    buffer = bits = 0
    out = bytearray()
    for c in text:
        if "A" <= c <= "Z":
            v = ord(c) - ord("A")
        elif "2" <= c <= "7":
            v = ord(c) - ord("2") + 26
        else:
            raise ValueError("TOTP secret is not valid base32")
        buffer = (buffer << 5) | v
        bits += 5
        if bits >= 8:
            out.append((buffer >> (bits - 8)) & 0xFF)
            bits -= 8
            buffer &= (1 << bits) - 1
    return bytes(out)


def totp_code(base32_secret: str, epoch_seconds: int, digits: int = 6) -> str:
    """RFC 6238 (HMAC-SHA1, 30 s step): the code for the window containing epoch_seconds, zero-padded to digits."""
    key = totp_base32(base32_secret)
    if not key:
        raise ValueError("TOTP secret is empty or not base32")
    h = hmac.new(key, struct.pack(">Q", epoch_seconds // TOTP_STEP_SECONDS), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    binary = ((h[o] & 0x7F) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3]
    return str(binary % (10**digits)).zfill(digits)


def millis_to_next_window(now_millis: int) -> int:
    return TOTP_STEP_SECONDS * 1000 - now_millis % (TOTP_STEP_SECONDS * 1000)


# ============================================================================================ WorkerAuth

WORKER_TOKEN_ENV = "SV_TOKEN"


class WorkerAuth:
    """Per-rental bearer token for the pod worker's HTTP port (env SV_TOKEN). A Vast.ai port is public (direct IP:port), so the
    worker answers 401 to everything without it. The token is HMAC-SHA256(secret, pod name): nothing to store, a control-plane
    restart derives the same token again for a recovered rental, and the host of one pod cannot compute the token of another. The
    secret is POD_TOKEN_SECRET; when empty it is derived from the provider keys already in .env, and when there are none, random
    for this run. The token reaches the Python side inside the pod address (http://worker:<token>@host:port)."""

    def __init__(self, secret: bytes = b"", providers: set[str] | frozenset[str] = frozenset()):
        self._secret, self._providers = secret, set(providers)

    @staticmethod
    def from_settings(secret: str, runpod_key: str, vast_key: str, runpod: bool = True) -> WorkerAuth:
        return WorkerAuth(
            WorkerAuth._secret_bytes(secret, runpod_key, vast_key),
            {"vast", "e2e", "jarvislabs", "runpod"} if runpod else {"vast", "e2e", "jarvislabs"},
        )

    @staticmethod
    def _secret_bytes(secret: str, runpod_key: str, vast_key: str) -> bytes:
        if secret and secret.strip():
            return secret.strip().encode()
        keys = f"{(runpod_key or '').strip()}|{(vast_key or '').strip()}"
        if len(keys) > 1:
            return hashlib.sha256(f"projectv-worker-token|{keys}".encode()).digest()
        return secrets.token_bytes(32)

    def enabled_for(self, provider: str) -> bool:
        return len(self._secret) > 0 and provider in self._providers

    def token(self, pod_name: str) -> str:
        """The token for one pod name (48 hex characters)."""
        return hmac.new(self._secret, pod_name.encode(), hashlib.sha256).digest()[:24].hex()

    def env(self, provider: str, pod_name: str, env: dict[str, str] | None) -> dict[str, str]:
        """The worker env plus SV_TOKEN when this provider uses tokens (the input map is not changed)."""
        if not self.enabled_for(provider):
            return env or {}
        out = dict(env or {})
        out[WORKER_TOKEN_ENV] = self.token(pod_name)
        return out

    def decorate(self, provider: str, pod_name: str, endpoint: str | None) -> str | None:
        """http://host:port -> http://worker:<token>@host:port; unchanged when no token applies or no endpoint."""
        if endpoint is None or not self.enabled_for(provider):
            return endpoint
        i = endpoint.find("://")
        if i < 0 or "@" in endpoint:
            return endpoint
        return endpoint[: i + 3] + "worker:" + self.token(pod_name) + "@" + endpoint[i + 3 :]


# ============================================================================================ PreferenceStore

MAX_PREMIUM = 1.5  # Auto mode never pays more than this multiple of the cheapest price for a learned preference


class NoPreferences:
    def learned_key(self) -> str | None:
        return None

    def record_choice(self, short_id: uuid.UUID, chosen_key: str, cheapest_key: str | None, context: dict[str, Any]) -> None:
        pass


class PgPreferences:
    """What the studio learns from the owner's manual GPU picks (preference_event, kind gpu_choice). When 3 of the owner's last 5
    picks were the same option and each time it was NOT the cheapest, Auto mode prefers that option (still only if it is available,
    inside its cap, and at most MAX_PREMIUM x the cheapest price)."""

    def __init__(self, db: Db):
        self._db = db

    def learned_key(self) -> str | None:
        rows = self._db.rows("SELECT chosen, rejected FROM preference_event WHERE kind = 'gpu_choice' ORDER BY ts DESC LIMIT 5")
        counts: dict[str, int] = {}
        for r in rows:
            if r["chosen"] is not None and r["chosen"] != r["rejected"]:
                counts[str(r["chosen"])] = counts.get(str(r["chosen"]), 0) + 1
        return next((k for k, v in counts.items() if v >= 3), None)

    def record_choice(self, short_id, chosen_key, cheapest_key, context) -> None:
        self._db.execute(
            """
            INSERT INTO preference_event (id, kind, context_json, chosen, rejected)
            VALUES (%(id)s, 'gpu_choice', CAST(%(ctx)s AS jsonb), %(chosen)s, %(rejected)s)
            """,
            {"id": uuid.uuid4(), "ctx": json.dumps(context, default=str), "chosen": chosen_key, "rejected": cheapest_key},
        )

