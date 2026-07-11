from __future__ import annotations

import re
import unicodedata

from ...ingest import IngestedDocument
from ...normalize import normalize_text, parse_amount
from ...schema import FieldValue, SectionResult

TRADE_RE = re.compile(
    r"(\d{4}\.\d{2}\.\d{2})\s+(\d{4}\.\d{2}\.\d{2})\s+(OS\d+)\s+(卖.|买.)\s+(.+?)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+([\d,]+\.?\d*)\s+(-?[\d,]+\.?\d*)"
)
TRAILING_EXPIRY_RE = re.compile(r"\d{6}$")
CONTROL_RE = re.compile(r"[\x00-\x1f]")

STOCK_FEE_LABELS = [
    ("佣金", "commission"),
    ("印花税", "stamp_duty"),
    ("平台费", "platform_fee"),
    ("交收费", "settlement_fee"),
    ("交易征费", "transaction_levy"),
    ("交易费", "transaction_fee"),
    ("会财局交易征费", "accounting_levy"),
    ("证券交易委员会费", "sec_fee"),
    ("交易活动收费", "trading_activity_fee"),
    ("综合审计跟踪费用", "audit_fee"),
    ("其他交易费用", "other_fees"),
]
OPTION_FEE_LABELS = [
    ("佣金", "commission"),
    ("综合审计跟踪费用", "audit_fee"),
    ("平台费", "platform_fee"),
    ("期权清算费", "clearing_fee"),
    ("期权监管费", "regulatory_fee"),
    ("期权交收费", "settlement_fee"),
    ("证券交易委员会费", "sec_fee"),
    ("交易活动收费", "activity_fee"),
    ("其他交易费用", "other_fees"),
]
ALL_FEE_LABELS = sorted({label for label, _ in STOCK_FEE_LABELS + OPTION_FEE_LABELS})
OPTION_SIGNATURE_LABELS = {"期权清算费", "期权监管费", "期权交收费"}
HKD_SIGNATURE_LABELS = {"印花税", "交易征费", "交易费", "会财局交易征费"}
USD_SIGNATURE_LABELS = {"证券交易委员会费", "交易活动收费", "综合审计跟踪费用"}
OPTION_SYMBOL_RE = re.compile(r"[CP]\d{5,}", re.IGNORECASE)
HK_SYMBOL_RE = re.compile(r"^\d{4,5}\s")
USD_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}\b")
US_MARKET_TZ_RE = re.compile(r"\b(?:EST|EDT)\b")
HK_MARKET_TZ_RE = re.compile(r"\bHKT\b")
TIME_PAIR_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2})\s+(HKT|EST|EDT)\s+"
    r"(\d{2}:\d{2}:\d{2})\s+(HKT|EST|EDT)",
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("−", "-").replace("－", "-")
    return CONTROL_RE.sub(" ", normalized)


def _parse_number(token: str) -> float:
    return parse_amount(token)


def _clean_total(raw_total: str) -> float:
    compact = raw_total.replace(",", "")
    negative = compact.startswith("-")
    body = compact.lstrip("-")
    if len(body) > 6 and TRAILING_EXPIRY_RE.search(body):
        body = body[:-6]
    if negative:
        body = f"-{body}"
    return _parse_number(body)


def _extract_fees(fee_text: str) -> dict[str, float]:
    fees: dict[str, float] = {}
    for fee_label in ALL_FEE_LABELS:
        paired_match = re.search(fee_label + r"\s+(\d+\.\d{2})\s?\.?(\d+\.\d{2})(?![.\d])", fee_text)
        if paired_match:
            fees[fee_label] = float(paired_match.group(2))
            continue
        single_match = re.search(fee_label + r"\s+([\d.]+)", fee_text)
        if single_match:
            fees[fee_label] = float(single_match.group(1).rstrip("."))
    return fees


