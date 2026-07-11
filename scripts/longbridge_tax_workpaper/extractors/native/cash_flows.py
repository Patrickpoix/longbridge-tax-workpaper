from __future__ import annotations

import re

from ...ingest import IngestedDocument
from ...normalize import canonical_transaction_type, classify_tax_category, normalize_text, pick_amount
from ...schema import FieldValue, SectionResult

OTHER_FUND_FLOWS = "其他资金出入明细"
LIABILITY = "责任说明"
CURRENCY_LABEL = "币种"
HKD_LABEL = "港元"
USD_LABEL = "美元"
SUMMARY_LABEL = "汇总"
FLOW_TYPES = sorted(
    [
        "强制性企业行动股票出账",
        "公司行动资金入账",
        "公司行动资金出账",
        "公司行动其他费用",
        "公司行动股票进账",
        "公司行动股票出账",
        "贷款利息",
        "融资利息",
        "现金分红",
        "存入资金",
        "提出资金",
        "活动礼包",
        "现金奖励",
        "ADR收费",
        "股票交易",
    ],
    key=len,
    reverse=True,
)
FLOW_PATTERN = "|".join(re.escape(item) for item in FLOW_TYPES)
ENTRY_RE = re.compile(
    rf"(\d{{4}}\.\d{{2}}\.\d{{2}})\s+({FLOW_PATTERN})\s+(.+?)(?=\s+\d{{4}}\.\d{{2}}\.\d{{2}}\s+(?:{FLOW_PATTERN})|\s+{SUMMARY_LABEL}\s*\(|\s+{CURRENCY_LABEL}:|\s+其他持仓出入明细|\s+股票交易明细|\s+期权交易明细|\s+{LIABILITY}|除非另有说明|综合账户月结单|Page\s+\d+\s+of|$)",
    re.DOTALL,
)
DISCLAIMER_RE = re.compile(r"(除非另有说明|综合账户月结单|Page\s+\d+\s+of).*", re.DOTALL)
CURRENCY_RE = re.compile(rf"(?:{CURRENCY_LABEL}|Currency)\s*[:：]\s*({HKD_LABEL}|{USD_LABEL}|HKD|USD)", re.IGNORECASE)



SECURITY_CODE_RE = re.compile(r"#?(\d{4,5})(?:\.HK)?", re.IGNORECASE)


def _security_code(text: object) -> str:
    match = SECURITY_CODE_RE.search(str(text or ""))
    return match.group(1) if match else ""


def _is_auto_ex(text: object) -> bool:
    upper = str(text or "").upper()
    return "AUTO-EX" in upper or "AUTO EX" in upper or "AUTOMATIC EXERCISE" in upper


def _reclassify_auto_ex_context(rows: list[dict[str, FieldValue]]) -> None:
    """Resolve derivative AUTO-EX cash/fee rows using same-date security context.

    A generic company-action cash receipt is not automatically taxable income.
    When the PDF explicitly identifies an automatic exercise/settlement and a
    matching forced stock-out, the cash receipt is proceeds for realized-P&L
    computation.  Same-event handling fees are transaction costs; the separate
    corporate-action fee follows the user-confirmed non-deductible treatment.
    """
    events: set[tuple[str, str]] = set()
    for row in rows:
        detail = row.get("raw_detail", FieldValue.missing()).value
        row_type = row.get("type", FieldValue.missing()).value
        if row_type == "company_action_cash_in" and _is_auto_ex(detail):
            code = _security_code(detail)
            date = str(row.get("date", FieldValue.missing()).value or "")
            if code:
                events.add((date, code))
                row["tax_category"] = FieldValue.derived(
                    "derivative_auto_ex_proceeds",
                    raw_text=str(detail or ""),
                    confidence=0.99,
                    warnings=["reclassified_from_company_action_cash_by_auto_ex_context"],
                )

    if not events:
        return

    for row in rows:
        detail = str(row.get("raw_detail", FieldValue.missing()).value or "")
        date = str(row.get("date", FieldValue.missing()).value or "")
        code = _security_code(detail)
        if (date, code) not in events:
            continue
        if row.get("type", FieldValue.missing()).value != "company_action_fee":
            continue
        lower = detail.lower()
        if "handling fee" in lower:
            row["tax_category"] = FieldValue.derived(
                "derivative_settlement_fee_deductible",
                raw_text=detail,
                confidence=0.99,
                warnings=["matched_to_auto_ex_settlement"],
            )
        elif "corporate action fee" in lower:
            row["tax_category"] = FieldValue.derived(
                "derivative_settlement_fee_non_deductible",
                raw_text=detail,
                confidence=0.99,
                warnings=["matched_to_auto_ex_settlement_user_non_deductible"],
            )


