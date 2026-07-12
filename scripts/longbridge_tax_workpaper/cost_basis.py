"""Cost basis report — event construction, opening lots, reconciliation, summary.

This module is the orchestration layer.  Security-ID resolution lives in
``security.py``, data models in ``cost_basis_models.py``, and FIFO/moving-average
engines in ``cost_basis_engine.py``.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .cost_basis_engine import run_fifo, run_moving_average
from .cost_basis_models import (
    EPS, Lot, CostBasisEvent, MethodResult, next_seq, reset_global_sequence,
)
from .filing_policy import load_tax_policy, year_end_fx_rate
from .hashing import sha256_file
from .money import decimal_value, q_cny, q_internal, to_float
from .schema import FieldValue, SectionResult, StatementResult
from .security import (
    asset_category, canonical_security_id, norm_text, security_market,
    SECURITY_CODE_RE,
)

# --- Regex constants used in event construction ---
SPLIT_RE = re.compile(
    r"Stock\s+Split\s+Amount:\s*([\d.]+)\s+for\s+([\d.]+)", re.IGNORECASE
)
HELD_QTY_RE = re.compile(
    r"Held\s*[:：]\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE
)
SPLIT_CASH_RE = re.compile(
    r"(?:cash\s+in\s+lieu|fractional\s+share|cash.*fractional|零碎股|碎股|现金替代)",
    re.IGNORECASE,
)


def _value(row: dict[str, FieldValue] | None, name: str):
    """Safely extract the value from a FieldValue row."""
    if not row:
        return None
    value = row.get(name)
    return value.value if value else None


def _parse_dt(
    date_text: object, time_text: object, fallback_index: int,
) -> tuple[str, str, int]:
    """Parse date/time from statement text with a fallback."""
    date_value = str(date_text or "").replace(".", "-")
    time_value = str(time_text or "") or "23:59:59"
    try:
        datetime.strptime(f"{date_value} {time_value}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        time_value = "23:59:59"
    return date_value, time_value, fallback_index


# ---- Event construction ---------------------------------------------------

def _find_trade_rows(statement: StatementResult) -> list[CostBasisEvent]:
    events: list[CostBasisEvent] = []
    for section_asset_type, section_name in (
        ("stock", "stock_trades"), ("option", "option_trades")
    ):
        section = statement.sections.get(
            section_name, SectionResult(name=section_name)
        )
        for row_index, row in enumerate(section.rows, start=1):
            symbol = norm_text(_value(row, "symbol"))
            security_id = canonical_security_id(symbol, asset_type=section_asset_type)
            side = str(
                _value(row, "side")
                or (
                    "SELL"
                    if "卖" in str(_value(row, "direction") or "")
                    else "BUY"
                )
            )
            quantity = float(_value(row, "quantity") or 0.0)
            amount = float(_value(row, "amount") or 0.0)
            total_amount = float(_value(row, "total_amount") or 0.0)
            currency = str(_value(row, "currency") or "UNKNOWN")
            order_time = _value(row, "order_time")
            execution_time = _value(row, "execution_time") or order_time
            date_value, time_value, _ = _parse_dt(
                _value(row, "trade_date"), execution_time, row_index
            )
            if side == "BUY":
                fees = round(max(0.0, abs(total_amount) - amount), 8)
                cash_effect = -abs(total_amount)
            else:
                fees = round(max(0.0, amount - total_amount), 8)
                cash_effect = total_amount
            events.append(
                CostBasisEvent(
                    event_type=side,
                    event_date=date_value,
                    event_time=time_value,
                    sequence=next_seq(),
                    statement_month=statement.statement_month,
                    source_pdf=Path(statement.source_pdf).name,
                    source_reference=str(
                        _value(row, "order_id")
                        or f"{statement.statement_month}:{section_name}:{row_index}"
                    ),
                    security_id=security_id,
                    symbol=symbol,
                    asset_category=asset_category(
                        section_asset_type, symbol, security_id
                    ),
                    currency=currency,
                    quantity=quantity,
                    gross_amount=amount,
                    fees=fees,
                    cash_effect=cash_effect,
                    evidence=json.dumps(
                        {
                            key: _value(row, key)
                            for key in (
                                "trade_date", "order_id", "direction", "symbol",
                                "quantity", "price", "amount", "total_amount",
                                "order_time", "execution_time", "market_timezone",
                            )
                        },
                        ensure_ascii=False,
                    ),
                )
            )
    return events


def _split_events(statements: Iterable[StatementResult]) -> list[CostBasisEvent]:
    events: list[CostBasisEvent] = []
    for statement in statements:
        section = statement.sections.get(
            "other_fund_flows", SectionResult(name="other_fund_flows")
        )
        seen: set[tuple[str, str]] = set()
        for row_index, row in enumerate(section.rows, start=1):
            raw_detail = norm_text(_value(row, "raw_detail"))
            split_match = SPLIT_RE.search(raw_detail)
            if not split_match:
                continue
            date_value = str(_value(row, "date") or "").replace(".", "-")
            security_id = canonical_security_id(raw_detail, asset_type="stock")
            key = (date_value, security_id)
            if key in seen:
                continue
            seen.add(key)
            numerator = float(split_match.group(1))
            denominator = float(split_match.group(2))
            if denominator == 0:
                continue
            events.append(
                CostBasisEvent(
                    event_type="SPLIT",
                    event_date=date_value,
                    event_time="00:00:00",
                    sequence=next_seq(),
                    statement_month=statement.statement_month,
                    source_pdf=Path(statement.source_pdf).name,
                    source_reference=f"split:{statement.statement_month}:{row_index}",
                    security_id=security_id,
                    symbol=raw_detail,
                    asset_category="stock",
                    currency=str(_value(row, "currency") or "UNKNOWN"),
                    split_ratio=numerator / denominator,
                    evidence=raw_detail,
                )
            )
    return events


# ---- Candidate matching helpers -------------------------------------------

def _nearest_candidate(
    candidates: list[tuple[int, dict[str, FieldValue]]],
    *,
    anchor_index: int,
    expected_quantity: float | None = None,
) -> tuple[tuple[int, dict[str, FieldValue]] | None, str]:
    if not candidates:
        return None, "missing"
    eligible = candidates
    if expected_quantity is not None:
        exact = [
            item
            for item in candidates
            if abs(
                abs(float(_value(item[1], "quantity_change") or 0.0))
                - expected_quantity
            )
            <= EPS
        ]
        if exact:
            eligible = exact
    ranked = sorted(eligible, key=lambda item: (abs(item[0] - anchor_index), item[0]))
    selected = ranked[0]
    status = (
        "matched_by_held_and_nearest_row"
        if expected_quantity is not None and eligible is not candidates
        else "matched_by_nearest_row"
    )
    return selected, status


def _pop_candidate(
    candidates: list[tuple[int, dict[str, FieldValue]]],
    *,
    anchor_index: int,
    expected_quantity: float | None = None,
) -> tuple[tuple[int, dict[str, FieldValue]] | None, str]:
    selected, status = _nearest_candidate(
        candidates, anchor_index=anchor_index, expected_quantity=expected_quantity
    )
    if selected is not None:
        candidates.remove(selected)
    return selected, status


def _auto_ex_events(statements: Iterable[StatementResult]) -> list[CostBasisEvent]:
    """Pair AUTO-EX proceeds, stock-out quantity, and fees deterministically."""
    events: list[CostBasisEvent] = []
    for statement in statements:
        flows = statement.sections.get(
            "other_fund_flows", SectionResult(name="other_fund_flows")
        ).rows
        groups: dict[
            str, dict[str, list[tuple[int, dict[str, FieldValue]]]]
        ] = defaultdict(
            lambda: {"proceeds": [], "stock_out": [], "handling": [], "other_fee": []}
        )
        for row_index, row in enumerate(flows, start=1):
            detail = str(_value(row, "raw_detail") or "")
            code_match = SECURITY_CODE_RE.search(detail)
            if not code_match:
                continue
            code = code_match.group(1)
            date_text = str(_value(row, "date") or "")
            key = (date_text, code)
            category = str(_value(row, "tax_category") or "")
            row_type = str(_value(row, "type") or "")
            if category == "derivative_auto_ex_proceeds":
                groups[key]["proceeds"].append((row_index, row))
            elif row_type == "company_action_stock_out" and "AUTO-EX" in detail.upper():
                groups[key]["stock_out"].append((row_index, row))
            elif category == "derivative_settlement_fee_deductible":
                groups[key]["handling"].append((row_index, row))
            elif category == "derivative_settlement_fee_non_deductible":
                groups[key]["other_fee"].append((row_index, row))

        for (date_text, code), group in sorted(groups.items()):
            stock_out = list(group["stock_out"])
            handling = list(group["handling"])
            other_fees = list(group["other_fee"])
            for event_index, (row_index, proceeds_row) in enumerate(
                sorted(group["proceeds"]), start=1
            ):
                raw_detail = norm_text(_value(proceeds_row, "raw_detail"))
                expected_match = HELD_QTY_RE.search(raw_detail)
                expected_quantity = (
                    float(expected_match.group(1).replace(",", ""))
                    if expected_match
                    else None
                )
                selected_stock, stock_status = _pop_candidate(
                    stock_out,
                    anchor_index=row_index,
                    expected_quantity=expected_quantity,
                )
                quantity = (
                    abs(float(_value(selected_stock[1], "quantity_change") or 0.0))
                    if selected_stock
                    else 0.0
                )
                selected_handling, handling_status = _pop_candidate(
                    handling, anchor_index=row_index
                )
                selected_other_fee, other_fee_status = _pop_candidate(
                    other_fees, anchor_index=row_index
                )
                handling_fee = (
                    abs(float(_value(selected_handling[1], "cash_amount") or 0.0))
                    if selected_handling
                    else 0.0
                )
                other_fee = (
                    abs(float(_value(selected_other_fee[1], "cash_amount") or 0.0))
                    if selected_other_fee
                    else 0.0
                )
                proceeds = float(
                    _value(proceeds_row, "cash_amount")
                    or _value(proceeds_row, "amount")
                    or 0.0
                )
                evidence_parts = [
                    raw_detail,
                    f"stock_out_match={stock_status}",
                    f"handling_match={handling_status}",
                    f"other_fee_match={other_fee_status}",
                ]
                for selected in (selected_stock, selected_handling, selected_other_fee):
                    if selected:
                        evidence_parts.append(
                            str(_value(selected[1], "raw_detail") or "")
                        )

                security_id = f"HK:{int(code):05d}"
                events.append(
                    CostBasisEvent(
                        event_type="SELL",
                        event_date=date_text.replace(".", "-"),
                        event_time="23:59:59",
                        sequence=next_seq(),
                        statement_month=statement.statement_month,
                        source_pdf=Path(statement.source_pdf).name,
                        source_reference=f"AUTOEX:{statement.statement_month}:{date_text}:{code}:{event_index}",
                        security_id=security_id,
                        symbol=raw_detail,
                        asset_category="warrant",
                        currency="HKD",
                        quantity=quantity,
                        gross_amount=proceeds,
                        fees=handling_fee,
                        cash_effect=round(proceeds - handling_fee, 8),
                        non_deductible_fee=other_fee,
                        evidence=" | ".join(evidence_parts),
                    )
                )
    return events


# ---- Opening lot construction ---------------------------------------------

def _build_opening_lots(
    statements: Iterable[StatementResult], prior_statements: Iterable[StatementResult],
) -> tuple[list[Lot], list[str]]:
    """Build opening lots from statement data and prior-period history."""
    from collections import OrderedDict

    opening_lots: list[Lot] = []
    errors: list[str] = []
    if prior_statements:
        prior_events: list[CostBasisEvent] = []
        for stmt in prior_statements:
            prior_events.extend(_find_trade_rows(stmt))
        prior_events.extend(_split_events(prior_statements))
        prior_events.extend(_auto_ex_events(prior_statements))
        fifo_result = run_fifo([], prior_events)
        errors.extend(fifo_result.errors)
        for lot_dict in fifo_result.remaining_lots:
            opening_lots.append(
                Lot(
                    security_id=str(lot_dict["security_id"]),
                    symbol=str(lot_dict["symbol"]),
                    asset_category=str(lot_dict["asset_category"]),
                    currency=str(lot_dict["currency"]),
                    acquired_date=str(lot_dict.get("acquired_date", "")),
                    acquired_time=str(lot_dict.get("acquired_time") or ""),
                    quantity=float(lot_dict["quantity"]),
                    total_cost=float(lot_dict["total_cost"]),
                    source_type="fifo_prior_period",
                    source_reference=str(lot_dict.get("source_reference", "")),
                    source_pdf=str(lot_dict.get("source_pdf", "")),
                    evidence=str(lot_dict.get("evidence", "")),
                )
            )
        moving_result = run_moving_average([], prior_events)
        errors.extend(moving_result.errors)
        for lot_dict in moving_result.remaining_lots:
            opening_lots.append(
                Lot(
                    security_id=str(lot_dict["security_id"]),
                    symbol=str(lot_dict["symbol"]),
                    asset_category=str(lot_dict["asset_category"]),
                    currency=str(lot_dict["currency"]),
                    acquired_date="MULTIPLE",
                    acquired_time=None,
                    quantity=float(lot_dict["quantity"]),
                    total_cost=float(lot_dict["total_cost"]),
                    source_type="moving_average_prior_period",
                    source_reference="multiple",
                    source_pdf="multiple",
                    evidence=str(lot_dict.get("evidence", "")),
                )
            )
    else:
        ordered = sorted(
            list(statements), key=lambda item: item.statement_month
        )
        seen_holdings: set[str] = set()
        for statement in ordered:
            holdings = statement.sections.get(
                "holdings", SectionResult(name="holdings")
            )
            for row in holdings.rows:
                symbol = norm_text(_value(row, "name"))
                asset_type = (
                    "option" if str(_value(row, "asset_type") or "") == "期权" else "stock"
                )
                sid = canonical_security_id(symbol, asset_type=asset_type)
                if sid in seen_holdings:
                    continue
                seen_holdings.add(sid)
                qty = float(_value(row, "opening_position") or 0.0)
                cost = float(_row_cost(row))
                if qty <= EPS:
                    continue
                if cost <= -EPS:
                    errors.append(
                        f"{sid} opening statement unit cost is non-positive ({cost}); "
                        "broker display cost is not used as tax basis"
                    )
                    continue
                opening_lots.append(
                    Lot(
                        security_id=sid,
                        symbol=symbol,
                        asset_category=asset_category(asset_type, symbol, sid),
                        currency=str(_value(row, "currency") or "UNKNOWN"),
                        acquired_date=f"{statement.statement_month[:4]}-01-01",
                        acquired_time="00:00:00",
                        quantity=qty,
                        total_cost=cost,
                        source_type="statement_display_cost",
                        source_reference=f"{statement.statement_month}",
                        source_pdf=Path(statement.source_pdf).name,
                        evidence=json.dumps(
                            {
                                key: _value(row, key)
                                for key in (
                                    "name", "opening_position", "ending_position",
                                    "opening_cost", "currency",
                                )
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
    return opening_lots, errors


def _row_cost(row: dict[str, FieldValue]) -> float:
    """Extract the cost value from a holdings row."""
    cost = _value(row, "average_cost")
    if cost is None or cost == 0:
        cost = _value(row, "opening_cost")
    if cost is None or cost == 0:
        cost = _value(row, "cost_price")
    return float(cost or 0.0)


def build_opening_lots(
    statements: Iterable[StatementResult],
    prior_statements: Iterable[StatementResult] = (),
) -> tuple[list[Lot], list[str]]:
    """Build opening lots from the first statement's holdings."""
    ordered = sorted(list(statements), key=lambda item: item.statement_month)
    if not ordered:
        return [], ["no_tax_year_statements"]
    first = ordered[0]
    errors: list[str] = []
    lots: list[Lot] = []
    holdings = first.sections.get("holdings", SectionResult(name="holdings"))
    for row_index, row in enumerate(holdings.rows, start=1):
        quantity = float(_value(row, "opening_position") or 0.0)
        if quantity <= EPS:
            continue
        symbol = norm_text(_value(row, "name"))
        security_id = canonical_security_id(symbol, asset_type="stock")
        currency = str(_value(row, "currency") or "UNKNOWN")
        unit_cost = _value(row, "cost")
        if unit_cost is None:
            errors.append(f"{security_id} opening holding has no statement unit cost")
            continue
        unit_cost = float(unit_cost)
        if unit_cost <= 0:
            errors.append(
                f"{security_id} opening statement unit cost is non-positive ({unit_cost}); "
                "broker display cost is not used as tax basis"
            )
            continue
        lot = Lot(
            security_id=security_id,
            symbol=symbol,
            asset_category=asset_category("stock", symbol, security_id),
            currency=currency,
            acquired_date=f"{int(first.statement_month[:4]) - 1}-12-31",
            acquired_time="23:59:59",
            quantity=quantity,
            total_cost=round(quantity * unit_cost, 8),
            source_type="opening_statement_average_cost",
            source_reference=f"{first.statement_month}:holdings:{row_index}",
            source_pdf=Path(first.source_pdf).name,
            evidence=f"opening_position={quantity}; statement_unit_cost={unit_cost}; source_pdf_sha256={sha256_file(first.source_pdf)}",
        )
        lots.append(lot)
    return lots, errors


