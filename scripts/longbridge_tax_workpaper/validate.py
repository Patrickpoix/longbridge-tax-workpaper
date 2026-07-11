from __future__ import annotations

from dataclasses import dataclass
import re

from .schema import FieldValue, SectionResult, StatementResult, ValidationResult
from .margin_interest import build_margin_interest_hkd_basis_row
from .filing_policy import is_pending_review_category
from .taxonomy import CASH_TAX_CATEGORIES, KNOWN_CURRENCIES, TAX_CATEGORY_NOTES, UNKNOWN_CURRENCY, normalize_currency

TOLERANCE = 0.02
AMOUNT_TOLERANCE = 0.05
RATE_TOLERANCE = 0.0001
HOLDINGS_MV_WARNING_PCT = 0.5
HOLDINGS_MV_ERROR_PCT = 2.0


def _num(value: FieldValue | None, default: float = 0.0) -> float:
    if value is None or value.value is None:
        return default
    return float(value.value)


def _field(value: FieldValue | None) -> object:
    if value is None:
        return None
    return value.value


@dataclass(slots=True)
class ValidationContext:
    statement: StatementResult
    validations: list[ValidationResult]

    def add(self, rule: str, passed: bool, message: str, *, severity: str = "info", details: dict | None = None):
        self.validations.append(
            ValidationResult(
                rule=rule,
                passed=passed,
                message=message,
                severity=severity,
                details=details or {},
            )
        )


def validate_statement(statement: StatementResult) -> list[ValidationResult]:
    ctx = ValidationContext(statement=statement, validations=[])

    _check_cash_balance_flows(ctx)
    _check_cash_currency_conversion(ctx)
    _check_total_assets_equation(ctx)
    _check_trade_amounts(ctx, "stock")
    _check_trade_amounts(ctx, "option")
    _check_trade_source_counts(ctx, "stock")
    _check_trade_source_counts(ctx, "option")
    _check_trade_currency(ctx, "stock")
    _check_trade_currency(ctx, "option")
    _check_trade_fee_currency_presence(ctx)
    _check_holdings_market_value(ctx)
    _check_fund_flow_totals(ctx)
    _check_fund_flow_currency_presence(ctx)
    _check_tax_summary_coverage(ctx)
    _check_derivative_auto_ex_classification(ctx)
    _check_margin_interest_hkd_tax_basis(ctx)

    return ctx.validations


def _get_usd_rate(statement: StatementResult) -> float | None:
    for row in statement.sections.get("cash_balances", SectionResult(name="cash_balances")).rows:
        if row.get("currency_label", FieldValue.missing()).value == "美元":
            return _num(row.get("reference_rate"))
    return None


def _holding_name(row: dict[str, FieldValue], index: int) -> str:
    return str(row.get("name", FieldValue.missing()).value or f"row[{index}]")


def _hkd_holdings_mv(statement: StatementResult) -> tuple[float, list[str], list[str]]:
    total = 0.0
    usd_rate = _get_usd_rate(statement)
    unresolved_rows: list[str] = []
    missing_rate_rows: list[str] = []

    for index, row in enumerate(statement.sections.get("holdings", SectionResult(name="holdings")).rows, start=1):
        mv = _num(row.get("market_value"))
        currency = row.get("currency", FieldValue.missing()).value
        name = _holding_name(row, index)

        if currency == "USD":
            if usd_rate is None:
                unresolved_rows.append(name)
                missing_rate_rows.append(name)
                continue
            total += mv * usd_rate
            continue
        if currency == "HKD":
            total += mv
            continue
        if mv != 0:
            unresolved_rows.append(name)

    return round(total, 2), unresolved_rows, missing_rate_rows


def _check_cash_balance_flows(ctx: ValidationContext) -> None:
    section = ctx.statement.sections.get("cash_balances", SectionResult(name="cash_balances"))
    if not section.rows:
        ctx.add("cash_balance_flow", True, "No cash balance rows to check", severity="info")
        return

    all_ok = True
    failures: list[str] = []
    for index, row in enumerate(section.rows):
        label = str(row.get("currency_label", FieldValue.missing()).value or f"row[{index}]")
        opening = _num(row.get("opening_balance"))
        change = _num(row.get("change_amount"))
        ending = _num(row.get("ending_balance"))
        expected = round(opening + change, 2)
        if abs(expected - ending) > TOLERANCE:
            all_ok = False
            failures.append(f"{label}: opening({opening}) + change({change}) = {expected}, ending={ending}")

    ctx.add(
        "cash_balance_flow",
        all_ok,
        f"Cash balance flow check: {len(section.rows)} rows checked" if all_ok else f"Cash balance flow failures: {'; '.join(failures)}",
        severity="error" if not all_ok else "info",
        details={"failures": failures},
    )


