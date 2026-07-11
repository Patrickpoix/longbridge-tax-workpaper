from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .filing_policy import load_tax_policy, load_taxpayer_profile, year_end_fx_rate
from .hashing import sha256_file
from .money import decimal_value, q_cny, q_internal, to_float
from .schema import FieldValue, SectionResult, StatementResult
from .symbol_mapping import resolve_symbol_alias

EPS = 1e-8
OPTION_CONTRACT_RE = re.compile(r"\b([A-Z]{1,6}\d{6}[CP]\d+)\b", re.IGNORECASE)
HK_CODE_RE = re.compile(r"^\s*(\d{3,5})\b")
EXPLICIT_HK_CODE_RE = re.compile(r"#?(\d{3,5})\.HK\b", re.IGNORECASE)
EXPLICIT_US_CODE_RE = re.compile(r"\b([A-Z]{1,6})(?:\.US|\s+US\s+Equity)\b", re.IGNORECASE)
US_TICKER_RE = re.compile(r"^\s*([A-Z]{1,5})\b")
SPLIT_RE = re.compile(r"Stock\s+Split\s+Amount:\s*([\d.]+)\s+for\s+([\d.]+)", re.IGNORECASE)
SECURITY_CODE_RE = re.compile(r"#?(\d{4,5})(?:\.HK)?\b", re.IGNORECASE)
HELD_QTY_RE = re.compile(r"Held\s*[:：]\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE)
SPLIT_CASH_RE = re.compile(r"(?:cash\s+in\s+lieu|fractional\s+share|cash.*fractional|零碎股|碎股|现金替代)", re.IGNORECASE)

# Symbol-name aliases are loaded from an auditable JSON mapping.


def _value(row: dict[str, FieldValue] | None, name: str):
    if not row:
        return None
    value = row.get(name)
    return value.value if value else None


def _norm_text(text: object) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("⻩", "黄").replace("汽⻋", "汽车")
    return re.sub(r"\s+", " ", value).strip()


def _compact_name(text: object) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", _norm_text(text)).lower()


def canonical_security_id(text: object, *, asset_type: str = "stock") -> str:
    normalized = _norm_text(text)
    option_match = OPTION_CONTRACT_RE.search(normalized.upper())
    if option_match:
        return f"OPT:{option_match.group(1).upper()}"

    explicit_hk = EXPLICIT_HK_CODE_RE.search(normalized)
    if explicit_hk:
        return f"HK:{int(explicit_hk.group(1)):05d}"

    explicit_us = EXPLICIT_US_CODE_RE.search(normalized.upper())
    if explicit_us:
        return f"US:{explicit_us.group(1).upper()}"

    hk_match = HK_CODE_RE.search(normalized)
    if hk_match:
        return f"HK:{int(hk_match.group(1)):05d}"

    compact = _compact_name(normalized)
    mapped = resolve_symbol_alias(normalized)
    if mapped:
        return mapped

    # Only accept an already-uppercase standalone first token as a ticker.
    # Uppercasing arbitrary English company names (for example ``Apple Inc``)
    # and treating the first word as a ticker creates silent cross-security
    # contamination in the cost ledger.
    first_token = normalized.split()[0] if normalized.split() else ""
    if re.fullmatch(r"[A-Z]{1,6}", first_token) and first_token not in {"CALL", "PUT"}:
        return f"US:{first_token}"

    prefix = "OPTNAME" if asset_type == "option" else "NAME"
    return f"{prefix}:{compact or 'unknown'}"


def security_market(security_id: str) -> str:
    if security_id.startswith("HK:"):
        return "HK"
    if security_id.startswith("US:") or security_id.startswith("OPT:"):
        return "US"
    return "UNKNOWN"



def _parse_dt(date_text: object, time_text: object, fallback_index: int) -> tuple[str, str, int]:
    date_value = str(date_text or "").replace(".", "-")
    time_value = str(time_text or "") or "23:59:59"
    try:
        datetime.strptime(f"{date_value} {time_value}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        time_value = "23:59:59"
    return date_value, time_value, fallback_index


def _asset_category(asset_type: str, symbol: str, security_id: str) -> str:
    if asset_type == "option" or security_id.startswith("OPT:"):
        return "option"
    if security_id.startswith("HK:") and any(marker in symbol for marker in ("购A", "购B", "沽A", "沽B", "牛", "熊")):
        return "warrant"
    return "stock"


@dataclass
class Lot:
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
    method: str
    disposals: list[dict[str, object]] = field(default_factory=list)
    remaining_lots: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reconciliation: list[dict[str, object]] = field(default_factory=list)


def _find_trade_rows(statement: StatementResult) -> list[CostBasisEvent]:
    events: list[CostBasisEvent] = []
    sequence = 0
    for section_asset_type, section_name in (("stock", "stock_trades"), ("option", "option_trades")):
        section = statement.sections.get(section_name, SectionResult(name=section_name))
        for row_index, row in enumerate(section.rows, start=1):
            sequence += 1
            symbol = _norm_text(_value(row, "symbol"))
            security_id = canonical_security_id(symbol, asset_type=section_asset_type)
            side = str(_value(row, "side") or ("SELL" if "卖" in str(_value(row, "direction") or "") else "BUY"))
            quantity = float(_value(row, "quantity") or 0.0)
            amount = float(_value(row, "amount") or 0.0)
            total_amount = float(_value(row, "total_amount") or 0.0)
            currency = str(_value(row, "currency") or "UNKNOWN")
            order_time = _value(row, "order_time")
            execution_time = _value(row, "execution_time") or order_time
            date_value, time_value, fallback = _parse_dt(_value(row, "trade_date"), execution_time, row_index)
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
                    sequence=int(statement.statement_month) * 10000 + fallback,
                    statement_month=statement.statement_month,
                    source_pdf=Path(statement.source_pdf).name,
                    source_reference=str(_value(row, "order_id") or f"{statement.statement_month}:{section_name}:{row_index}"),
                    security_id=security_id,
                    symbol=symbol,
                    asset_category=_asset_category(section_asset_type, symbol, security_id),
                    currency=currency,
                    quantity=quantity,
                    gross_amount=amount,
                    fees=fees,
                    cash_effect=cash_effect,
                    evidence=json.dumps({key: _value(row, key) for key in ("trade_date", "order_id", "direction", "symbol", "quantity", "price", "amount", "total_amount", "order_time", "execution_time", "market_timezone")}, ensure_ascii=False),
                )
            )
    return events