USD_TOKENS = [
    ".US",
    " USD",
    "PDD US",
    "AVGO",
    "VST",
    "YINN",
    "DFEN",
    "UNH",
    "UNHG",
    "UGL",
    "CLF",
    "NIO",
    "BIDU",
    "OSCR",
    "OPEN",
    "RUN",
    "RKLB",
    "PATH",
    "PROSHARES",
    "DIREXION",
    "VISTRA",
    "BROADCOM",
    "UNITEDHEALTH",
    "LEVERAGE SHARES",
]
HKD_TOKENS = [
    ".HK",
    " HKD",
    "01288",
    "1288.HK",
    "00288",
    "0288",
    "00857",
    "857.HK",
    "01378",
    "1378.HK",
    "01508",
    "1508.HK",
    "01776",
    "1776.HK",
    "03800",
    "3800.HK",
    "06886",
    "6886.HK",
    "06979",
    "6979.HK",
    "07234",
    "7234.HK",
    "13773",
    "17128",
    "17986",
    "18529",
    "19047",
    "PETROCHINA",
    "ABC",
    "WH GROUP",
    "HTSC",
    "农业银行",
    "中国宏桥",
    "华泰",
    "石油",
    "中芯",
    "万洲",
    "三生制药",
]


def _flow_section_text(document: IngestedDocument) -> tuple[str, bool]:
    full_text = normalize_text("\n".join(page.text for page in document.pages))
    start = full_text.find(OTHER_FUND_FLOWS)
    if start < 0:
        return full_text, False
    section = full_text[start:]
    end = section.find(LIABILITY)
    if end >= 0:
        section = section[:end]
    return section, True


def _trim_disclaimer(text: str) -> str:
    match = DISCLAIMER_RE.search(text)
    if match:
        return text[: match.start()].rstrip()
    return text


def _currency_code(label: str) -> str:
    normalized = str(label).upper()
    return "HKD" if label == HKD_LABEL or normalized == "HKD" else "USD"


def _infer_currency(detail: str, transaction_type: str, amount: float | None) -> str | None:
    normalized = normalize_text(detail)
    upper = normalized.upper()
    if any(token in upper for token in USD_TOKENS):
        return "USD"
    if any(token in upper for token in HKD_TOKENS):
        return "HKD"

    # Never infer currency from the amount magnitude.  Legacy unlabeled
    # financing-interest rows are resolved later against the previous month's
    # per-currency accrued-interest values; ambiguous rows remain unresolved.
    return None


def _missing_currency(raw_detail: str, warning: str) -> FieldValue:
    return FieldValue.missing(raw_text=raw_detail, warnings=[warning])


