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