def _extract_trade_times(fee_text: str) -> tuple[str | None, str | None, str | None]:
    """Return order time, earliest execution time and market timezone.

    Longbridge lists trades by statement layout rather than guaranteed execution
    chronology.  Same-day lot matching therefore must use the printed timestamps
    instead of row order.  Multi-fill orders can have multiple timestamp rows;
    the earliest execution timestamp is sufficient to sequence the order against
    other orders in the same market day.
    """

    matches = TIME_PAIR_RE.findall(fee_text)
    if not matches:
        return None, None, None
    order_times = [item[0] for item in matches]
    execution_times = [item[2] for item in matches]
    timezones = [item[3].upper() for item in matches]
    return min(order_times), min(execution_times), timezones[0]


def _infer_currency(symbol: str, fees: dict[str, float], fee_text: str, *, is_option: bool) -> tuple[str | None, str, float]:
    if is_option:
        return "USD", "option_fee_signature", 0.98
    if any(label in fees for label in HKD_SIGNATURE_LABELS):
        return "HKD", "hong_kong_fee_signature", 0.96
    if any(label in fees for label in USD_SIGNATURE_LABELS):
        return "USD", "us_fee_signature", 0.94

    normalized_fee_text = normalize_text(fee_text).upper()
    if US_MARKET_TZ_RE.search(normalized_fee_text):
        return "USD", "us_market_timezone", 0.9
    if HK_MARKET_TZ_RE.search(normalized_fee_text):
        return "HKD", "hong_kong_market_timezone", 0.9

    normalized_symbol = normalize_text(symbol).upper()
    if HK_SYMBOL_RE.search(normalized_symbol) or ".HK" in normalized_symbol:
        return "HKD", "symbol_heuristic_hk", 0.86
    if USD_SYMBOL_RE.search(normalized_symbol):
        return "USD", "symbol_heuristic_us", 0.84
    return None, "currency_not_found_in_trade_context", 0.0


def _native_text_value(value, *, raw_text: str | None = None, confidence: float = 0.9, warnings: list[str] | None = None) -> FieldValue:
    return FieldValue.native(value, raw_text=raw_text if raw_text is not None else str(value), confidence=confidence, warnings=warnings)


def _derived_value(value, *, raw_text: str | None = None, confidence: float = 0.85, warnings: list[str] | None = None) -> FieldValue:
    return FieldValue.derived(value, raw_text=raw_text, confidence=confidence, warnings=warnings)


def _trade_row(
    *,
    trade_date: str,
    order_date: str,
    order_id: str,
    direction: str,
    symbol: str,
    quantity: float,
    price: float,
    amount: float,
    total_amount: float,
    currency: str | None,
    currency_reason: str,
    currency_confidence: float,
    currency_raw_text: str,
    order_time: str | None,
    execution_time: str | None,
    market_timezone: str | None,
    fees: dict[str, float],
    fee_labels: list[tuple[str, str]],
) -> dict[str, FieldValue]:
    row = {
        "trade_date": _native_text_value(trade_date, confidence=0.95),
        "order_date": _native_text_value(order_date, confidence=0.95),
        "order_id": _native_text_value(order_id, confidence=0.95),
        "order_time": (
            _native_text_value(order_time, raw_text=order_time, confidence=0.93)
            if order_time
            else FieldValue.missing(warnings=["order_time_not_found"])
        ),
        "execution_time": (
            _native_text_value(execution_time, raw_text=execution_time, confidence=0.93)
            if execution_time
            else FieldValue.missing(warnings=["execution_time_not_found"])
        ),
        "market_timezone": (
            _derived_value(market_timezone, raw_text=market_timezone, confidence=0.95)
            if market_timezone
            else FieldValue.missing(warnings=["market_timezone_not_found"])
        ),
        "direction": _native_text_value(direction, confidence=0.95),
        "side": _derived_value("SELL" if "卖" in direction else "BUY" if "买" in direction else "UNKNOWN", raw_text=direction, confidence=0.96),
        "symbol": _native_text_value(symbol, confidence=0.9),
        "quantity": _native_text_value(quantity, raw_text=str(quantity), confidence=0.9),
        "price": _native_text_value(price, raw_text=str(price), confidence=0.9),
        "amount": _native_text_value(amount, raw_text=str(amount), confidence=0.9),
        "total_amount": _native_text_value(total_amount, raw_text=str(total_amount), confidence=0.9),
        "currency": (
            _derived_value(currency, raw_text=currency_raw_text, confidence=currency_confidence, warnings=[currency_reason])
            if currency is not None
            else FieldValue.missing(raw_text=currency_raw_text, warnings=[currency_reason])
        ),
    }
    for fee_label, field_name in fee_labels:
        fee_value = fees.get(fee_label, 0.0)
        row[field_name] = _native_text_value(fee_value, raw_text=str(fee_value), confidence=0.88)
    return row


