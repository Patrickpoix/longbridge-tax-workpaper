"""Cost-basis calculation engines — FIFO and moving-average disposal matching.

Extracted from cost_basis.py to reduce module size.
"""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

from .cost_basis_models import EPS, Lot, MethodResult, CostBasisEvent
from .filing_policy import load_tax_policy, year_end_fx_rate
from .money import decimal_value, q_cny, q_internal, to_float


def _disposal_base(
    event: CostBasisEvent, method: str, allocated_cost: float,
    match_detail: object, status: str, note: str,
) -> dict[str, object]:
    """Build a standard disposal row dict for the given event and method."""
    policy = load_tax_policy()
    fx = year_end_fx_rate(event.currency, policy) if event.currency in {"HKD", "USD"} else None
    pnl_decimal = q_internal(
        decimal_value(event.cash_effect, default=0) - decimal_value(allocated_cost, default=0)
    )
    pnl = to_float(pnl_decimal)
    pnl_cny_decimal = q_internal(pnl_decimal * fx) if fx is not None else None
    tax_decimal = (
        q_cny(max(pnl_cny_decimal, decimal_value(0)) * decimal_value("0.20"))
        if pnl_cny_decimal is not None else None
    )
    return {
        "method": method,
        "statement_month": event.statement_month,
        "trade_date": event.event_date,
        "execution_time": event.event_time,
        "source_reference": event.source_reference,
        "event_type": "AUTO_EX" if event.source_reference.startswith("AUTOEX:") else "TRADE_SELL",
        "security_id": event.security_id,
        "symbol": event.symbol,
        "asset_category": event.asset_category,
        "market": _security_market_for_disposal(event.security_id),
        "currency": event.currency,
        "quantity": round(event.quantity, 8),
        "gross_proceeds": round(event.gross_amount, 8),
        "disposal_fees": round(event.fees, 8),
        "net_proceeds": round(event.cash_effect, 8),
        "allocated_cost": round(allocated_cost, 8),
        "realized_pnl": pnl,
        "year_end_cny_rate": fx,
        "cny_conversion_status": "complete" if fx is not None else "incomplete_missing_fx",
        "realized_pnl_cny": to_float(pnl_cny_decimal),
        "reference_tax_rate": 0.20,
        "reference_tax_on_positive_pnl_cny": to_float(tax_decimal),
        "non_deductible_fee": round(event.non_deductible_fee, 8),
        "allocated_cost_detail": str(match_detail),
        "validation_status": status,
        "note": note,
    }


def _security_market_for_disposal(security_id: str) -> str:
    """Return market string for a disposal row."""
    if security_id.startswith("HK:"):
        return "HK"
    if security_id.startswith("US:") or security_id.startswith("OPT:"):
        return "US"
    return "UNKNOWN"


def run_fifo(opening_lots: list[Lot], events: list[CostBasisEvent]) -> MethodResult:
    """First-In-First-Out cost basis calculation on events per security."""
    from collections import deque

    lots: dict[str, deque[Lot]] = defaultdict(deque)
    for lot in opening_lots:
        lots[lot.security_id].append(lot)

    result = MethodResult(method="FIFO")
    for event in sorted(events, key=lambda e: e.sort_key):
        queue = lots[event.security_id]
        if event.event_type == "SPLIT":
            _apply_split(lots, event)
            continue
        if event.event_type == "BUY":
            queue.append(
                Lot(
                    security_id=event.security_id,
                    symbol=event.symbol,
                    asset_category=event.asset_category,
                    currency=event.currency,
                    acquired_date=event.event_date,
                    acquired_time=event.event_time,
                    quantity=event.quantity,
                    total_cost=abs(event.cash_effect),
                    source_type="trade_buy",
                    source_reference=event.source_reference,
                    source_pdf=event.source_pdf,
                    evidence=event.evidence,
                )
            )
            continue
        security_id = event.security_id
        queue = lots[security_id]
        allocated = 0.0
        remaining_qty = abs(event.quantity)
        match_detail = {}
        status = "ok"

        while remaining_qty > EPS and queue:
            lot = queue[0]
            used = min(lot.quantity, remaining_qty)
            cost_share = lot.total_cost * (used / lot.quantity) if lot.quantity > EPS else 0.0
            allocated += cost_share
            match_detail.setdefault(lot.source_reference, []).append(used)
            lot.quantity -= used
            lot.total_cost -= cost_share
            if lot.quantity <= EPS:
                queue.popleft()
            remaining_qty -= used

        if remaining_qty > EPS:
            status = f"insufficient_lots_remaining_qty={remaining_qty}"
            result.errors.append(f"FIFO {security_id}: {status}")

        result.disposals.append(
            _disposal_base(event, "FIFO", allocated, match_detail, status, f"FIFO matched from {len(match_detail)} lot(s)")
        )

    for security_id, queue in lots.items():
        for lot in queue:
            result.remaining_lots.append(lot.to_dict())
    return result