def _check_cash_currency_conversion(ctx: ValidationContext) -> None:
    section = ctx.statement.sections.get("cash_balances", SectionResult(name="cash_balances"))
    usd_rate = _get_usd_rate(ctx.statement)
    if usd_rate is None or len(section.rows) < 2:
        ctx.add("cash_currency_conversion", True, "Skipped: no USD rate or insufficient cash rows", severity="info")
        return

    all_ok = True
    failures: list[str] = []
    for row in section.rows:
        label = str(row.get("currency_label", FieldValue.missing()).value or "")
        ending = _num(row.get("ending_balance"))
        rate = _num(row.get("reference_rate"))
        ending_hkd = _num(row.get("ending_hkd_balance"))
        if rate is None or rate == 0.0:
            continue
        expected_hkd = round(ending * rate, 2)
        if abs(expected_hkd - ending_hkd) > TOLERANCE:
            all_ok = False
            failures.append(f"{label}: {ending} x {rate} = {expected_hkd}, ending_hkd={ending_hkd}")

    ctx.add(
        "cash_currency_conversion",
        all_ok,
        "Currency conversion check passed" if all_ok else f"Conversion failures: {'; '.join(failures)}",
        severity="error" if not all_ok else "info",
        details={"usd_rate": usd_rate, "failures": failures},
    )


def _check_total_assets_equation(ctx: ValidationContext) -> None:
    overview = ctx.statement.sections.get("account_overview")
    if overview is None:
        ctx.add("total_assets_equation", True, "Skipped: account overview section is absent", severity="info")
        return
    total_assets = _num(overview.fields.get("total_assets"))
    market_value = _num(overview.fields.get("market_value"))
    cash_balance = _num(overview.fields.get("cash_balance"))
    expected = round(market_value + cash_balance, 2)
    passed = abs(expected - total_assets) < AMOUNT_TOLERANCE

    ctx.add(
        "total_assets_equation",
        passed,
        f"TotalAssets({total_assets}) = MarketValue({market_value}) + CashBalance({cash_balance}) = {expected}" if passed
        else f"TotalAssets({total_assets}) != MarketValue({market_value}) + CashBalance({cash_balance}) = {expected}",
        severity="error" if not passed else "info",
        details={"total_assets": total_assets, "market_value": market_value, "cash_balance": cash_balance, "expected": expected},
    )


def _check_trade_amounts(ctx: ValidationContext, sheet_type: str) -> None:
    section_name = f"{sheet_type}_trades"
    section = ctx.statement.sections.get(section_name, SectionResult(name=section_name))
    if not section.rows:
        ctx.add(f"{sheet_type}_trade_amount", True, f"No {sheet_type} trades to check", severity="info")
        return

    multiplier = 100 if sheet_type == "option" else 1
    all_ok = True
    failures: list[str] = []
    for index, row in enumerate(section.rows, start=1):
        qty = _num(row.get("quantity"))
        price = _num(row.get("price"))
        amount = _num(row.get("amount"))
        symbol = str(_field(row.get("symbol")) or f"row[{index}]")
        if qty == 0 and amount == 0:
            continue
        expected = round(qty * price * multiplier, 2)
        smart_tol = max(AMOUNT_TOLERANCE, abs(amount) * 0.002)
        if abs(expected - amount) > smart_tol:
            all_ok = False
            failures.append(f"{symbol}: qty({qty}) x price({price}) x {multiplier} = {expected}, amount={amount}")

    label_cn = "stock" if sheet_type == "stock" else "option"
    ctx.add(
        f"{sheet_type}_trade_amount",
        all_ok,
        f"{label_cn} trade qty x price check passed ({len(section.rows)} rows)" if all_ok
        else f"{label_cn} trade qty x price mismatch: {'; '.join(failures)}",
        severity="error" if not all_ok else "info",
        details={"failures": failures, "multiplier": multiplier},
    )


