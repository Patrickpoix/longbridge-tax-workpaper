from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .filing_policy import year_end_fx_rate
from .jurisdiction import jurisdiction_for
from .money import decimal_value, q_cny, to_float
from .schema import FieldValue, SectionResult, StatementResult

# Longbridge descriptions have changed spacing and punctuation across statement
# generations.  These expressions accept both ASCII and full-width punctuation
# and common English/Chinese labels while still requiring a per-share amount and
# a held quantity before grossing up embedded withholding.
RMB_PER_SHARE_RE = re.compile(
    r"(?:RMB|CNY|人民币)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:/\s*(?:SH|股)|每\s*股)",
    re.IGNORECASE,
)
HELD_RE = re.compile(
    r"(?:Held|持有|持股)\s*[:：]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.IGNORECASE,
)
EMBEDDED_WITHHOLDING_RE = re.compile(
    r"(?:\(|（)?\s*[-−－]?\s*10\s*%\s*(?:\)|）)?",
    re.IGNORECASE,
)
SYMBOL_RE = re.compile(r"#?([A-Z0-9]+(?:\.[A-Z]+)?)", re.IGNORECASE)


def _value(row: dict[str, FieldValue], key: str):
    return row.get(key, FieldValue.missing()).value


def _cash_amount(row: dict[str, FieldValue]) -> Decimal:
    value = _value(row, "cash_amount") if "cash_amount" in row else _value(row, "amount")
    return decimal_value(value, default=Decimal("0")) or Decimal("0")


def _security_id_from_code(code: str) -> str:
    code = code.strip().upper()
    if code.endswith(".HK") and code[:-3].isdigit():
        return f"HK:{int(code[:-3]):05d}"
    if code.endswith(".US"):
        return f"US:{code[:-3]}"
    return code


def _converted(value: Decimal, currency: str) -> tuple[Decimal | None, float | None]:
    rate = year_end_fx_rate(currency) if currency in {"HKD", "USD"} else None
    return (q_cny(value * decimal_value(rate)) if rate is not None else None, rate)