def _apply_split(lots: dict, event: CostBasisEvent) -> None:
    """Apply a stock split ratio to existing lots for the affected security."""
    if event.split_ratio is None or event.split_ratio <= EPS:
        return
    queue = lots.get(event.security_id)
    if not queue:
        return
    for lot in queue:
        lot.quantity *= event.split_ratio
        # total_cost stays the same


def run_moving_average(
    opening_lots: list[Lot], events: list[CostBasisEvent],
) -> MethodResult:
    """Moving-average cost basis calculation on events per security."""
    from collections import OrderedDict

    state: dict[str, dict[str, Any]] = {}
    for lot in opening_lots:
        sid = lot.security_id
        if sid not in state:
            state[sid] = {"symbol": lot.symbol, "asset_category": lot.asset_category,
                          "currency": lot.currency, "quantity": 0.0, "total_cost": 0.0, "sources": []}
        state[sid]["quantity"] += lot.quantity
        state[sid]["total_cost"] += lot.total_cost
        state[sid]["sources"].append({"type": lot.source_type, "ref": lot.source_reference,
                                      "evidence": lot.evidence})

    result = MethodResult(method="MOVING_AVERAGE")
    for event in sorted(events, key=lambda e: e.sort_key):
        if event.event_type == "SPLIT":
            _apply_split_to_state(state, event)
            continue
        if event.event_type not in ("SELL", "BUY"):
            continue
        sid = event.security_id
        current = state.setdefault(sid, {"symbol": event.symbol, "asset_category": event.asset_category,
                                         "currency": event.currency, "quantity": 0.0, "total_cost": 0.0, "sources": []})

        if event.event_type == "BUY":
            current["quantity"] += abs(event.quantity)
            current["total_cost"] += abs(event.cash_effect)
            current["sources"].append({"type": "BUY", "ref": event.source_reference, "pdf": event.source_pdf})
            continue

        remaining_qty = abs(event.quantity)
        if current["quantity"] <= EPS:
            result.errors.append(f"MOVING_AVERAGE {sid}: sell without prior lot")
            status = "no_prior_lot"
            result.disposals.append(
                _disposal_base(event, "MOVING_AVERAGE", 0.0, {}, status, "No prior lot; allocated cost = 0")
            )
            continue

        unit_cost = current["total_cost"] / current["quantity"]
        used_qty = min(remaining_qty, current["quantity"])
        allocated_cost = unit_cost * used_qty
        remaining_qty -= used_qty
        current["quantity"] -= used_qty
        current["total_cost"] -= allocated_cost
        status = "ok" if remaining_qty <= EPS else f"insufficient_lots_remaining_qty={remaining_qty}"

        if remaining_qty > EPS:
            result.errors.append(f"MOVING_AVERAGE {sid}: {status}")

        match_detail = {"pool_unit_cost": round(unit_cost, 8), "pool_quantity_before": round(unit_cost * used_qty + current["total_cost"], 8)}
        result.disposals.append(
            _disposal_base(event, "MOVING_AVERAGE", allocated_cost, match_detail, status,
                           f"Moving-average pool: {used_qty} @ {unit_cost:.8f}")
        )

    for sid, current in state.items():
        qty = float(current["quantity"])
        if qty <= EPS:
            continue
        tc = float(current["total_cost"])
        result.remaining_lots.append({
            "method": "MOVING_AVERAGE",
            "security_id": sid,
            "symbol": current["symbol"],
            "asset_category": current["asset_category"],
            "currency": current["currency"],
            "acquired_date": "MULTIPLE",
            "acquired_time": None,
            "quantity": round(qty, 8),
            "total_cost": round(tc, 8),
            "unit_cost": round(tc / qty, 8),
            "source_type": "moving_average_pool",
            "source_reference": "multiple",
            "source_pdf": "multiple",
            "evidence": json.dumps(current["sources"], ensure_ascii=False),
        })
    return result


def _apply_split_to_state(state: dict, event: CostBasisEvent) -> None:
    """Apply a stock split ratio to moving-average state."""
    if event.split_ratio is None or event.split_ratio <= EPS:
        return
    sid = event.security_id
    if sid not in state:
        return
    state[sid]["quantity"] *= event.split_ratio
    # total_cost stays the same
