"""Decimal helpers. Never use float for money or quantities."""
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
_TWO = Decimal("0.01")
_THREE = Decimal("0.001")
_FOUR = Decimal("0.0001")


def D(value) -> Decimal:
    """Convert anything (None, int, float, str, Decimal) to Decimal safely."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def q2(value) -> Decimal:
    return D(value).quantize(_TWO, rounding=ROUND_HALF_UP)


def q3(value) -> Decimal:
    return D(value).quantize(_THREE, rounding=ROUND_HALF_UP)


def q4(value) -> Decimal:
    return D(value).quantize(_FOUR, rounding=ROUND_HALF_UP)
