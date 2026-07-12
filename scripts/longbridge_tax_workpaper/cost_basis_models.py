"""Data models for cost-basis tracking — lots, events, and method results.

Extracted from cost_basis.py to reduce module size.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EPS = 1e-8

# Global monotonically increasing sequence counter for CostBasisEvent.
# Guarantees unique ordering across all event types (trades, splits, auto-ex).
_GLOBAL_SEQUENCE: list[int] = [0]


def next_seq() -> int:
    """Return the next globally-unique sequence number for event ordering."""
    _GLOBAL_SEQUENCE[0] += 1
    return _GLOBAL_SEQUENCE[0]


def reset_global_sequence() -> None:
    """Reset the global sequence counter (used in tests)."""
    _GLOBAL_SEQUENCE[0] = 0


@dataclass
class Lot:
    """A purchase lot representing a block of shares acquired at a specific cost."""
    security_id: str
    symbol: str
    asset_category: str
    currency: str
    acquired_date: str
    acquired_time: str
    quantity: float
    total_cost: float
    source_type: str
    source_reference: str
    source_pdf: str
    evidence: str

    def unit_cost(self) -> float | None:
        return self.total_cost / self.quantity if abs(self.quantity) > EPS else None

    def to_dict(self) -> dict[str, object]:
        return {
            "security_id": self.security_id,
            "symbol": self.symbol,
            "asset_category": self.asset_category,
            "currency": self.currency,
            "acquired_date": self.acquired_date,
            "acquired_time": self.acquired_time,
            "quantity": round(self.quantity, 8),
            "total_cost": round(self.total_cost, 8),
            "unit_cost": round(self.unit_cost(), 8) if self.unit_cost() is not None else None,
            "source_type": self.source_type,
            "source_reference": self.source_reference,
            "source_pdf": self.source_pdf,
            "evidence": self.evidence,
        }


@dataclass
class CostBasisEvent:
    """A single event affecting cost basis: BUY, SELL, SPLIT, or AUTO-EX."""
    event_type: str
    event_date: str
    event_time: str
    sequence: int
    statement_month: str
    source_pdf: str
    source_reference: str
    security_id: str
    symbol: str
    asset_category: str
    currency: str
    quantity: float = 0.0
    gross_amount: float = 0.0
    fees: float = 0.0
    cash_effect: float = 0.0
    split_ratio: float | None = None
    evidence: str = ""
    non_deductible_fee: float = 0.0

    @property
    def sort_key(self) -> tuple[str, str, int]:
        return self.event_date, self.event_time, self.sequence


@dataclass
class MethodResult:
    """Cost-basis calculation result for one method (FIFO or MOVING_AVERAGE)."""
    method: str
    disposals: list[dict[str, object]] = field(default_factory=list)
    remaining_lots: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reconciliation: list[dict[str, object]] = field(default_factory=list)
