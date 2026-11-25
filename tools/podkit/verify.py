"""sha256 verification of downloaded model files. Shipped to the pod as-is (VERIFY_B64) and imported by the download script, so the
laptop tests and the pod run the same code. Standard library only."""

from __future__ import annotations

import hashlib
import os

CHUNK = 8 * 1024 * 1024


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path: str, expected_sha256: str, expected_size: int | None = None) -> tuple[bool, str]:
    """(ok, reason). Size is checked first (cheap) so a truncated download fails without hashing 17 GB."""
    if not expected_sha256 or len(expected_sha256) != 64:
        return False, "no valid expected sha256"
    if not os.path.isfile(path):
        return False, "missing"
    size = os.path.getsize(path)
    if expected_size and size != expected_size:
        return False, f"size {size} != {expected_size}"
    got = sha256_file(path)
    if got != expected_sha256.lower():
        return False, f"sha256 {got[:12]}.. != {expected_sha256[:12]}.."
    return True, "ok"