def _split_events(statements: Iterable[StatementResult]) -> list[CostBasisEvent]:
    events: list[CostBasisEvent] = []
    for statement in statements:
        section = statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
        seen: set[tuple[str, str]] = set()
        for row_index, row in enumerate(section.rows, start=1):
            raw_detail = _norm_text(_value(row, "raw_detail"))
            split_match = SPLIT_RE.search(raw_detail)
            if not split_match:
                continue
            date_value = str(_value(row, "date") or "").replace(".", "-")
            security_id = canonical_security_id(raw_detail, asset_type="stock")
            # The statement reports a stock split as one stock-in row and one
            # stock-out row. They are the two legs of one split event and must
            # not multiply the lot quantity twice.
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
                    sequence=int(statement.statement_month) * 10000 + row_index,
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
            item for item in candidates
            if abs(abs(float(_value(item[1], "quantity_change") or 0.0)) - expected_quantity) <= EPS
        ]
        if exact:
            eligible = exact
    ranked = sorted(eligible, key=lambda item: (abs(item[0] - anchor_index), item[0]))
    selected = ranked[0]
    status = "matched_by_held_and_nearest_row" if expected_quantity is not None and eligible is not candidates else "matched_by_nearest_row"
    return selected, status


def _pop_candidate(
    candidates: list[tuple[int, dict[str, FieldValue]]],
    *,
    anchor_index: int,
    expected_quantity: float | None = None,
) -> tuple[tuple[int, dict[str, FieldValue]] | None, str]:
    selected, status = _nearest_candidate(candidates, anchor_index=anchor_index, expected_quantity=expected_quantity)
    if selected is not None:
        candidates.remove(selected)
    return selected, status