def _check_trade_source_counts(ctx: ValidationContext, sheet_type: str) -> None:
    if sheet_type != "stock":
        return
    section = ctx.statement.sections.get("stock_trades", SectionResult(name="stock_trades"))
    source_count = section.fields.get("all_source_order_id_count")
    parsed_count = section.fields.get("all_parsed_order_id_count")
    if source_count is None or parsed_count is None:
        ctx.add("all_trade_source_count", True, "No source-order count available", severity="info")
        return
    passed = _num(source_count) == _num(parsed_count)
    details = {
        "source_order_id_count": source_count.value,
        "parsed_order_id_count": parsed_count.value,
        "unmatched_order_ids": section.fields.get("unmatched_order_ids", FieldValue.derived([])).value,
    }
    message = f"Source order ids={source_count.value}, parsed trade rows={parsed_count.value}"
    ctx.add(
        "trade_order_count",
        passed,
        message,
        severity="error" if not passed else "info",
        details=details,
    )
    # Backward-compatible rule name retained for existing audit workbooks.
    ctx.add(
        "all_trade_source_count",
        passed,
        message,
        severity="error" if not passed else "info",
        details=details,
    )


def _check_trade_currency(ctx: ValidationContext, sheet_type: str) -> None:
    section_name = f"{sheet_type}_trades"
    section = ctx.statement.sections.get(section_name, SectionResult(name=section_name))
    missing: list[str] = []
    for index, row in enumerate(section.rows, start=1):
        currency = row.get("currency", FieldValue.missing()).value
        if currency not in KNOWN_CURRENCIES:
            missing.append(str(row.get("order_id", FieldValue.derived(f"row[{index}]")).value))
    passed = not missing
    ctx.add(
        f"{sheet_type}_trade_currency",
        passed,
        f"All {sheet_type} trade currencies resolved" if passed else f"Missing {sheet_type} trade currency: {', '.join(missing)}",
        severity="error" if not passed else "info",
        details={"missing_order_ids": missing},
    )


def _check_trade_fee_currency_presence(ctx: ValidationContext) -> None:
    missing: list[str] = []
    for section_name in ("stock_trades", "option_trades"):
        section = ctx.statement.sections.get(section_name, SectionResult(name=section_name))
        for index, row in enumerate(section.rows, start=1):
            currency = row.get("currency", FieldValue.missing()).value
            if currency not in KNOWN_CURRENCIES:
                order_id = row.get("order_id", FieldValue.derived(f"{section_name}[{index}]", confidence=1.0)).value
                missing.append(str(order_id))
    passed = not missing
    ctx.add(
        "trade_fee_currency_presence",
        passed,
        "All trade fee currencies are explicitly resolved" if passed else "Trade rows with unresolved fee currency: " + ", ".join(missing),
        severity="error" if not passed else "info",
        details={"missing_order_ids": missing},
    )


def _check_fund_flow_currency_presence(ctx: ValidationContext) -> None:
    section = ctx.statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
    missing: list[str] = []
    for index, row in enumerate(section.rows, start=1):
        category = str(row.get("tax_category", FieldValue.missing()).value or "")
        if category == "non_cash_company_action":
            continue
        currency = row.get("currency", FieldValue.missing()).value
        if normalize_currency(currency) == UNKNOWN_CURRENCY:
            raw_type = row.get("raw_type", row.get("type", FieldValue.derived(f"row[{index}]", confidence=1.0))).value
            missing.append(f"row[{index}] {raw_type}")
    passed = not missing
    ctx.add(
        "fund_flow_currency_presence",
        passed,
        "All cash fund-flow currencies are explicitly resolved" if passed else "Fund-flow rows with unresolved currency: " + "; ".join(missing),
        severity="error" if not passed else "info",
        details={"missing_rows": missing},
    )