def _row(date: str, raw_type: str, raw_detail: str, currency: str | None) -> dict[str, FieldValue] | None:
    trimmed_detail = _trim_disclaimer(raw_detail)
    transaction_type = canonical_transaction_type(raw_type, trimmed_detail)
    amount_candidate = pick_amount(trimmed_detail, transaction_type=raw_type)
    if amount_candidate is None and transaction_type not in {"company_action_stock_in", "company_action_stock_out"}:
        return None

    amount_value = amount_candidate.value if amount_candidate is not None else None
    amount_raw = amount_candidate.text if amount_candidate is not None else None
    amount_warnings = list(amount_candidate.reasons) if amount_candidate is not None else []
    inferred_currency = _infer_currency(trimmed_detail, transaction_type, amount_value)
    # Currency blocks describe the cash ledger, but non-cash stock-action rows
    # can appear adjacent to the wrong cash block. For stock in/out entries, use
    # the security/detail marker (.HK/.US/HKD/USD/ticker) first; for true cash
    # movements, keep the explicit ledger block as authoritative.
    if transaction_type in {"company_action_stock_in", "company_action_stock_out"}:
        resolved_currency = inferred_currency or currency
    else:
        resolved_currency = currency or inferred_currency
    tax_category = classify_tax_category(transaction_type, trimmed_detail)
    normalized_detail = normalize_text(raw_detail)
    row_confidence = max(0.1, min(0.99, (amount_candidate.score if amount_candidate else 80.0) / 100.0))
    quantity_change_value = None

    if transaction_type in {"company_action_stock_in", "company_action_stock_out"}:
        quantity_change_value = amount_value
        amount_value = None
        amount_warnings.append("non_cash_company_action_quantity_not_cash_amount")

    row: dict[str, FieldValue] = {
        "date": FieldValue.native(date, raw_text=date, confidence=0.95),
        "type": FieldValue.derived(transaction_type, raw_text=raw_type, confidence=0.9),
        "raw_type": FieldValue.native(raw_type, raw_text=raw_type, confidence=0.95),
        "raw_detail": FieldValue.native(normalized_detail, raw_text=raw_detail, confidence=0.9),
        "amount": (
            FieldValue.native(
                amount_value,
                raw_text=amount_raw,
                confidence=row_confidence,
                warnings=amount_warnings,
            )
            if amount_value is not None
            else FieldValue.missing(raw_text=amount_raw or raw_detail, warnings=amount_warnings or ["cash_amount_not_applicable"])
        ),
        "cash_amount": (
            FieldValue.native(amount_value, raw_text=amount_raw, confidence=row_confidence, warnings=amount_warnings)
            if amount_value is not None
            else FieldValue.missing(raw_text=amount_raw or raw_detail, warnings=amount_warnings or ["cash_amount_not_applicable"])
        ),
        "currency": (
            FieldValue.derived(resolved_currency, raw_text=raw_detail, confidence=0.95 if (currency and resolved_currency == currency) else 0.82)
            if resolved_currency is not None
            else _missing_currency(raw_detail, "currency_not_found_in_cash_flow_context")
        ),
        "tax_category": FieldValue.derived(tax_category, raw_text=raw_detail, confidence=0.9),
    }
    row["quantity_change"] = (
        FieldValue.native(
            quantity_change_value,
            raw_text=amount_raw,
            confidence=row_confidence,
            warnings=["extracted_from_non_cash_company_action"],
        )
        if quantity_change_value is not None
        else FieldValue.missing(raw_text=raw_detail, warnings=["not_applicable"])
    )
    return row


def _parse_currency_blocks(section_text: str) -> list[dict[str, FieldValue]]:
    rows: list[dict[str, FieldValue]] = []
    matches = list(CURRENCY_RE.finditer(section_text))
    for index, match in enumerate(matches):
        currency = _currency_code(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section_text)
        block = section_text[start:end]
        for entry in ENTRY_RE.finditer(block):
            row = _row(entry.group(1), entry.group(2), entry.group(3), currency)
            if row is not None:
                rows.append(row)
    return rows


def _parse_legacy_text(text: str) -> list[dict[str, FieldValue]]:
    rows: list[dict[str, FieldValue]] = []
    end = text.find(LIABILITY)
    if end >= 0:
        text = text[:end]
    for entry in ENTRY_RE.finditer(text):
        row = _row(entry.group(1), entry.group(2), entry.group(3), None)
        if row is not None:
            rows.append(row)
    return rows


def extract_other_fund_flows(document: IngestedDocument) -> SectionResult:
    section_text, has_anchor = _flow_section_text(document)
    rows = _parse_currency_blocks(section_text)
    if not rows:
        rows = _parse_legacy_text(section_text)
    _reclassify_auto_ex_context(rows)
    result = SectionResult(name="other_fund_flows", rows=rows)
    result.fields["row_count"] = FieldValue.derived(len(rows), raw_text=OTHER_FUND_FLOWS if has_anchor else None, confidence=0.9)
    if not has_anchor:
        result.warnings.append("Other fund flow anchor not found; parsed legacy text fallback")
    return result