def _prior_period_opening_lots(
    prior_list: list[StatementResult], *, tax_year: int,
) -> tuple[list[Lot], list[Lot], list[dict[str, object]], list[str], dict[str, object]]:
    """Reconstruct opening lots from prior-period trade history."""
    fifo_lots: list[Lot] = []
    moving_lots: list[Lot] = []
    opening_rows: list[dict[str, object]] = []
    errors: list[str] = []
    prior_events: list[CostBasisEvent] = []
    for stmt in prior_list:
        prior_events.extend(_find_trade_rows(stmt))
    prior_events.extend(_split_events(prior_list))
    prior_events.extend(_auto_ex_events(prior_list))

    fifo_result = run_fifo([], prior_events)
    errors.extend(fifo_result.errors)
    for lot_dict in fifo_result.remaining_lots:
        lot = Lot(
            security_id=str(lot_dict["security_id"]),
            symbol=str(lot_dict["symbol"]),
            asset_category=str(lot_dict["asset_category"]),
            currency=str(lot_dict["currency"]),
            acquired_date=str(lot_dict.get("acquired_date", "")),
            acquired_time=str(lot_dict.get("acquired_time") or ""),
            quantity=float(lot_dict["quantity"]),
            total_cost=float(lot_dict["total_cost"]),
            source_type="fifo_from_prior_trades",
            source_reference=str(lot_dict.get("source_reference", "")),
            source_pdf=str(lot_dict.get("source_pdf", "")),
            evidence=str(lot_dict.get("evidence", "")),
        )
        fifo_lots.append(lot)
        row = lot.to_dict()
        row["method"] = "FIFO"
        row["evidence_status"] = "verified_from_complete_prior_trade_ledger"
        opening_rows.append(row)

    moving_result = run_moving_average([], prior_events)
    errors.extend(moving_result.errors)
    for lot_dict in moving_result.remaining_lots:
        lot = Lot(
            security_id=str(lot_dict["security_id"]),
            symbol=str(lot_dict["symbol"]),
            asset_category=str(lot_dict["asset_category"]),
            currency=str(lot_dict["currency"]),
            acquired_date="MULTIPLE",
            acquired_time=None,
            quantity=float(lot_dict["quantity"]),
            total_cost=float(lot_dict["total_cost"]),
            source_type="moving_average_from_prior_trades",
            source_reference="multiple",
            source_pdf="multiple",
            evidence=str(lot_dict.get("evidence", "")),
        )
        moving_lots.append(lot)
        row = lot.to_dict()
        row["method"] = "MOVING_AVERAGE"
        row["evidence_status"] = "verified_from_complete_prior_trade_ledger"
        opening_rows.append(row)

    from .cost_basis_models import _GLOBAL_SEQUENCE
    prior_coverage = {
        "status": "ok",
        "actual_months": sorted(
            set(str(s.statement_month) for s in prior_list)
        ),
        "expected_months": [f"{tax_year - 1}{m:02d}" for m in range(1, 13)],
        "monthly_reconciliation": [],
        "monthly_reconciliation_error_count": 0,
        "monthly_reconciliation_status": "not_applicable",
    }
    return fifo_lots, moving_lots, opening_rows, errors, prior_coverage