def _auto_ex_events(statements: Iterable[StatementResult]) -> list[CostBasisEvent]:
    """Pair AUTO-EX proceeds, stock-out quantity, and fees deterministically.

    Pairing uses date and security code, then the PDF ``Held`` quantity and the
    nearest statement-row position.  Each stock-out and fee row is consumed at
    most once, so multiple same-day settlements cannot reuse evidence.
    """

    events: list[CostBasisEvent] = []
    for statement in statements:
        flows = statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows")).rows
        groups: dict[tuple[str, str], dict[str, list[tuple[int, dict[str, FieldValue]]]]] = defaultdict(
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
            for event_index, (row_index, proceeds_row) in enumerate(sorted(group["proceeds"]), start=1):
                raw_detail = _norm_text(_value(proceeds_row, "raw_detail"))
                expected_match = HELD_QTY_RE.search(raw_detail)
                expected_quantity = float(expected_match.group(1).replace(",", "")) if expected_match else None

                selected_stock, stock_status = _pop_candidate(
                    stock_out, anchor_index=row_index, expected_quantity=expected_quantity
                )
                quantity = abs(float(_value(selected_stock[1], "quantity_change") or 0.0)) if selected_stock else 0.0
                selected_handling, handling_status = _pop_candidate(handling, anchor_index=row_index)
                selected_other_fee, other_fee_status = _pop_candidate(other_fees, anchor_index=row_index)
                handling_fee = abs(float(_value(selected_handling[1], "cash_amount") or 0.0)) if selected_handling else 0.0
                other_fee = abs(float(_value(selected_other_fee[1], "cash_amount") or 0.0)) if selected_other_fee else 0.0
                proceeds = float(_value(proceeds_row, "cash_amount") or _value(proceeds_row, "amount") or 0.0)
                evidence_parts = [
                    raw_detail,
                    f"stock_out_match={stock_status}",
                    f"handling_match={handling_status}",
                    f"other_fee_match={other_fee_status}",
                ]
                for selected in (selected_stock, selected_handling, selected_other_fee):
                    if selected:
                        evidence_parts.append(str(_value(selected[1], "raw_detail") or ""))

                security_id = f"HK:{int(code):05d}"
                events.append(
                    CostBasisEvent(
                        event_type="SELL",
                        event_date=date_text.replace(".", "-"),
                        event_time="23:59:59",
                        sequence=int(statement.statement_month) * 10000 + 9000 + row_index,
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


def _split_cash_compensation_issues(statements: Iterable[StatementResult]) -> list[str]:
    issues: list[str] = []
    for statement in statements:
        section = statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
        for row_index, row in enumerate(section.rows, start=1):
            detail = _norm_text(_value(row, "raw_detail"))
            if SPLIT_CASH_RE.search(detail) and ("split" in detail.lower() or "拆股" in detail or "合股" in detail):
                issues.append(
                    f"{statement.statement_month}:fund_flow:{row_index}: split cash/fractional-share compensation requires manual basis allocation"
                )
    return issues

def _month_sequence(start_month: str, end_month: str) -> list[str]:
    start_year, start_num = int(start_month[:4]), int(start_month[4:])
    end_year, end_num = int(end_month[:4]), int(end_month[4:])
    output: list[str] = []
    year, month = start_year, start_num
    while (year, month) <= (end_year, end_num):
        output.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return output


def _lot_from_row(row: dict[str, object], *, source_type: str | None = None) -> Lot:
    return Lot(
        security_id=str(row.get("security_id") or "UNKNOWN"),
        symbol=str(row.get("symbol") or row.get("security_id") or "UNKNOWN"),
        asset_category=str(row.get("asset_category") or "stock"),
        currency=str(row.get("currency") or "UNKNOWN"),
        acquired_date=str(row.get("acquired_date") or "MULTIPLE"),
        acquired_time=str(row.get("acquired_time") or "00:00:00"),
        quantity=float(row.get("quantity") or 0.0),
        total_cost=float(row.get("total_cost") or 0.0),
        source_type=source_type or str(row.get("source_type") or "prior_period_ledger"),
        source_reference=str(row.get("source_reference") or "multiple"),
        source_pdf=str(row.get("source_pdf") or "multiple"),
        evidence=str(row.get("evidence") or ""),
    )


def _prior_period_opening_lots(
    prior_statements: Iterable[StatementResult],
    *,
    tax_year: int,
) -> tuple[list[Lot], list[Lot], list[dict[str, object]], list[str], dict[str, object]]:
    """Build opening inventory from the complete pre-tax-year trade ledger.

    Broker-displayed holding cost can become negative after partial disposals and
    is not used.  The account-opening statements are replayed from an empty
    inventory so every remaining unit inherits original trade cost and buy fees.
    """

    ordered = sorted(list(prior_statements), key=lambda item: item.statement_month)
    errors: list[str] = []
    if not ordered:
        return [], [], [], ["prior-period statements are required for traceable opening cost basis"], {
            "status": "missing",
            "actual_months": [],
            "expected_months": [],
        }

    profile = load_taxpayer_profile()
    opening_month = str(profile.get("account_opening_month") or ordered[0].statement_month)
    expected_months = _month_sequence(opening_month, f"{tax_year - 1}12")
    actual_months = [statement.statement_month for statement in ordered]
    if actual_months != expected_months:
        errors.append(f"prior-period coverage mismatch: actual={actual_months}; expected={expected_months}")

    prior_events = build_cost_basis_events(ordered)
    fifo_prior = _run_fifo([], prior_events)
    moving_prior = _run_moving_average([], prior_events)
    _reconcile(fifo_prior, ordered)
    _reconcile(moving_prior, ordered)
    errors.extend(f"prior_fifo:{item}" for item in fifo_prior.errors)
    errors.extend(f"prior_moving_average:{item}" for item in moving_prior.errors)

    # Prove continuity month by month rather than validating only the final
    # pre-tax-year statement. Quantity is method-independent, so FIFO is used
    # as the rolling inventory ledger and each month is compared with that
    # month's statement ending position.
    monthly_reconciliation: list[dict[str, object]] = []
    rolling_lots: list[Lot] = []
    monthly_reconciliation_errors: list[str] = []
    for statement in ordered:
        month_result = _run_fifo(rolling_lots, build_cost_basis_events([statement]))
        _reconcile(month_result, [statement])
        for row in month_result.reconciliation:
            monthly_row = dict(row)
            monthly_row["statement_month"] = statement.statement_month
            monthly_row["reconciliation_scope"] = "prior_period_month_end"
            monthly_reconciliation.append(monthly_row)
        monthly_reconciliation_errors.extend(
            f"{statement.statement_month}:{item}" for item in month_result.errors
        )
        rolling_lots = [
            _lot_from_row(row, source_type=str(row.get("source_type") or "prior_period_fifo_trade_ledger"))
            for row in month_result.remaining_lots
        ]
    errors.extend(f"prior_monthly_reconciliation:{item}" for item in monthly_reconciliation_errors)

    fifo_lots = [_lot_from_row(row, source_type="prior_period_fifo_trade_ledger") for row in fifo_prior.remaining_lots]
    moving_lots = [_lot_from_row(row, source_type="prior_period_moving_average_ledger") for row in moving_prior.remaining_lots]
    opening_rows: list[dict[str, object]] = []
    for method, lots in (("FIFO", fifo_lots), ("MOVING_AVERAGE", moving_lots)):
        for lot in lots:
            row = lot.to_dict()
            row["method"] = method
            row["evidence_status"] = "verified_from_complete_prior_trade_ledger"
            opening_rows.append(row)
            if lot.quantity <= EPS:
                errors.append(f"{method}:{lot.security_id} opening quantity must be positive")
            if lot.total_cost <= EPS:
                errors.append(f"{method}:{lot.security_id} opening total cost must be positive, got {lot.total_cost}")

    coverage = {
        "status": "ok" if actual_months == expected_months else "error",
        "account_opening_month": opening_month,
        "actual_months": actual_months,
        "expected_months": expected_months,
        "event_count": len(prior_events),
        "buy_count": sum(event.event_type == "BUY" for event in prior_events),
        "sell_count": sum(event.event_type == "SELL" for event in prior_events),
        "split_count": sum(event.event_type == "SPLIT" for event in prior_events),
        "last_statement": ordered[-1].statement_month,
        "monthly_reconciliation": monthly_reconciliation,
        "monthly_reconciliation_error_count": sum(
            row.get("validation_status") != "ok" for row in monthly_reconciliation
        ),
        "monthly_reconciliation_status": (
            "ok"
            if monthly_reconciliation
            and all(row.get("validation_status") == "ok" for row in monthly_reconciliation)
            else "error"
        ),
    }
    return fifo_lots, moving_lots, opening_rows, sorted(set(errors)), coverage


def build_opening_lots(statements: Iterable[StatementResult], prior_statements: Iterable[StatementResult] = ()) -> tuple[list[Lot], list[str]]:
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
        symbol = _norm_text(_value(row, "name"))
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
            asset_category=_asset_category("stock", symbol, security_id),
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


def build_cost_basis_events(statements: Iterable[StatementResult]) -> list[CostBasisEvent]:
    events: list[CostBasisEvent] = []
    for statement in statements:
        events.extend(_find_trade_rows(statement))
    events.extend(_split_events(statements))
    events.extend(_auto_ex_events(statements))
    return sorted(events, key=lambda item: item.sort_key)


def _disposal_base(event: CostBasisEvent, method: str, allocated_cost: float, match_detail: object, status: str, note: str) -> dict[str, object]:
    policy = load_tax_policy()
    fx = year_end_fx_rate(event.currency, policy) if event.currency in {"HKD", "USD"} else None
    pnl_decimal = q_internal(decimal_value(event.cash_effect, default=0) - decimal_value(allocated_cost, default=0))
    pnl = to_float(pnl_decimal)
    pnl_cny_decimal = q_internal(pnl_decimal * decimal_value(fx)) if fx is not None else None
    tax_decimal = q_cny(max(pnl_cny_decimal, decimal_value(0)) * decimal_value("0.20")) if pnl_cny_decimal is not None else None
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
        "market": security_market(event.security_id),
        "currency": event.currency,
        "quantity": round(event.quantity, 8),
        "gross_proceeds": round(event.gross_amount, 8),
        "disposal_fees": round(event.fees, 8),
        "net_proceeds": round(event.cash_effect, 8),
        "allocated_cost": round(allocated_cost, 8),
        "realized_pnl": pnl,
        "year_end_cny_rate": fx,
        "realized_pnl_cny": to_float(pnl_cny_decimal),
        "cny_conversion_status": "complete" if fx is not None else "incomplete_missing_fx",
        "reference_tax_rate": 0.20,
        "reference_tax_on_positive_pnl_cny": to_float(tax_decimal),
        "non_deductible_fee": round(event.non_deductible_fee, 8),
        "match_detail_json": json.dumps(match_detail, ensure_ascii=False),
        "validation_status": status,
        "validation_note": note,
        "source_pdf": event.source_pdf,
        "evidence": event.evidence,
    }


def _run_fifo(opening_lots: list[Lot], events: list[CostBasisEvent]) -> MethodResult:
    result = MethodResult(method="FIFO")
    states: dict[str, deque[Lot]] = defaultdict(deque)
    for lot in opening_lots:
        states[lot.security_id].append(Lot(**lot.__dict__))

    for event in events:
        queue = states[event.security_id]
        if event.event_type == "SPLIT":
            ratio = float(event.split_ratio or 0.0)
            if ratio <= 0:
                result.errors.append(f"{event.source_reference}: invalid split ratio")
                continue
            for lot in queue:
                lot.quantity *= ratio
                lot.evidence += f"; split {ratio}:1 on {event.event_date} ({event.source_reference})"
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

        remaining = event.quantity
        allocated_cost = 0.0
        matches: list[dict[str, object]] = []
        while remaining > EPS and queue:
            lot = queue[0]
            if lot.quantity <= EPS:
                queue.popleft()
                continue
            take = min(remaining, lot.quantity)
            unit_cost = lot.total_cost / lot.quantity
            cost = unit_cost * take
            matches.append(
                {
                    "source_reference": lot.source_reference,
                    "acquired_date": lot.acquired_date,
                    "quantity": round(take, 8),
                    "unit_cost": round(unit_cost, 8),
                    "allocated_cost": round(cost, 8),
                    "source_type": lot.source_type,
                    "source_pdf": lot.source_pdf,
                }
            )
            lot.quantity -= take
            lot.total_cost -= cost
            allocated_cost += cost
            remaining -= take
            if lot.quantity <= EPS:
                queue.popleft()
        status = "ok"
        note = "all disposal quantity matched to traceable FIFO lots"
        if remaining > EPS:
            status = "error"
            note = f"unmatched disposal quantity {remaining:.8f}"
            result.errors.append(f"{event.source_reference}: {note}")
        result.disposals.append(_disposal_base(event, "FIFO", allocated_cost, matches, status, note))

    for security_id, queue in states.items():
        for lot in queue:
            if lot.quantity > EPS:
                row = lot.to_dict()
                row["method"] = "FIFO"
                result.remaining_lots.append(row)
    return result


def _run_moving_average(opening_lots: list[Lot], events: list[CostBasisEvent]) -> MethodResult:
    result = MethodResult(method="MOVING_AVERAGE")
    state: dict[str, dict[str, object]] = {}
    for lot in opening_lots:
        current = state.setdefault(
            lot.security_id,
            {"quantity": 0.0, "total_cost": 0.0, "symbol": lot.symbol, "asset_category": lot.asset_category, "currency": lot.currency, "sources": []},
        )
        current["quantity"] = float(current["quantity"]) + lot.quantity
        current["total_cost"] = float(current["total_cost"]) + lot.total_cost
        current["sources"].append(lot.to_dict())

    for event in events:
        current = state.setdefault(
            event.security_id,
            {"quantity": 0.0, "total_cost": 0.0, "symbol": event.symbol, "asset_category": event.asset_category, "currency": event.currency, "sources": []},
        )
        if event.event_type == "SPLIT":
            ratio = float(event.split_ratio or 0.0)
            if ratio <= 0:
                result.errors.append(f"{event.source_reference}: invalid split ratio")
                continue
            current["quantity"] = float(current["quantity"]) * ratio
            current["sources"].append({"event": "split", "ratio": ratio, "date": event.event_date, "source_reference": event.source_reference})
            continue
        if event.event_type == "BUY":
            current["quantity"] = float(current["quantity"]) + event.quantity
            current["total_cost"] = float(current["total_cost"]) + abs(event.cash_effect)
            current["symbol"] = event.symbol
            current["asset_category"] = event.asset_category
            current["currency"] = event.currency
            current["sources"].append({"event": "buy", "source_reference": event.source_reference, "quantity": event.quantity, "cost": abs(event.cash_effect)})
            continue

        qty_before = float(current["quantity"])
        cost_before = float(current["total_cost"])
        status = "ok"
        note = "disposal quantity matched to moving-average inventory"
        if event.quantity > qty_before + EPS or qty_before <= EPS:
            allocated_cost = 0.0
            status = "error"
            note = f"unmatched disposal quantity {max(event.quantity - max(qty_before, 0.0), 0.0):.8f}"
            result.errors.append(f"{event.source_reference}: {note}")
        else:
            average = cost_before / qty_before
            allocated_cost = average * event.quantity
            current["quantity"] = qty_before - event.quantity
            current["total_cost"] = cost_before - allocated_cost
        match = {
            "quantity_before": round(qty_before, 8),
            "total_cost_before": round(cost_before, 8),
            "average_unit_cost": round(cost_before / qty_before, 8) if qty_before > EPS else None,
            "allocated_cost": round(allocated_cost, 8),
        }
        result.disposals.append(_disposal_base(event, "MOVING_AVERAGE", allocated_cost, match, status, note))

    for security_id, current in state.items():
        quantity = float(current["quantity"])
        if quantity <= EPS:
            continue
        total_cost = float(current["total_cost"])
        result.remaining_lots.append(
            {
                "method": "MOVING_AVERAGE",
                "security_id": security_id,
                "symbol": current["symbol"],
                "asset_category": current["asset_category"],
                "currency": current["currency"],
                "acquired_date": "MULTIPLE",
                "acquired_time": None,
                "quantity": round(quantity, 8),
                "total_cost": round(total_cost, 8),
                "unit_cost": round(total_cost / quantity, 8),
                "source_type": "moving_average_pool",
                "source_reference": "multiple",
                "source_pdf": "multiple",
                "evidence": json.dumps(current["sources"], ensure_ascii=False),
            }
        )
    return result


def _ending_holding_quantities(statements: Iterable[StatementResult]) -> dict[str, dict[str, object]]:
    ordered = sorted(list(statements), key=lambda item: item.statement_month)
    if not ordered:
        return {}
    last = ordered[-1]
    output: dict[str, dict[str, object]] = {}
    holdings = last.sections.get("holdings", SectionResult(name="holdings"))
    for row in holdings.rows:
        symbol = _norm_text(_value(row, "name"))
        asset_type = "option" if str(_value(row, "asset_type") or "") == "期权" else "stock"
        security_id = canonical_security_id(symbol, asset_type=asset_type)
        output[security_id] = {
            "statement_ending_quantity": float(_value(row, "ending_position") or 0.0),
            "statement_symbol": symbol,
            "source_pdf": Path(last.source_pdf).name,
        }
    return output


def _reconcile(result: MethodResult, statements: Iterable[StatementResult]) -> None:
    expected = _ending_holding_quantities(statements)
    computed: dict[str, float] = defaultdict(float)
    for lot in result.remaining_lots:
        computed[str(lot["security_id"])] += float(lot["quantity"])
    security_ids = sorted(set(expected) | set(computed))
    for security_id in security_ids:
        expected_qty = float(expected.get(security_id, {}).get("statement_ending_quantity", 0.0))
        computed_qty = float(computed.get(security_id, 0.0))
        difference = round(computed_qty - expected_qty, 8)
        status = "ok" if abs(difference) <= EPS else "error"
        if status == "error":
            result.errors.append(f"{security_id}: year-end quantity difference {difference}")
        result.reconciliation.append(
            {
                "method": result.method,
                "security_id": security_id,
                "symbol": expected.get(security_id, {}).get("statement_symbol") or next((str(row["symbol"]) for row in result.remaining_lots if row["security_id"] == security_id), security_id),
                "computed_ending_quantity": round(computed_qty, 8),
                "statement_ending_quantity": round(expected_qty, 8),
                "difference": difference,
                "validation_status": status,
                "source_pdf": expected.get(security_id, {}).get("source_pdf", ""),
            }
        )


def _difference_rows(fifo: MethodResult, moving: MethodResult) -> list[dict[str, object]]:
    moving_map = {str(row["source_reference"]): row for row in moving.disposals}
    output: list[dict[str, object]] = []
    for fifo_row in fifo.disposals:
        key = str(fifo_row["source_reference"])
        avg_row = moving_map.get(key)
        if avg_row is None:
            output.append({"source_reference": key, "validation_status": "error", "note": "missing moving-average disposal row"})
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
                "pnl_difference": to_float(q_internal(decimal_value(fifo_row["realized_pnl"], default=0) - decimal_value(avg_row["realized_pnl"], default=0))),
                "fifo_realized_pnl_cny": fifo_row["realized_pnl_cny"],
                "moving_average_realized_pnl_cny": avg_row["realized_pnl_cny"],
                "pnl_difference_cny": to_float(q_internal(fifo_cny - moving_cny)) if fifo_cny is not None and moving_cny is not None else None,
                "cny_conversion_status": "complete" if fifo_cny is not None and moving_cny is not None else "incomplete_missing_fx",
                "validation_status": "ok" if fifo_row["validation_status"] == "ok" and avg_row["validation_status"] == "ok" else "error",
                "note": "Both methods use acquisition costs including buy fees and net disposal proceeds after sell fees.",
            }
        )
    return output


