from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from .schema import FieldValue, SectionResult, StatementResult
from .validate import validate_statement


def _previous_month(month: str) -> str | None:
    if len(month) != 6 or not month.isdigit():
        return None
    year, number = int(month[:4]), int(month[4:])
    if not 1 <= number <= 12:
        return None
    if number == 1:
        return f"{year - 1}12"
    return f"{year}{number - 1:02d}"


def _money(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _previous_accruals(statement: StatementResult) -> dict[str, list[Decimal]]:
    result: dict[str, list[Decimal]] = {"HKD": [], "USD": []}
    section = statement.sections.get("cash_balances", SectionResult(name="cash_balances"))
    label_map = {"港元": "HKD", "美元": "USD"}
    for row in section.rows:
        label = row.get("currency_label", FieldValue.missing()).value
        currency = label_map.get(str(label or ""))
        value = row.get("accrued_interest", FieldValue.missing()).value
        if currency and value not in (None, 0, 0.0):
            result[currency].append(_money(value))
    return result




def _cash_balance_currency_order(statement: StatementResult) -> list[str]:
    result: list[str] = []
    section = statement.sections.get("cash_balances", SectionResult(name="cash_balances"))
    label_map = {"港元": "HKD", "美元": "USD", "HKD": "HKD", "USD": "USD"}
    for row in section.rows:
        currency = label_map.get(str(row.get("currency_label", FieldValue.missing()).value or "").upper())
        if currency and currency not in result:
            result.append(currency)
    return result


def _resolve_unique_remaining_margin_currency(
    statement: StatementResult,
    previous: StatementResult,
) -> bool:
    """Resolve only when row/currency cardinality makes the answer unique.

    Legacy statements sometimes omit currency headings while listing one debit
    per currency in the same order as the cash-balance currency rows.  Existing
    exact amount matches are retained.  We assign a remaining currency only if
    there is exactly one one-to-one mapping left; otherwise rows stay missing.
    """

    flows = statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
    margin_rows = [
        row for row in flows.rows
        if str(row.get("tax_category", FieldValue.missing()).value or "") == "margin_interest_deductible"
    ]
    unresolved = [
        row for row in margin_rows
        if row.get("currency", FieldValue.missing()).value in (None, "", "UNKNOWN")
    ]
    if not unresolved:
        return False

    prior_nonzero = [currency for currency, values in _previous_accruals(previous).items() if values]
    currency_order = _cash_balance_currency_order(previous) or _cash_balance_currency_order(statement)
    candidates = [currency for currency in currency_order if currency in prior_nonzero]
    if not candidates:
        candidates = prior_nonzero
    used = [
        str(row.get("currency", FieldValue.missing()).value)
        for row in margin_rows
        if row.get("currency", FieldValue.missing()).value not in (None, "", "UNKNOWN")
    ]
    remaining = [currency for currency in candidates if currency not in used]
    if len(unresolved) != len(remaining):
        return False

    for row, currency in zip(unresolved, remaining):
        raw = str(row.get("raw_detail", FieldValue.missing()).value or "")
        row["currency"] = FieldValue.derived(
            currency,
            raw_text=raw,
            confidence=0.94,
            warnings=[
                f"resolved_by_unique_remaining_prior_month_currency_{previous.statement_month}",
                "no_amount_magnitude_heuristic_used",
            ],
        )
    return True


def _refresh_validations(statement: StatementResult) -> None:
    retained = [
        validation
        for validation in statement.validations
        if validation.rule.startswith("native_")
        or validation.rule in {"pdf_statement_month_consistency", "ocr_structured_merge"}
    ]
    statement.validations = retained + validate_statement(statement)


def resolve_cross_month_statement_context(statements: Iterable[StatementResult]) -> list[StatementResult]:
    """Resolve legacy financing-interest currencies from prior-month accruals.

    Legacy Longbridge layouts can show actual financing-interest debits without
    a currency label.  The debit is normally the prior month's accrued interest.
    A currency is assigned only when the absolute two-decimal amount uniquely
    matches a prior-month HKD or USD accrued-interest row.  No amount-size
    heuristic is used; ambiguous rows remain unresolved and validation blocks
    them for review.
    """

    ordered = sorted(list(statements), key=lambda item: item.statement_month)
    by_month = {statement.statement_month: statement for statement in ordered}
    for statement in ordered:
        previous = by_month.get(_previous_month(statement.statement_month) or "")
        if previous is None:
            continue
        candidates = _previous_accruals(previous)
        consumed: set[tuple[str, int]] = set()
        changed = False
        flows = statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
        for row in flows.rows:
            category = str(row.get("tax_category", FieldValue.missing()).value or "")
            current_currency = row.get("currency", FieldValue.missing()).value
            if category != "margin_interest_deductible" or current_currency not in (None, "", "UNKNOWN"):
                continue
            amount_field = row.get("cash_amount") or row.get("amount") or FieldValue.missing()
            amount = _money(amount_field.value)
            matches: list[tuple[str, int]] = []
            for currency, values in candidates.items():
                for index, accrued in enumerate(values):
                    if (currency, index) in consumed:
                        continue
                    if abs(abs(amount) - abs(accrued)) <= Decimal("0.01"):
                        matches.append((currency, index))
            if len(matches) != 1:
                continue
            currency, index = matches[0]
            consumed.add((currency, index))
            raw = str(row.get("raw_detail", FieldValue.missing()).value or "")
            row["currency"] = FieldValue.derived(
                currency,
                raw_text=raw,
                confidence=0.98,
                warnings=[
                    f"matched_prior_month_{previous.statement_month}_accrued_interest",
                    "no_amount_magnitude_heuristic_used",
                ],
            )
            changed = True
        changed = _resolve_unique_remaining_margin_currency(statement, previous) or changed
        if changed:
            _refresh_validations(statement)
    return ordered