def _check_holdings_market_value(ctx: ValidationContext) -> None:
    overview = ctx.statement.sections.get("account_overview")
    if overview is None:
        ctx.add("holdings_market_value", True, "Skipped: account overview section is absent", severity="info")
        return
    overview_mv = _num(overview.fields.get("market_value"))
    holdings_mv, unresolved_rows, missing_rate_rows = _hkd_holdings_mv(ctx.statement)

    if missing_rate_rows:
        ctx.add(
            "holdings_market_value",
            False,
            f"Missing USD rate for holdings market value conversion: {', '.join(missing_rate_rows)}",
            severity="error",
            details={"holdings_mv": holdings_mv, "overview_mv": overview_mv, "unresolved_rows": missing_rate_rows},
        )
        return

    if unresolved_rows:
        ctx.add(
            "holdings_market_value",
            False,
            f"Missing holding currency for holdings market value comparison: {', '.join(unresolved_rows)}",
            severity="error",
            details={"holdings_mv": holdings_mv, "overview_mv": overview_mv, "unresolved_rows": unresolved_rows},
        )
        return

    if holdings_mv == 0:
        ctx.add("holdings_market_value", True, "No holdings market value to compare", severity="info")
        return

    diff = abs(holdings_mv - overview_mv)
    diff_pct = (diff / max(abs(overview_mv), 1)) * 100
    details = {"holdings_mv": holdings_mv, "overview_mv": overview_mv, "diff": round(diff, 2), "diff_pct": round(diff_pct, 4)}
    message = f"HoldingsMV(HKD-converted) {holdings_mv} vs OverviewMV {overview_mv}, diff {diff:.2f} ({diff_pct:.2f}%)"
    if diff_pct > HOLDINGS_MV_ERROR_PCT:
        ctx.add("holdings_market_value", False, message, severity="error", details=details)
    elif diff_pct > HOLDINGS_MV_WARNING_PCT:
        ctx.add("holdings_market_value", True, message, severity="warning", details=details)
    else:
        ctx.add("holdings_market_value", True, message, severity="info", details=details)


def _cash_amount_for_row(row: dict[str, FieldValue]) -> float:
    if "cash_amount" in row and row["cash_amount"].value is not None:
        return _num(row["cash_amount"])
    return _num(row.get("amount"))


def _check_fund_flow_totals(ctx: ValidationContext) -> None:
    section = ctx.statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
    if not section.rows:
        ctx.add("fund_flow_totals", True, "No fund flow rows to check", severity="info")
        return

    by_category: dict[tuple[str, str], float] = {}
    for row in section.rows:
        category = str(row.get("tax_category", FieldValue.missing()).value or "")
        if category == "non_cash_company_action":
            continue
        currency = normalize_currency(row.get("currency", FieldValue.missing()).value)
        key = (category, currency)
        by_category[key] = by_category.get(key, 0.0) + round(_cash_amount_for_row(row), 2)

    messages: list[str] = []
    for (cat, cur), total in sorted(by_category.items()):
        if abs(total) < 0.01:
            continue
        messages.append(f"{cat}({cur}): {total:.2f}")

    if not messages:
        messages.append("No non-zero fund flow categories")

    ctx.add(
        "fund_flow_totals",
        True,
        f"Fund flow by category: {'; '.join(messages)}",
        severity="info",
        details={"by_category": {f"{k[0]}({k[1]})": v for k, v in by_category.items()}},
    )


def _check_tax_summary_coverage(ctx: ValidationContext) -> None:
    section = ctx.statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
    unknown_categories: list[str] = []
    unknown_currency_rows: list[str] = []
    non_cash_amount_rows: list[str] = []
    unresolved_policy_rows: list[str] = []
    for index, row in enumerate(section.rows, start=1):
        category = str(row.get("tax_category", FieldValue.missing()).value or "")
        currency = row.get("currency", FieldValue.missing()).value
        raw_field = row.get("raw_type") or row.get("type") or FieldValue.derived(f"row[{index}]", confidence=1.0)
        raw_type = str(raw_field.value or f"row[{index}]")
        amount = row.get("amount", FieldValue.missing()).value
        if category not in TAX_CATEGORY_NOTES:
            unknown_categories.append(f"row[{index}] {raw_type}: {category}")
        if category in CASH_TAX_CATEGORIES and normalize_currency(currency) == UNKNOWN_CURRENCY:
            unknown_currency_rows.append(f"row[{index}] {raw_type}")
        if category == "non_cash_company_action" and amount is not None:
            non_cash_amount_rows.append(f"row[{index}] {raw_type}")
        if is_pending_review_category(category) and amount not in (None, 0, 0.0):
            unresolved_policy_rows.append(f"row[{index}] {raw_type}: {category}")

    failures = []
    if unknown_categories:
        failures.append("unknown categories: " + "; ".join(unknown_categories))
    if unknown_currency_rows:
        failures.append("tax-summary rows with unknown currency: " + "; ".join(unknown_currency_rows))
    if non_cash_amount_rows:
        failures.append("non-cash company actions carrying cash amount: " + "; ".join(non_cash_amount_rows))
    if unresolved_policy_rows:
        failures.append("unresolved tax-policy rows: " + "; ".join(unresolved_policy_rows))
    passed = not failures
    ctx.add(
        "tax_summary_coverage",
        passed,
        "All fund-flow tax categories and currencies are explicitly handled" if passed else "Tax summary coverage failures: " + " | ".join(failures),
        severity="error" if not passed else "info",
        details={
            "unknown_categories": unknown_categories,
            "unknown_currency_rows": unknown_currency_rows,
            "non_cash_amount_rows": non_cash_amount_rows,
            "unresolved_policy_rows": unresolved_policy_rows,
        },
    )