# ---- Event assembly -------------------------------------------------------

def _split_cash_compensation_issues(
    statements: Iterable[StatementResult],
) -> list[str]:
    issues: list[str] = []
    for statement in statements:
        section = statement.sections.get(
            "other_fund_flows", SectionResult(name="other_fund_flows")
        )
        for row_index, row in enumerate(section.rows, start=1):
            detail = norm_text(_value(row, "raw_detail"))
            if SPLIT_CASH_RE.search(detail) and ("split" in detail.lower() or "拆股" in detail or "合股" in detail):
                issues.append(
                    f"{statement.statement_month}:fund_flow:{row_index}: split cash/fractional-share compensation requires manual basis allocation"
                )
    return issues


def build_cost_basis_events(
    statements: list[StatementResult],
) -> list[CostBasisEvent]:
    """Build all cost-basis events from statement trade rows, splits, and auto-ex."""
    events: list[CostBasisEvent] = []
    for statement in statements:
        events.extend(_find_trade_rows(statement))
    events.extend(_split_events(statements))
    events.extend(_auto_ex_events(statements))
    return sorted(events, key=lambda item: item.sort_key)


# ---- Reconciliation & reporting -------------------------------------------

def _ending_holding_quantities(
    statements: Iterable[StatementResult],
) -> dict[str, dict[str, object]]:
    ordered = sorted(list(statements), key=lambda item: item.statement_month)
    if not ordered:
        return {}
    last = ordered[-1]
    output: dict[str, dict[str, object]] = {}
    holdings = last.sections.get("holdings", SectionResult(name="holdings"))
    for row in holdings.rows:
        symbol = norm_text(_value(row, "name"))
        asset_type = "option" if str(_value(row, "asset_type") or "") == "期权" else "stock"
        security_id = canonical_security_id(symbol, asset_type=asset_type)
        output[security_id] = {
            "statement_ending_quantity": float(_value(row, "ending_position") or 0.0),
            "statement_symbol": symbol,
            "source_pdf": Path(last.source_pdf).name,
        }
    return output


