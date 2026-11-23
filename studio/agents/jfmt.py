"""Java-compatible number formatting: String.format("%.Nf") and Math.round round HALF_UP; Python rounds half to even."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal


def fixed(x: float, places: int) -> str:
    """Java String.format("%.<places>f", x)."""
    return str(Decimal(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def jround(x: float) -> int:
    """Java Math.round(double): half up."""
    return math.floor(x + 0.5)


def string_hash(s: str) -> int:
    """Java String.hashCode() (a signed 32-bit int)."""
    h = 0
    b = s.encode("utf-16-le")  # Java hashes UTF-16 code units
    for i in range(0, len(b), 2):
        h = (31 * h + (b[i] | b[i + 1] << 8)) & 0xFFFFFFFF
    return h - 0x100000000 if h >= 0x80000000 else h