def _check_derivative_auto_ex_classification(ctx: ValidationContext) -> None:
    section = ctx.statement.sections.get("other_fund_flows", SectionResult(name="other_fund_flows"))
    auto_ex_rows: list[dict[str, object]] = []
    failures: list[str] = []
    code_re = re.compile(r"#?(\d{4,5})(?:\.HK)?", re.IGNORECASE)

    for row in section.rows:
        detail = str(row.get("raw_detail", FieldValue.missing()).value or "")
        if "AUTO-EX" not in detail.upper() and "AUTO EX" not in detail.upper():
            continue
        row_type = str(row.get("type", FieldValue.missing()).value or "")
        category = str(row.get("tax_category", FieldValue.missing()).value or "")
        date = str(row.get("date", FieldValue.missing()).value or "")
        match = code_re.search(detail)
        code = match.group(1) if match else ""
        auto_ex_rows.append({"date": date, "code": code, "type": row_type, "category": category, "detail": detail})
        if row_type == "company_action_cash_in" and category != "derivative_auto_ex_proceeds":
            failures.append(f"AUTO-EX cash row not classified as derivative proceeds: {detail}")
        if row_type == "company_action_fee" and "handling fee" in detail.lower() and category != "derivative_settlement_fee_deductible":
            failures.append(f"AUTO-EX handling fee not classified into derivative P&L: {detail}")
        if row_type == "company_action_fee" and "corporate action fee" in detail.lower() and category != "derivative_settlement_fee_non_deductible":
            failures.append(f"AUTO-EX corporate-action fee not separately non-deductible: {detail}")

    cash_events = {(item["date"], item["code"]) for item in auto_ex_rows if item["type"] == "company_action_cash_in"}
    stock_out_events = {(item["date"], item["code"]) for item in auto_ex_rows if item["type"] == "company_action_stock_out"}
    for event in cash_events:
        if event not in stock_out_events:
            failures.append(f"AUTO-EX cash event lacks same-date forced stock-out: {event}")

    passed = not failures
    ctx.add(
        "derivative_auto_ex_classification",
        passed,
        "AUTO-EX cash, forced stock-out and fees are classified as derivative-settlement components"
        if passed
        else "AUTO-EX classification failures: " + " | ".join(failures),
        severity="error" if not passed else "info",
        details={"rows": auto_ex_rows, "failures": failures},
    )


def _check_margin_interest_hkd_tax_basis(ctx: ValidationContext) -> None:
    row = build_margin_interest_hkd_basis_row(ctx.statement)
    failures: list[str] = []
    if row["validation_status"] != "ok":
        failures.append(str(row["validation_note"]))
    if row["usd_accrued_interest"] not in (None, 0, 0.0) and row["pdf_usd_hkd_reference_rate"] is None:
        failures.append("USD financing interest must use the same month's PDF-provided USD/HKD reference_rate")
    if row["total_margin_interest_hkd_tax_basis"] is None:
        failures.append("total_margin_interest_hkd_tax_basis is missing")

    passed = not failures
    ctx.add(
        "margin_interest_hkd_tax_basis",
        passed,
        "Margin interest HKD tax basis is computed from same-month PDF cash-balance accrued_interest and reference_rate"
        if passed
        else "Margin interest HKD tax-basis failures: " + " | ".join(failures),
        severity="error" if not passed else "info",
        details=row,
    )


def has_blocking_errors(statement: StatementResult) -> bool:
    """Return True when validation contains failing error-severity rules."""
    return any((not validation.passed) and validation.severity == "error" for validation in statement.validations)