def _reconcile(
    result: MethodResult, statements: Iterable[StatementResult],
) -> None:
    expected = _ending_holding_quantities(statements)
    computed: dict[str, float] = defaultdict(float)
    for lot in result.remaining_lots:
        computed[str(lot["security_id"])] += float(lot["quantity"])
    security_ids = sorted(set(expected) | set(computed))
    for sid in security_ids:
        expected_qty = float(expected.get(sid, {}).get("statement_ending_quantity", 0.0))
        computed_qty = float(computed.get(sid, 0.0))
        diff = round(computed_qty - expected_qty, 8)
        status = "ok" if abs(diff) <= EPS else "error"
        if status == "error":
            result.errors.append(f"{sid}: year-end quantity difference {diff}")
        result.reconciliation.append(
            {
                "method": result.method,
                "security_id": sid,
                "symbol": expected.get(sid, {}).get("statement_symbol")
                or next(
                    (
                        str(row["symbol"])
                        for row in result.remaining_lots
                        if row["security_id"] == sid
                    ),
                    sid,
                ),
                "computed_ending_quantity": round(computed_qty, 8),
                "statement_ending_quantity": round(expected_qty, 8),
                "difference": diff,
                "validation_status": status,
                "source_pdf": expected.get(sid, {}).get("source_pdf", ""),
            }
        )