def build_dividend_tax_basis_rows(statements: Iterable[StatementResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for statement in sorted(statements, key=lambda st: st.statement_month):
        section = statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
        direct_withholding: dict[tuple[str, str, str], Decimal] = defaultdict(lambda: Decimal("0"))
        for withholding_row in section.rows:
            if str(_value(withholding_row, "tax_category") or "") != "withholding_tax":
                continue
            detail = str(_value(withholding_row, "raw_detail") or "")
            symbol_match = SYMBOL_RE.search(detail)
            symbol = symbol_match.group(1).upper() if symbol_match else "UNKNOWN"
            date = str(_value(withholding_row, "date") or "")
            currency = str(_value(withholding_row, "currency") or "")
            direct_withholding[(date, symbol, currency)] += abs(_cash_amount(withholding_row))

        consumed_direct: set[tuple[str, str, str]] = set()
        for index, row in enumerate(section.rows, start=1):
            if str(_value(row, "tax_category") or "") != "dividend_income":
                continue
            detail = unicodedata.normalize("NFKC", str(_value(row, "raw_detail") or ""))
            currency = str(_value(row, "currency") or "")
            amount_decimal = q_cny(_cash_amount(row))
            date = str(_value(row, "date") or "")
            symbol_match = SYMBOL_RE.search(detail)
            security_code = symbol_match.group(1).upper() if symbol_match else "UNKNOWN"
            rmb_match = RMB_PER_SHARE_RE.search(detail)
            held_match = HELD_RE.search(detail)
            embedded = bool(rmb_match and held_match and EMBEDDED_WITHHOLDING_RE.search(detail))

            gross_cny: Decimal | None = None
            embedded_tax_cny: Decimal | None = None
            net_cny: Decimal | None = None
            filing_cny: Decimal | None = None
            fx_rate: float | None = None
            basis = "year_end_fx_cash_dividend"
            if embedded:
                per_share_rmb = decimal_value(rmb_match.group(1), default=Decimal("0")) or Decimal("0")
                held = decimal_value(held_match.group(1).replace(",", ""), default=Decimal("0")) or Decimal("0")
                gross_cny = q_cny(per_share_rmb * held)
                embedded_tax_cny = q_cny(gross_cny * Decimal("0.10"))
                net_cny = q_cny(gross_cny - embedded_tax_cny)
                filing_cny = gross_cny
                basis = "pdf_declared_rmb_gross_with_embedded_10pct_withholding"
            elif currency in {"HKD", "USD"}:
                filing_cny, fx_rate = _converted(amount_decimal, currency)

            withholding_key = (date, security_code, currency)
            direct_original = Decimal("0")
            if withholding_key not in consumed_direct:
                direct_original = q_cny(direct_withholding.get(withholding_key, Decimal("0")))
                consumed_direct.add(withholding_key)
            if direct_original:
                direct_cny, direct_rate = _converted(direct_original, currency)
                fx_rate = fx_rate if fx_rate is not None else direct_rate
            else:
                direct_cny = Decimal("0")

            credit_complete = direct_cny is not None
            candidate_cny = (
                q_cny((embedded_tax_cny or Decimal("0")) + direct_cny)
                if credit_complete
                else None
            )
            china_tax_before = q_cny(filing_cny * Decimal("0.20")) if filing_cny is not None else None
            automatic_credit = Decimal("0")
            tax_after_auto = china_tax_before
            tax_after_candidate = (
                q_cny(max(china_tax_before - candidate_cny, Decimal("0")))
                if china_tax_before is not None and candidate_cny is not None
                else None
            )
            conversion_complete = filing_cny is not None and candidate_cny is not None
            rows.append(
                {
                    "statement_month": statement.statement_month,
                    "date": date,
                    "row_index": index,
                    "security_code": security_code,
                    "currency": currency,
                    "cash_received": to_float(amount_decimal),
                    "year_end_fx_rate": fx_rate,
                    "gross_dividend_cny": to_float(gross_cny),
                    "embedded_withholding_cny": to_float(embedded_tax_cny),
                    "net_declared_cny": to_float(net_cny),
                    "filing_dividend_income_cny": to_float(filing_cny),
                    "direct_withholding_original": to_float(direct_original),
                    "direct_withholding_cny": to_float(direct_cny),
                    "statement_withholding_credit_candidate_cny": to_float(candidate_cny),
                    "china_tax_before_credit_cny": to_float(china_tax_before),
                    "china_tax_after_statement_credit_cny": to_float(tax_after_candidate),
                    "automatic_credit_cny": to_float(automatic_credit),
                    "requested_credit_candidate_cny": to_float(candidate_cny),
                    "china_tax_after_automatic_credit_cny": to_float(tax_after_auto),
                    "china_tax_after_requested_candidate_cny": to_float(tax_after_candidate),
                    "cny_conversion_status": "complete" if conversion_complete else "incomplete_missing_fx",
                    **jurisdiction_for(_security_id_from_code(security_code)),
                    "credit_evidence_status": "monthly_statement_only" if candidate_cny else "no_withholding_shown",
                    "basis": basis,
                    "raw_detail": detail,
                    "source_pdf": Path(statement.source_pdf).name if statement.source_pdf else "",
                }
            )
    return rows


def _sum_complete(rows: list[dict[str, object]], key: str) -> float | None:
    values = [decimal_value(row.get(key)) for row in rows]
    if any(value is None for value in values):
        return None
    return to_float(q_cny(sum((value for value in values if value is not None), Decimal("0"))))


def dividend_basis_totals(statements: Iterable[StatementResult]) -> dict[str, float | None]:
    rows = build_dividend_tax_basis_rows(statements)
    return {
        "dividend_income_cny": _sum_complete(rows, "filing_dividend_income_cny"),
        "embedded_withholding_cny": to_float(q_cny(sum((decimal_value(row.get("embedded_withholding_cny"), default=Decimal("0")) or Decimal("0") for row in rows), Decimal("0")))),
        "direct_withholding_cny": _sum_complete(rows, "direct_withholding_cny"),
        "statement_withholding_credit_candidate_cny": _sum_complete(rows, "statement_withholding_credit_candidate_cny"),
        "china_tax_before_credit_cny": _sum_complete(rows, "china_tax_before_credit_cny"),
        "china_tax_after_statement_credit_cny": _sum_complete(rows, "china_tax_after_statement_credit_cny"),
        "automatic_credit_cny": 0.0,
        "requested_credit_candidate_cny": _sum_complete(rows, "requested_credit_candidate_cny"),
        "china_tax_after_automatic_credit_cny": _sum_complete(rows, "china_tax_after_automatic_credit_cny"),
        "cash_received_hkd": to_float(q_cny(sum((decimal_value(row.get("cash_received"), default=Decimal("0")) or Decimal("0") for row in rows if row.get("currency") == "HKD"), Decimal("0")))),
        "cash_received_usd": to_float(q_cny(sum((decimal_value(row.get("cash_received"), default=Decimal("0")) or Decimal("0") for row in rows if row.get("currency") == "USD"), Decimal("0")))),
    }
