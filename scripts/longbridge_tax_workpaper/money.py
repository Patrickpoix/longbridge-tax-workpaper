from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

INTERNAL_QUANTUM = Decimal("0.00000001")
CNY_QUANTUM = Decimal("0.01")


def decimal_value(value: Any, *, default: Decimal | None = None) -> Decimal | None:
    if value in (None, ""):
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def q_internal(value: Any) -> Decimal:
    number = decimal_value(value, default=Decimal("0"))
    assert number is not None
    return number.quantize(INTERNAL_QUANTUM, rounding=ROUND_HALF_UP)


def q_cny(value: Any) -> Decimal:
    number = decimal_value(value, default=Decimal("0"))
    assert number is not None
    return number.quantize(CNY_QUANTUM, rounding=ROUND_HALF_UP)


def to_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