def _difference_rows(
    fifo: MethodResult, moving: MethodResult,
) -> list[dict[str, object]]:
    moving_map = {str(row["source_reference"]): row for row in moving.disposals}
    output: list[dict[str, object]] = []
    for fifo_row in fifo.disposals:
        key = str(fifo_row["source_reference"])
        avg_row = moving_map.get(key)
        if avg_row is None:
            output.append(
                {
                    "source_reference": key,
                    "validation_status": "error",
                    "note": "missing moving-average disposal row",
                }
            )
            continue
        fifo_cny = decimal_value(fifo_row.get("realized_pnl_cny"))
        moving_cny = decimal_value(avg_row.get("realized_pnl_cny"))
        output.append(
            {
                "source_reference": key,
                "trade_date": fifo_row["trade_date"],
                "security_id": fifo_row["security_id"],
                "symbol": fifo_row["symbol"],
                "asset_category": fifo_row["asset_category"],
                "currency": fifo_row["currency"],
                "quantity": fifo_row["quantity"],
                "net_proceeds": fifo_row["net_proceeds"],
                "fifo_allocated_cost": fifo_row["allocated_cost"],
                "moving_average_allocated_cost": avg_row["allocated_cost"],
                "fifo_realized_pnl": fifo_row["realized_pnl"],
                "moving_average_realized_pnl": avg_row["realized_pnl"],
                "pnl_difference": to_float(
                    q_internal(
                        decimal_value(fifo_row["realized_pnl"], default=0)
                        - decimal_value(avg_row["realized_pnl"], default=0)
                    )
                ),
                "fifo_realized_pnl_cny": fifo_row["realized_pnl_cny"],
                "moving_average_realized_pnl_cny": avg_row["realized_pnl_cny"],
                "pnl_difference_cny": to_float(
                    q_internal(fifo_cny - moving_cny)
                )
                if fifo_cny is not None and moving_cny is not None
                else None,
                "cny_conversion_status": "complete"
                if fifo_cny is not None and moving_cny is not None
                else "incomplete_missing_fx",
                "validation_status": "ok"
                if fifo_row["validation_status"] == "ok"
                   and avg_row["validation_status"] == "ok"
                else "error",
                "note": "Both methods use acquisition costs including buy fees "
                        "and net disposal proceeds after sell fees.",
            }
        )
    return output