def _summary_rows(method_results: Iterable[MethodResult]) -> list[dict[str, object]]:
    policy = load_tax_policy()
    loss_rule = policy.get("property_transfer_loss_offset", {})
    loss_status = str(loss_rule.get("status") or "unconfirmed")
    loss_treatment = str(loss_rule.get("treatment") or "unconfirmed")
    buckets: dict[tuple[str, str, str, str], dict[str, object]] = defaultdict(lambda: {
        "gross_proceeds": decimal_value(0), "fees": decimal_value(0), "net_proceeds": decimal_value(0), "cost": decimal_value(0),
        "pnl": decimal_value(0), "pnl_cny": decimal_value(0), "positive_pnl_cny": decimal_value(0), "negative_pnl_cny": decimal_value(0),
        "cny_complete": True,
    })
    for result in method_results:
        for row in result.disposals:
            key = (result.method, str(row["asset_category"]), str(row["market"]), str(row["currency"]))
            bucket = buckets[key]
            bucket["gross_proceeds"] += decimal_value(row.get("gross_proceeds"), default=0)
            bucket["fees"] += decimal_value(row.get("disposal_fees"), default=0)
            bucket["net_proceeds"] += decimal_value(row.get("net_proceeds"), default=0)
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
    for (method, asset_category, market, currency), values in sorted(buckets.items()):
        complete = bool(values["cny_complete"])
        pnl_cny = q_internal(values["pnl_cny"]) if complete else None
        positive = q_internal(values["positive_pnl_cny"]) if complete else None
        negative = q_internal(values["negative_pnl_cny"]) if complete else None
        rows.append(
            {
                "method": method,
                "income_category": "property_transfer_income_candidate",
                "asset_category": asset_category,
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
                "reference_tax_on_positive_annual_net_cny": to_float(q_cny(max(pnl_cny, decimal_value(0)) * decimal_value("0.20"))) if pnl_cny is not None else None,
                "reference_tax_without_loss_offset_cny": to_float(q_cny(positive * decimal_value("0.20"))) if positive is not None else None,
                "loss_offset_treatment": loss_treatment,
                "loss_offset_status": loss_status,
                "method_status": "method_unconfirmed",
            }
        )
    for result in method_results:
        cny_values = [decimal_value(row.get("realized_pnl_cny")) for row in result.disposals]
        complete = all(value is not None for value in cny_values)
        valid_values = [value for value in cny_values if value is not None]
        total_pnl_cny = q_internal(sum(valid_values, decimal_value(0))) if complete else None
        positive_pnl_cny = q_internal(sum((max(value, decimal_value(0)) for value in valid_values), decimal_value(0))) if complete else None
        negative_pnl_cny = q_internal(sum((min(value, decimal_value(0)) for value in valid_values), decimal_value(0))) if complete else None
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
                "reference_tax_on_positive_annual_net_cny": to_float(q_cny(max(total_pnl_cny, decimal_value(0)) * decimal_value("0.20"))) if total_pnl_cny is not None else None,
                "reference_tax_without_loss_offset_cny": to_float(q_cny(positive_pnl_cny * decimal_value("0.20"))) if positive_pnl_cny is not None else None,
                "loss_offset_treatment": loss_treatment,
                "loss_offset_status": loss_status,
                "method_status": "method_unconfirmed",
            }
        )
    return rows


