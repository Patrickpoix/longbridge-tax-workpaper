from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ingest import IngestedDocument
from .normalize import normalize_text

ACCOUNT_OVERVIEW = "账户总览"
FINANCING_LIMIT = "融资限额"
EQUITY_WITH_LOAN_VALUE = "含贷权益价值"
CURRENCY_SECTION = "币种:"
OTHER_FUND_FLOWS = "其他资金出入明细"
STATEMENT_TITLE = "综合账户月结单"
ACCOUNT_ID_RE = re.compile(r"\b[A-Z]\d{6,}\b", re.IGNORECASE)
MONTH_RE = re.compile(r"\b20\d{2}[.\-/年]\d{1,2}\b")

STATEMENT_TITLE_ALIASES = ("综合账户月结单", "综合帐户月结单", "账户月结单", "帐户月结单")
ACCOUNT_OVERVIEW_ALIASES = ("账户总览", "帐户总览", "账户概览", "帐户概览")
FINANCING_LIMIT_ALIASES = ("融资限额", "融资额度", "可融资额度")
EQUITY_WITH_LOAN_ALIASES = ("含贷权益价值", "含贷款权益价值", "含融资权益价值")
OTHER_FUND_FLOW_ALIASES = ("其他资金出入明细", "其他资金收支明细", "其他资金流水")
TRADE_HEADER_ALIASES = (
    ("下单时间", "委托时间", "订单时间", "下單時間", "委託時間"),
    ("成交时间", "执行时间", "成交時間", "執行時間"),
    ("平均价格", "成交均价", "平均成交价", "平均價格", "成交均價"),
)
FEE_ALIASES = ("佣金", "经纪佣金", "平台费", "印花税", "交易征费", "交易费", "平台費", "交收費")
HOLDING_HEADER_ALIASES = ("持仓", "投資組合", "投资组合", "持倉")


def _contains_alias(text: str, aliases: tuple[str, ...]) -> bool:
    compact = normalize_text(text, compact=True)
    return any(normalize_text(alias, compact=True) in compact for alias in aliases)


def text_layer_requires_ocr(document: IngestedDocument) -> bool:
    """Return whether the embedded text layer is too degraded to trust alone."""

    compact = normalize_text(document.normalized_full_text, compact=True)
    if len(compact) < 24:
        return True
    if re.search(r"[�□▯]{3,}", compact):
        return True
    suspicious = sum(compact.count(char) for char in ("�", "□", "▯"))
    return suspicious / max(len(compact), 1) >= 0.01


class UnknownStatementTemplateError(ValueError):
    """Raised when a PDF does not meet a known Longbridge statement signature."""


@dataclass(slots=True)
class TemplateVersion:
    template_id: str
    recognized: bool
    score: int
    features: dict[str, bool] = field(default_factory=dict)
    missing_requirements: list[str] = field(default_factory=list)


def detect_template(document: IngestedDocument) -> TemplateVersion:
    if not document.pages:
        return TemplateVersion("unknown_template", False, 0, {}, ["document_has_no_pages"])

    first_page = document.pages[0].normalized_text
    first_words = " ".join(str(word.get("text", "")) for word in document.pages[0].words)
    first_text = f"{first_page} {first_words}"
    full_text = document.normalized_full_text

    features = {
        "has_statement_title": _contains_alias(first_text, STATEMENT_TITLE_ALIASES),
        "has_statement_month": bool(MONTH_RE.search(first_text)),
        "has_account_id": bool(ACCOUNT_ID_RE.search(full_text)),
        "has_account_overview_anchor": _contains_alias(first_text, ACCOUNT_OVERVIEW_ALIASES),
        "has_header_financing_limit": _contains_alias(first_text, FINANCING_LIMIT_ALIASES),
        "has_equity_with_loan_value": _contains_alias(first_text, EQUITY_WITH_LOAN_ALIASES),
        "has_currency_grouped_fund_flow": CURRENCY_SECTION in full_text,
        "has_other_fund_flow_anchor": _contains_alias(full_text, OTHER_FUND_FLOW_ALIASES),
        "has_trade_detail_anchor": all(_contains_alias(full_text, group) for group in TRADE_HEADER_ALIASES),
        "has_fee_anchor": _contains_alias(full_text, FEE_ALIASES),
        "has_currency_rows": "港元" in first_text or "美元" in first_text,
        "has_page_footer": bool(re.search(r"Page\s*\d+\s*of\s*\d+", full_text, re.IGNORECASE)),
        "has_holding_section": _contains_alias(full_text, HOLDING_HEADER_ALIASES),
    }

    base_requirements = ["has_statement_title", "has_statement_month", "has_account_id"]
    content_features = [
        "has_account_overview_anchor",
        "has_other_fund_flow_anchor",
        "has_trade_detail_anchor",
        "has_fee_anchor",
        "has_currency_rows",
        "has_holding_section",
    ]
    score = sum(2 if key in base_requirements else 1 for key, value in features.items() if value)
    missing = [key for key in base_requirements if not features[key]]
    content_count = sum(1 for key in content_features if features[key])

    if features["has_statement_title"] and features["has_account_overview_anchor"] and features["has_equity_with_loan_value"]:
        template_id = "overview_with_equity_with_loan"
        recognized = not missing and content_count >= 2
    elif features["has_statement_title"] and features["has_account_overview_anchor"]:
        template_id = "overview_headered"
        recognized = not missing and content_count >= 2
    elif not missing and content_count >= 2 and features["has_page_footer"]:
        template_id = "legacy_inline_overview"
        recognized = True
    else:
        template_id = "unknown_template"
        recognized = False
        if content_count < 2:
            missing.append("at_least_two_content_anchors")
        if not features["has_page_footer"] and not features["has_account_overview_anchor"]:
            missing.append("known_header_or_page_footer")

    return TemplateVersion(
        template_id=template_id,
        recognized=recognized,
        score=score,
        features=features,
        missing_requirements=sorted(set(missing)),
    )
