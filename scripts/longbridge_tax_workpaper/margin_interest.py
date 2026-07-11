from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

from .schema import FieldValue, SectionResult, StatementResult

HKD_LABEL = "港元"
USD_LABEL = "美元"
SUMMARY_HKD_LABEL = "汇总(HKD)"
MARGIN_INTEREST_TAX_BASIS_NOTE = (
    "融资利息应计口径底稿使用每月 PDF 首页资金详情："
    "HKD 应计利息原额 + USD 应计利息 × 同月 PDF 参考汇率。"
    "该口径仅用于与券商账单核对，不代表税务上已确认可扣除。"
)

MARGIN_INTEREST_ACTUAL_PAYMENT_NOTE = (
    "融资利息实际扣款口径来自月结单资金流水。每月 HKD 扣款与 USD 扣款分开保留，"
    "USD 扣款按该月 PDF 资金详情参考汇率折为 HKD 后形成当月实际支付 HKD 等值。"
    "该口径仅作为实际现金支付审计证据，税务可扣除性仍未确认。"
)


def _round2(value: float | int | Decimal | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _decimal(value: object, *, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    return Decimal(str(value))


def _field_value(row: dict[str, FieldValue], name: str):
    return row.get(name, FieldValue.missing()).value


def _currency_row(statement: StatementResult, label: str) -> dict[str, FieldValue] | None:
    section = statement.sections.get("cash_balances", SectionResult(name="cash_balances"))
    for row in section.rows:
        if _field_value(row, "currency_label") == label:
            return row
    return None


def build_margin_interest_hkd_basis_row(statement: StatementResult) -> dict[str, object]:
    """Return the PDF-statement HKD tax-basis row for one month.

    Longbridge statements list per-currency *accrued interest* in the cash
    balance table together with that month's reference FX rate.  For tax review
    the deductible financing interest is kept on Longbridge's HKD basis:

        HKD accrued interest + USD accrued interest * the same statement's
        USD/HKD reference rate.

    The ordinary trade/dividend/fee items remain in their original currency and
    can be translated only at year-end by a separate tax FX table.
    """
    hkd_row = _currency_row(statement, HKD_LABEL)
    usd_row = _currency_row(statement, USD_LABEL)
    summary_row = _currency_row(statement, SUMMARY_HKD_LABEL)

    hkd_interest = _decimal(_field_value(hkd_row or {}, "accrued_interest"))
    usd_interest = _decimal(_field_value(usd_row or {}, "accrued_interest"))
    usd_hkd_rate = _field_value(usd_row or {}, "reference_rate")

    if usd_interest and usd_hkd_rate is None:
        usd_hkd_equivalent = None
        total_hkd_interest = None
    else:
        usd_hkd_equivalent_decimal = usd_interest * _decimal(usd_hkd_rate)
        usd_hkd_equivalent = _round2(usd_hkd_equivalent_decimal)
        total_hkd_interest = _round2(hkd_interest + _decimal(usd_hkd_equivalent))

    pdf_summary_hkd_interest = _field_value(summary_row or {}, "accrued_interest")
    difference_vs_pdf_summary = None
    validation_status = "ok"
    validation_note = "computed from HKD/USD cash-balance accrued_interest and same-month USD/HKD reference_rate"

    if usd_interest and usd_hkd_rate is None:
        validation_status = "error"
        validation_note = "USD accrued financing interest exists but PDF reference_rate is missing"
    elif pdf_summary_hkd_interest is not None and total_hkd_interest is not None:
        difference_vs_pdf_summary = _round2(_decimal(total_hkd_interest) - _decimal(pdf_summary_hkd_interest))
        if abs(float(difference_vs_pdf_summary)) > 0.02:
            validation_status = "error"
            validation_note = "computed HKD total does not match PDF 汇总(HKD) accrued_interest"
        else:
            validation_note = "computed HKD total matches PDF 汇总(HKD) accrued_interest"

    return {
        "statement_month": statement.statement_month,
        "month_label": f"{statement.statement_month[:4]}-{statement.statement_month[4:]}" if statement.statement_month.isdigit() and len(statement.statement_month) == 6 else statement.statement_month,
        "source_pdf": Path(statement.source_pdf).name if statement.source_pdf else "",
        "hkd_accrued_interest": _round2(hkd_interest),
        "usd_accrued_interest": _round2(usd_interest),
        "pdf_usd_hkd_reference_rate": usd_hkd_rate,
        "usd_interest_hkd_equivalent": usd_hkd_equivalent,
        "total_margin_interest_hkd_tax_basis": total_hkd_interest,
        "deductible_amount_hkd_abs": _round2(abs(total_hkd_interest)) if total_hkd_interest is not None else None,
        "pdf_summary_hkd_accrued_interest": _round2(pdf_summary_hkd_interest),
        "difference_vs_pdf_summary": difference_vs_pdf_summary,
        "validation_status": validation_status,
        "validation_note": validation_note,
        "basis_note": MARGIN_INTEREST_TAX_BASIS_NOTE,
    }


def build_margin_interest_hkd_basis_rows(statements: Iterable[StatementResult]) -> list[dict[str, object]]:
    return [build_margin_interest_hkd_basis_row(statement) for statement in sorted(statements, key=lambda st: st.statement_month)]


def margin_interest_hkd_tax_basis_total(statements: Iterable[StatementResult]) -> float:
    total = Decimal("0.00")
    for row in build_margin_interest_hkd_basis_rows(statements):
        value = row.get("total_margin_interest_hkd_tax_basis")
        if value is not None:
            total += Decimal(str(value))
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def build_margin_interest_actual_payment_row(statement: StatementResult) -> dict[str, object]:
    """Build one month's actual cash-payment financing-interest evidence.

    The cash-flow section can contain a HKD financing-interest debit and a USD
    debit.  The USD debit is translated to HKD with the *same statement month's*
    PDF reference rate.  No year-end rate is used at this stage.
    """

    flow_section = statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
    hkd_paid = Decimal("0")
    usd_paid = Decimal("0")
    evidence: list[str] = []
    for row in flow_section.rows:
        if _field_value(row, "tax_category") != "margin_interest_deductible":
            continue
        amount = _decimal(_field_value(row, "cash_amount") or _field_value(row, "amount"))
        currency = str(_field_value(row, "currency") or "")
        if currency == "HKD":
            hkd_paid += amount
        elif currency == "USD":
            usd_paid += amount
        evidence.append(str(_field_value(row, "raw_detail") or ""))

    usd_row = _currency_row(statement, USD_LABEL)
    usd_hkd_rate = _field_value(usd_row or {}, "reference_rate")
    if usd_paid and usd_hkd_rate is None:
        usd_hkd_equivalent = None
        total_hkd_equivalent = None
        status = "error"
        note = "USD actual financing-interest debit exists but same-month PDF reference_rate is missing"
    else:
        usd_hkd_equivalent = _round2(usd_paid * _decimal(usd_hkd_rate))
        total_hkd_equivalent = _round2(hkd_paid + _decimal(usd_hkd_equivalent))
        status = "ok"
        note = "actual HKD/USD cash debits translated with the same statement month's PDF USD/HKD reference rate"

    return {
        "statement_month": statement.statement_month,
        "month_label": f"{statement.statement_month[:4]}-{statement.statement_month[4:]}" if statement.statement_month.isdigit() and len(statement.statement_month) == 6 else statement.statement_month,
        "source_pdf": Path(statement.source_pdf).name if statement.source_pdf else "",
        "hkd_actual_payment": _round2(hkd_paid),
        "usd_actual_payment": _round2(usd_paid),
        "pdf_usd_hkd_reference_rate": usd_hkd_rate,
        "usd_actual_payment_hkd_equivalent": usd_hkd_equivalent,
        "total_actual_payment_hkd_equivalent": total_hkd_equivalent,
        "validation_status": status,
        "validation_note": note,
        "basis_note": MARGIN_INTEREST_ACTUAL_PAYMENT_NOTE,
        "cash_flow_evidence": " | ".join(item for item in evidence if item),
    }


def build_margin_interest_actual_payment_rows(statements: Iterable[StatementResult]) -> list[dict[str, object]]:
    return [build_margin_interest_actual_payment_row(statement) for statement in sorted(statements, key=lambda st: st.statement_month)]