def build_cost_basis_report(
    statements: Iterable[StatementResult],
    prior_statements: Iterable[StatementResult] = (),
) -> dict[str, object]:
    statements_list = sorted(list(statements), key=lambda item: item.statement_month)
    prior_list = sorted(list(prior_statements), key=lambda item: item.statement_month)
    tax_year = int(statements_list[0].statement_month[:4]) if statements_list and statements_list[0].statement_month[:4].isdigit() else 0
    if prior_list:
        fifo_opening, moving_opening, opening_rows, opening_errors, prior_coverage = _prior_period_opening_lots(prior_list, tax_year=tax_year)
    else:
        fallback_lots, fallback_errors = build_opening_lots(statements_list, ())
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
                "complete prior-period trade history was not supplied; statement display costs are not filing-grade evidence",
            ]
            prior_coverage = {"status": "missing", "actual_months": [], "expected_months": []}
        else:
            opening_errors = []
            prior_coverage = {
                "status": "ok",
                "actual_months": [],
                "expected_months": [],
                "monthly_reconciliation": [],
                "monthly_reconciliation_error_count": 0,
                "monthly_reconciliation_status": "not_applicable",
                "note": "No positive opening inventory required prior-period cost reconstruction.",
            }
    events = build_cost_basis_events(statements_list)
    fifo = _run_fifo(fifo_opening, events)
    moving = _run_moving_average(moving_opening, events)
    fifo.errors[:0] = opening_errors
    moving.errors[:0] = opening_errors
    _reconcile(fifo, statements_list)
    _reconcile(moving, statements_list)
    differences = _difference_rows(fifo, moving)
    split_cash_issues = _split_cash_compensation_issues(statements_list)
    errors = sorted(set(
        fifo.errors
        + moving.errors
        + split_cash_issues
        + [f"difference:{row['source_reference']}" for row in differences if row.get("validation_status") != "ok"]
    ))
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
            "FIFO and moving-average results are both supplied because the disposal-matching method has not been confirmed by the competent tax authority. "
            "Buy-side fees are included in acquisition cost; sell-side fees reduce disposal proceeds."
        ),
    }