def _summary_rows(
    method_results: Iterable[MethodResult],
) -> list[dict[str, object]]:
    policy = load_tax_policy()
    loss_rule = policy.get("property_transfer_loss_offset", {})
    loss_status = str(loss_rule.get("status") or "unconfirmed")
    loss_treatment = str(loss_rule.get("treatment") or "unconfirmed")
    buckets: dict[
        tuple[str, str, str, str], dict[str, object]
    ] = defaultdict(
        lambda: {
            "gross_proceeds": decimal_value(0),
            "fees": decimal_value(0),
            "net_proceeds": decimal_value(0),
            "cost": decimal_value(0),
            "pnl": decimal_value(0),
            "pnl_cny": decimal_value(0),
            "positive_pnl_cny": decimal_value(0),
            "negative_pnl_cny": decimal_value(0),
            "cny_complete": True,
        }
    )
    for result in method_results:
        for row in result.disposals:
            key = (
                result.method,
                str(row["asset_category"]),
                str(row["market"]),
                str(row["currency"]),
            )
            bucket = buckets[key]
            bucket["gross_proceeds"] += decimal_value(
                row.get("gross_proceeds"), default=0
            )
            bucket["fees"] += decimal_value(row.get("disposal_fees"), default=0)
            bucket["net_proceeds"] += decimal_value(
                row.get("net_proceeds"), default=0
            )
            bucket["cost"] += decimal_value(row.get("allocated_cost"), default=0)
            bucket["pnl"] += decimal_value(row.get("realized_pnl"), default=0)
            row_pnl_cny = decimal_value(row.get("realized_pnl_cny"))
            if row_pnl_cny is None:
                bucket["cny_complete"] = False
            else:
                bucket["pnl_cny"] += row_pnl_cny
                bucket["positive_pnl_cny"] += max(row_pnl_cny, decimal_value(0))
                bucket["negative_pnl_cny"] += min(row_pnl_cny, decimal_value(0))
    rows: list[dict[str, object]] = []
    for (method, acat, market, currency), values in sorted(buckets.items()):
        complete = bool(values["cny_complete"])
        pnl_cny = q_internal(values["pnl_cny"]) if complete else None
        positive = q_internal(values["positive_pnl_cny"]) if complete else None
        negative = q_internal(values["negative_pnl_cny"]) if complete else None
        rows.append(
            {
                "method": method,
                "income_category": "property_transfer_income_candidate",
                "asset_category": acat,
                "market": market,
                "currency": currency,
                "gross_proceeds": to_float(q_internal(values["gross_proceeds"])),
                "disposal_fees": to_float(q_internal(values["fees"])),
                "net_proceeds": to_float(q_internal(values["net_proceeds"])),
                "allocated_cost": to_float(q_internal(values["cost"])),
                "realized_pnl": to_float(q_internal(values["pnl"])),
                "realized_pnl_cny": to_float(pnl_cny),
                "positive_disposal_pnl_cny": to_float(positive),
                "negative_disposal_pnl_cny": to_float(negative),
                "cny_conversion_status": "complete" if complete else "incomplete_missing_fx",
                "reference_tax_rate": 0.20,
                "reference_tax_on_positive_annual_net_cny": to_float(
                    q_cny(max(pnl_cny, decimal_value(0)) * decimal_value("0.20"))
                )
                if pnl_cny is not None
                else None,
                "reference_tax_without_loss_offset_cny": to_float(
                    q_cny(positive * decimal_value("0.20"))
                )
                if positive is not None
                else None,
                "loss_offset_treatment": loss_treatment,
                "loss_offset_status": loss_status,
                "method_status": "method_unconfirmed",
            }
        )
    for result in method_results:
        cny_values = [
            decimal_value(row.get("realized_pnl_cny")) for row in result.disposals
        ]
        complete = all(value is not None for value in cny_values)
        valid_values = [v for v in cny_values if v is not None]
        total_pnl_cny = (
            q_internal(sum(valid_values, decimal_value(0))) if complete else None
        )
        positive_pnl_cny = (
            q_internal(
                sum(
                    (max(v, decimal_value(0)) for v in valid_values),
                    decimal_value(0),
                )
            )
            if complete
            else None
        )
        negative_pnl_cny = (
            q_internal(
                sum(
                    (min(v, decimal_value(0)) for v in valid_values),
                    decimal_value(0),
                )
            )
            if complete
            else None
        )
        rows.append(
            {
                "method": result.method,
                "income_category": "property_transfer_income_candidate",
                "asset_category": "ALL_REALIZED_SECURITIES",
                "market": "ALL",
                "currency": "CNY_CONVERTED",
                "gross_proceeds": None,
                "disposal_fees": None,
                "net_proceeds": None,
                "allocated_cost": None,
                "realized_pnl": None,
                "realized_pnl_cny": to_float(total_pnl_cny),
                "positive_disposal_pnl_cny": to_float(positive_pnl_cny),
                "negative_disposal_pnl_cny": to_float(negative_pnl_cny),
                "cny_conversion_status": "complete" if complete else "incomplete_missing_fx",
                "reference_tax_rate": 0.20,
                "reference_tax_on_positive_annual_net_cny": to_float(
                    q_cny(
                        max(total_pnl_cny, decimal_value(0)) * decimal_value("0.20")
                    )
                )
                if total_pnl_cny is not None
                else None,
                "reference_tax_without_loss_offset_cny": to_float(
                    q_cny(positive_pnl_cny * decimal_value("0.20"))
                )
                if positive_pnl_cny is not None
                else None,
                "loss_offset_treatment": loss_treatment,
                "loss_offset_status": loss_status,
                "method_status": "method_unconfirmed",
            }
        )
    return rows