def extract_trade_sections(document: IngestedDocument) -> tuple[SectionResult, SectionResult]:
    all_text = "\n".join(_clean_text(page.text) for page in document.pages)
    matches = list(TRADE_RE.finditer(all_text))
    source_order_ids = sorted(set(re.findall(r"\bOS\d+\b", all_text)))
    parsed_order_ids: list[str] = []
    stock_rows: list[dict[str, FieldValue]] = []
    option_rows: list[dict[str, FieldValue]] = []

    for index, match in enumerate(matches):
        trade_date, order_date, order_id, direction, symbol, qty_s, price_s, amount_s, total_s = match.groups()
        parsed_order_ids.append(order_id)
        fee_end = matches[index + 1].start() if index + 1 < len(matches) else min(match.end() + 400, len(all_text))
        fee_text = all_text[match.end() : fee_end]
        cut = fee_text.find("01.除非")
        if cut < 0:
            cut = fee_text.find("01. 除")
        if cut > 10:
            fee_text = fee_text[:cut]

        fees = _extract_fees(fee_text)
        order_time, execution_time, market_timezone = _extract_trade_times(fee_text)
        is_option = any(label in fees for label in OPTION_SIGNATURE_LABELS) or bool(OPTION_SYMBOL_RE.search(symbol))
        currency, currency_reason, currency_confidence = _infer_currency(symbol, fees, fee_text, is_option=is_option)
        row = _trade_row(
            trade_date=trade_date,
            order_date=order_date,
            order_id=order_id,
            direction=direction,
            symbol=symbol.strip(),
            quantity=_parse_number(qty_s),
            price=_parse_number(price_s),
            amount=_parse_number(amount_s),
            total_amount=_clean_total(total_s),
            currency=currency,
            currency_reason=currency_reason,
            currency_confidence=currency_confidence,
            currency_raw_text=fee_text.strip() or symbol.strip(),
            order_time=order_time,
            execution_time=execution_time,
            market_timezone=market_timezone,
            fees=fees,
            fee_labels=OPTION_FEE_LABELS if is_option else STOCK_FEE_LABELS,
        )
        if is_option:
            option_rows.append(row)
        else:
            stock_rows.append(row)

    unmatched_order_ids = sorted(set(source_order_ids) - set(parsed_order_ids))
    stock_section = SectionResult(name="stock_trades", rows=stock_rows)
    stock_section.fields["row_count"] = FieldValue.derived(len(stock_rows), confidence=0.95)
    stock_section.fields["all_source_order_id_count"] = FieldValue.derived(len(source_order_ids), confidence=0.95)
    stock_section.fields["all_parsed_order_id_count"] = FieldValue.derived(len(parsed_order_ids), confidence=0.95)
    stock_section.fields["unmatched_order_ids"] = FieldValue.derived(unmatched_order_ids, confidence=0.95)
    option_section = SectionResult(name="option_trades", rows=option_rows)
    option_section.fields["row_count"] = FieldValue.derived(len(option_rows), confidence=0.95)
    option_section.fields["all_source_order_id_count"] = FieldValue.derived(len(source_order_ids), confidence=0.95)
    option_section.fields["all_parsed_order_id_count"] = FieldValue.derived(len(parsed_order_ids), confidence=0.95)
    option_section.fields["unmatched_order_ids"] = FieldValue.derived(unmatched_order_ids, confidence=0.95)
    return stock_section, option_section