# ---- Main entry point -----------------------------------------------------

def build_cost_basis_report(
    statements: Iterable[StatementResult],
    prior_statements: Iterable[StatementResult] = (),
) -> dict[str, object]:
    """Build the complete cost-basis report for a tax year."""
    statements_list = sorted(
        list(statements), key=lambda item: item.statement_month
    )
    prior_list = sorted(
        list(prior_statements), key=lambda item: item.statement_month
    )
    tax_year = int(statements_list[0].statement_month[:4]) if statements_list and statements_list[0].statement_month[:4].isdigit() else 0
    if prior_list:
        fifo_opening, moving_opening, opening_rows, opening_errors, prior_coverage = (
            _prior_period_opening_lots(prior_list, tax_year=tax_year)
        )
    else:
        fallback_lots, fallback_errors = build_opening_lots(
            statements_list, ()
        )
        fifo_opening = fallback_lots
        moving_opening = fallback_lots
        opening_rows = []
        for method in ("FIFO", "MOVING_AVERAGE"):
            for lot in fallback_lots:
                row = lot.to_dict()
                row["method"] = method
                row["evidence_status"] = "unverified_statement_display_cost"
                opening_rows.append(row)
        if fallback_lots or fallback_errors:
            opening_errors = [
                *fallback_errors,
            ]
            prior_coverage = {
                "status": "missing",
                "actual_months": [],
                "expected_months": [],
            }
        else:
            opening_errors = []
            prior_coverage = {
                "status": "ok",
                "actual_months": [],
                "expected_months": [],
                "monthly_reconciliation": [],
                "monthly_reconciliation_error_count": 0,
                "monthly_reconciliation_status": "not_applicable",
                "note": "No positive opening inventory required prior-period "
                        "cost reconstruction.",
            }
    events = build_cost_basis_events(statements_list)
    fifo = run_fifo(fifo_opening, events)
    moving = run_moving_average(moving_opening, events)
    fifo.errors[:0] = opening_errors
    moving.errors[:0] = opening_errors
    _reconcile(fifo, statements_list)
    _reconcile(moving, statements_list)
    differences = _difference_rows(fifo, moving)
    split_cash_issues = _split_cash_compensation_issues(statements_list)
    errors = sorted(
        set(
            fifo.errors
            + moving.errors
            + split_cash_issues
            + [
                f"difference:{row['source_reference']}"
                for row in differences
                if row.get("validation_status") != "ok"
            ]
        )
    )
    return {
        "opening_lots": opening_rows,
        "prior_period_coverage": prior_coverage,
        "events": [event.__dict__ for event in events],
        "fifo": fifo,
        "moving_average": moving,
        "differences": differences,
        "summary": _summary_rows([fifo, moving]),
        "errors": errors,
        "split_cash_review_issues": split_cash_issues,
        "ready": not errors,
        "method_note": (
            "FIFO and moving-average results are both supplied because the "
            "disposal-matching method has not been confirmed by the competent "
            "tax authority. Buy-side fees are included in acquisition cost; "
            "sell-side fees reduce disposal proceeds."
        ),
    }
